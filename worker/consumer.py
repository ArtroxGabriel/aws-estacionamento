"""RabbitMQ transport of the OCR Worker.

Owns everything about the queue: the connection, the consumption (push, one
message at a time), the acknowledgement, the retry queue, the dead-letter queue,
the reconnection and the graceful shutdown. What to do with the *content* of a
message is the :class:`processor.MessageProcessor`'s job.

Topology (declared by ``infra/rabbitmq/definitions.json``; the Worker only
checks that it exists, with passive declarations, so the API and the Worker can
never disagree on the queue arguments)::

    API ──publish──► [ocr-processamento-fila] ──► Worker
                            ▲                        │ transient failure
                            │ TTL expires            ▼
                     [ocr-processamento-fila-retry] ◄┘  (header x-attempt = n + 1)
                                                     │ failure on the last attempt
                                                     ▼
                                       [ocr-processamento-fila-dlq]

The attempt counter travels in the ``x-attempt`` header (absent = first
delivery). Every publication waits for the broker's confirmation *before* the
original message is acknowledged; if the process dies between the two, the
message is delivered again and the idempotent processor absorbs the duplicate.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import pika
from pika.exceptions import AMQPConnectionError, AMQPError, ChannelClosedByBroker

from config import Config, redact_url
from processor import MessageProcessor, Outcome

logger = logging.getLogger(__name__)

__all__ = [
    "ATTEMPT_HEADER",
    "MAX_BACKOFF_SECONDS",
    "QueueMissingError",
    "RabbitConsumer",
    "REASON_HEADER",
]

ATTEMPT_HEADER = "x-attempt"
REASON_HEADER = "x-failure-reason"

# The broker drops a connection that misses two heartbeats, and the thread that
# answers them is the one processing the message. The worst case of one message
# (3 S3 attempts + 10 s of OCR + Postgres + DynamoDB) stays well below this.
HEARTBEAT_SECONDS = 120
BLOCKED_CONNECTION_TIMEOUT_SECONDS = 60

# On a connection error the consumer backs off before reconnecting, never giving
# up. The back-off doubles up to this cap.
MAX_BACKOFF_SECONDS = 30
_SLEEP_STEP_SECONDS = 0.5

_PERSISTENT = pika.DeliveryMode.Persistent


class QueueMissingError(Exception):
    """Raised when a queue of the topology does not exist on the broker."""


def _default_connect(cfg: Config) -> Any:
    params = pika.URLParameters(cfg.rabbitmq_url)
    params.heartbeat = HEARTBEAT_SECONDS
    params.blocked_connection_timeout = BLOCKED_CONNECTION_TIMEOUT_SECONDS
    params.client_properties = {"connection_name": "ocr-worker"}
    return pika.BlockingConnection(params)


def attempt_of(properties: Any) -> int:
    """Return the 1-based delivery attempt carried by the message headers.

    A missing, unparseable or non-positive ``x-attempt`` means the first delivery
    (the API publishes without it), the conservative choice that never classifies
    a message as poison on a header alone.
    """

    headers = getattr(properties, "headers", None) or {}
    raw = headers.get(ATTEMPT_HEADER)
    try:
        value = int(raw)
    except TypeError, ValueError:
        return 1
    return value if value >= 1 else 1


class RabbitConsumer:
    """Consumes the OCR queue and applies the processor's verdict to each message."""

    def __init__(
        self,
        cfg: Config,
        processor: MessageProcessor,
        connect: Callable[[], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cfg = cfg
        self._processor = processor
        self._connect = connect if connect is not None else (lambda: _default_connect(cfg))
        self._sleep = sleep

        self._stop = False
        self._connection: Any = None
        self._channel: Any = None
        self._backoff = 1.0

    # --- Run loop ---------------------------------------------------------

    def run(self) -> int:
        """Consume until a stop is requested; return the process exit code.

        A connection or channel error never ends the loop: the consumer backs off
        and reconnects (unacknowledged messages are redelivered by the broker). A
        missing queue ends it with exit code ``2``, because nothing here can
        create the topology.
        """

        exit_code = 0
        try:
            while not self._stop:
                try:
                    self._open()
                    self._backoff = 1.0
                    if self._stop:  # a signal arrived while connecting
                        break
                    logger.info(
                        "worker started; consuming %s (broker=%s)",
                        self._cfg.rabbitmq_queue_name,
                        redact_url(self._cfg.rabbitmq_url),
                    )
                    self._channel.start_consuming()
                    if not self._stop:
                        # Only a stop ends consumption; anything else (e.g. the
                        # broker cancelling the consumer) is a connection loss.
                        raise AMQPConnectionError("consumption ended unexpectedly")
                except QueueMissingError as exc:
                    logger.error("queue topology missing; aborting: %s", exc)
                    exit_code = 2
                    break
                except AMQPError as exc:
                    if self._stop:
                        break
                    backoff = self._backoff
                    logger.error(
                        "RabbitMQ error: dependency=RabbitMQ; reconnecting in %.1fs: %r",
                        backoff,
                        exc,
                    )
                    self._backoff = min(backoff * 2, float(MAX_BACKOFF_SECONDS))
                    self._close_connection()
                    self._wait(backoff)
        finally:
            self._close_connection()
            self._processor.close()
        return exit_code

    def _open(self) -> None:
        """Connect, check the topology and start consuming (one message at a time)."""

        self._connection = self._connect()
        channel = self._connection.channel()
        for queue in (
            self._cfg.rabbitmq_queue_name,
            self._cfg.retry_queue_name,
            self._cfg.dlq_name,
        ):
            try:
                channel.queue_declare(queue=queue, passive=True)
            except ChannelClosedByBroker as exc:
                if exc.reply_code == 404:
                    raise QueueMissingError(queue) from exc
                raise
        channel.confirm_delivery()
        channel.basic_qos(prefetch_count=1)
        channel.basic_consume(
            queue=self._cfg.rabbitmq_queue_name,
            on_message_callback=self._on_message,
        )
        self._channel = channel

    def _wait(self, seconds: float) -> None:
        """Sleep the reconnection back-off in small steps, ending early on a stop."""

        remaining = seconds
        while remaining > 0 and not self._stop:
            step = min(_SLEEP_STEP_SECONDS, remaining)
            self._sleep(step)
            remaining -= step

    # --- One message ------------------------------------------------------

    def _on_message(self, channel: Any, method: Any, properties: Any, body: bytes) -> None:
        """Process one delivery and apply the verdict: acknowledge, retry or give up."""

        if self._stop:
            # Stop requested while this delivery was already on its way: hand it
            # back untouched so another instance picks it up.
            channel.basic_nack(method.delivery_tag, requeue=True)
            channel.stop_consuming()
            return

        attempt = attempt_of(properties)
        result = self._processor.handle(body.decode("utf-8", errors="replace"), attempt)

        if result.outcome is Outcome.RETAIN:
            self._publish(
                channel,
                self._cfg.retry_queue_name,
                body,
                properties,
                {ATTEMPT_HEADER: attempt + 1},
            )
            logger.info(
                "attempt %d failed (%s); retrying in the holding queue", attempt, result.reason
            )
        elif result.outcome is Outcome.POISON:
            self._publish(
                channel,
                self._cfg.dlq_name,
                body,
                properties,
                {ATTEMPT_HEADER: attempt, REASON_HEADER: result.reason or "processing failed"},
            )
            logger.warning("message given up after %d attempts; moved to the DLQ", attempt)

        channel.basic_ack(method.delivery_tag)

        if self._stop:
            channel.stop_consuming()

    def _publish(
        self,
        channel: Any,
        queue: str,
        body: bytes,
        properties: Any,
        extra_headers: dict[str, Any],
    ) -> None:
        """Publish ``body`` to ``queue`` and wait for the broker's confirmation.

        The channel is in confirm mode, so an unroutable or refused message raises
        (an ``AMQPError``): the original is then never acknowledged, the consumer
        reconnects and the broker redelivers it.
        """

        headers = dict(getattr(properties, "headers", None) or {})
        headers.update(extra_headers)
        channel.basic_publish(
            exchange="",
            routing_key=queue,
            body=body,
            properties=pika.BasicProperties(
                delivery_mode=_PERSISTENT,
                content_type=getattr(properties, "content_type", None),
                headers=headers,
            ),
            mandatory=True,
        )

    # --- Shutdown ---------------------------------------------------------

    def request_stop(self, signum: Any = None, frame: Any = None) -> None:
        """Signal handler for SIGTERM/SIGINT that requests a graceful stop.

        Sets the stop flag and asks the connection thread to stop consuming. The
        message being processed is never interrupted: it finishes, is
        acknowledged, and only then does consumption end. Safe to call more than
        once.
        """

        if not self._stop:
            logger.info("stop requested (signal=%s); finishing the current message", signum)
        self._stop = True
        connection = self._connection
        if connection is None:
            return
        try:
            connection.add_callback_threadsafe(self._stop_consuming)
        except Exception as exc:  # noqa: BLE001 - the flag alone ends the loop at the next message
            logger.warning("could not schedule stop on the connection: %r", exc)

    def _stop_consuming(self) -> None:
        channel = self._channel
        if channel is not None and getattr(channel, "is_open", True):
            channel.stop_consuming()

    def _close_connection(self) -> None:
        connection, self._connection, self._channel = self._connection, None, None
        if connection is None:
            return
        try:
            if getattr(connection, "is_open", True):
                connection.close()
        except Exception as exc:  # noqa: BLE001 - closing must never mask the real exit
            logger.error("failed to close the RabbitMQ connection; continuing: %r", exc)
