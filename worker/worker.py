"""Python OCR Worker entrypoint and Poller.

Holds the pure ``Session_Message`` parsing/validation layer, the Poller (SQS
long-polling loop, idempotency branch, transactional side effects, poison
classification, graceful shutdown) and the ``main`` startup wiring.

The Worker only *enriches* a session: the car is already in, holding its spot,
and its ticket is valid from the moment the API created the session. All the
Worker does is read the plate from the entry photo and record it
(``plate_status`` ``PENDING`` -> ``READ`` | ``UNREADABLE``). It never changes
the session ``status`` nor the spots counter.

Shared contract (from the Go API, see design.md "SQS Session_Message"):
the SQS message body is a bare JSON object ``{"session_id", "s3_key"}`` with
no SNS envelope.
"""

from __future__ import annotations

import enum
import logging
import os
import signal
import time
from typing import Any

from config import Config, ConfigError, load_config
from ocr.clean import PlateResult
from ocr.clean import normalize as normalize_plate
from ocr.processor import OcrResult, extract_text
from ocr.rekognition import HybridOcr
from parser import (
    SessionMessage,
    parse_session_message,
)
from storage.audit import AuditError, AuditLogger
from storage.clients import dynamodb_client, rekognition_client, s3_client, sqs_client
from storage.s3_store import RetrievalError, S3Connector
from storage.session_repo import PLATE_PENDING, SessionRepository

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_RECEIVE_COUNT",
    "Outcome",
    "Poller",
    "SessionMessage",
    "main",
    "parse_session_message",
]

# Must equal the ``maxReceiveCount`` of the redrive policy on
# ``ocr-processamento-fila`` (infra/main.tf). SQS moves the message to the DLQ
# right after its MAX_RECEIVE_COUNT-th receive, so a message that fails on that
# delivery is classified as a Poison_Message and audited then (Req 12.1-12.3).
MAX_RECEIVE_COUNT = 3

# --- Loop / shutdown tuning ------------------------------------------------
#
# SQS long-polling parameters (Req 1.1): each ReceiveMessage waits up to 20 s
# for messages and pulls at most 10 at a time.
RECEIVE_WAIT_SECONDS = 20
MAX_MESSAGES = 10

# On an SQS connection/authorization error the loop backs off before retrying,
# never terminating (Req 1.5). The back-off is capped at 30 s.
MAX_RECEIVE_BACKOFF_SECONDS = 30

# The queue visibility timeout the Worker expects while a message is in flight
# (Req 9.4). This is an infra-configured value the Worker documents rather than
# sets; it bounds how long a retained/abandoned message stays invisible before
# redelivery.
VISIBILITY_TIMEOUT_SECONDS = 300


class _Interrupted(BaseException):
    """Raised by the stop signal handler to cut a blocking wait short.

    Only raised while the loop sleeps in the receive back-off, where no request
    is in flight. Never during an SQS long poll (an abandoned poll can still
    take a message on the server side) nor while a message is processed. It
    derives from BaseException so no ``except Exception`` can swallow it.
    """


# --- Poller ---------------------------------------------------------------
#
# The Poller owns SQS orchestration: the long-polling loop, the idempotency
# branch, the ordered side effects, message deletion, poison classification,
# and graceful shutdown.
#
# The Worker's whole state machine is ``plate_status``: it only ever moves a
# session from ``PENDING`` to ``READ`` or ``UNREADABLE``. Anything else
# (``READ``/``UNREADABLE`` already recorded, ``MANUAL`` typed by the cashier)
# makes the message terminal/idempotent. The session ``status``
# (``PARKED``/``PAID``) belongs to the API and is never read as a gate here: a
# car that already paid still gets its plate recorded as evidence.


class Outcome(enum.Enum):
    """How the Poller should treat a message after ``_handle`` returns.

    The mapping to the actual SQS operation is applied by the loop (task 15.1):
      - ``DELETE`` — the plate was recorded, the photo was recorded as
        unreadable, or the message is terminal/idempotent (``plate_status`` is
        no longer ``PENDING``, or the session does not exist):
        ``DeleteMessage`` (Req 1.4, 9.1, 11.1, 11.2, 11.3).
      - ``RETAIN`` — a transient failure or a downstream connection error:
        leave the message for redelivery, never delete (Req 9.2, 10.1, 11.4,
        13.1, 13.3). This is the fail-safe default.
      - ``POISON`` — the message exceeded the configured receive count or is
        malformed: rely on the SQS redrive policy to move it (task 14.1).
    """

    DELETE = "delete"
    RETAIN = "retain"
    POISON = "poison"


class Poller:
    """Consumes ``Session_Message`` events and records the plate of each photo.

    Wires together the pure OCR layer (``ocr/``) and the I/O connectors
    (``storage/``) supplied at construction. All collaborators are injected so
    the orchestration logic can be exercised with in-memory fakes.

    The ``_handle`` method drives one message end-to-end through the fixed
    pipeline: parse/validate -> session lookup -> idempotency branch -> S3
    download -> OCR -> normalize -> side effects (RDS conditional UPDATE ->
    DynamoDB PutItem) -> report ``DELETE`` so the loop performs the SQS
    ``DeleteMessage`` last.

    The guiding rule is **fail safe toward RETAIN**: any pipeline error, lookup
    failure or downstream connection error leaves the message in the queue for
    redelivery. Nothing here can block a car: whatever happens to the photo,
    the session is already ``PARKED`` and its ticket pays.
    """

    def __init__(
        self,
        cfg: Config,
        sqs: Any,
        s3: S3Connector,
        sessions: SessionRepository,
        audit: AuditLogger,
        ocr: Any = None,
        normalizer: Any = None,
    ) -> None:
        """Store configuration and the injected connectors/pure functions.

        ``sqs`` is the low-level boto3 SQS client (the loop uses it for
        ``ReceiveMessage``/``DeleteMessage``); ``s3``/``sessions``/``audit``
        are the ``storage/`` connectors. ``ocr`` and ``normalizer`` default to
        the pure ``ocr/`` functions (``extract_text`` and ``normalize``) but
        may be overridden with fakes in tests.
        """

        self._cfg = cfg
        self._sqs = sqs
        self._s3 = s3
        self._sessions = sessions
        self._audit = audit
        # Default to the pure OCR-layer functions; tests may inject fakes.
        self._ocr = ocr if ocr is not None else extract_text
        self._normalizer = normalizer if normalizer is not None else normalize_plate

        # --- Loop / shutdown state ---------------------------------------
        # ``request_stop`` sets this flag from a signal handler; the loop reads
        # it before every receive and before every message (Req 16.1).
        self._stop = False
        # True while the loop sits in a wait that the stop signal may cut short
        # (the SQS long poll and the receive back-off).
        self._interruptible = False
        # Consecutive receive back-off, doubled on each connection/auth error
        # and capped at MAX_RECEIVE_BACKOFF_SECONDS (Req 1.5).
        self._receive_backoff = 1.0

    def _handle(self, msg: dict[str, Any]) -> Outcome:
        """Process a single SQS message end-to-end and return its outcome.

        ``msg`` is a boto3 SQS message dict; ``Body`` carries the payload and
        ``Attributes.ApproximateReceiveCount`` drives poison classification
        (the ``ReceiptHandle`` is used by the loop to delete). Domain outcomes
        are returned as :class:`Outcome` values; this method never raises for
        an expected failure — connector exceptions are caught and mapped to
        ``RETAIN`` per the design taxonomy.

        Poison classification (Req 10.4, 12.1-12.3): the SQS redrive policy
        moves a message to the DLQ right after its ``MAX_RECEIVE_COUNT``-th
        receive, so that delivery is the Worker's last chance to see it. A
        message that fails on that delivery is classified ``POISON`` and its
        failure reason is audited once. A message seen *beyond* the threshold
        means the redrive is missing or lagging: it was already audited, so it
        is neither reprocessed nor re-audited.

        Giving up on a message also closes the plate reading: the session goes
        ``PENDING`` -> ``UNREADABLE`` whatever the reason was (bad photo or an
        outage), so nothing shows "reading the plate" forever. The cashier can
        still type the plate, and the ticket works either way.
        """

        body = msg.get("Body", "")
        receive_count = self._receive_count(msg)

        if receive_count > MAX_RECEIVE_COUNT:
            logger.warning(
                "message received %d times (max %d); poison already recorded, "
                "leaving it for the SQS redrive policy",
                receive_count,
                MAX_RECEIVE_COUNT,
            )
            return Outcome.POISON

        outcome, reason = self._process(body)

        if outcome is Outcome.RETAIN and receive_count >= MAX_RECEIVE_COUNT:
            parsed = parse_session_message(body)
            session_id = parsed.message.session_id if parsed.message else "unknown"
            logger.warning(
                "poison message (session=%s) after %d deliveries: %s",
                session_id,
                receive_count,
                reason,
            )
            self._log_poison_best_effort(session_id, reason or "processing failed")
            if parsed.message is not None:
                self._mark_unreadable_best_effort(session_id, reason or "processing failed")
            return Outcome.POISON

        return outcome

    def _process(self, body: str) -> tuple[Outcome, str | None]:
        """Drive one message body through the pipeline.

        Returns the outcome plus, for ``RETAIN``, the failure reason that is
        audited if this turns out to be the message's last delivery.
        """

        # 1. Parse + validate the Session_Message (Req 2.x). A malformed body is
        # left in the queue; its poison reason is audited on the last delivery.
        parsed = parse_session_message(body)
        if not parsed.ok or parsed.message is None:
            reason = parsed.reason or "invalid message"
            logger.warning("invalid message: %s", reason)
            return Outcome.RETAIN, reason

        message = parsed.message
        session_id = message.session_id

        # 2. Look up the session by id. A lookup failure (RDS unreachable) must
        # NOT delete the message — leave it for redelivery (Req 11.4, 13.1).
        try:
            row = self._sessions.get(session_id)
        except Exception as exc:  # noqa: BLE001 - map any lookup failure to RETAIN
            logger.error(
                "session lookup failed for %s: dependency=RDS: %s",
                session_id,
                exc,
            )
            return Outcome.RETAIN, "session lookup failed (RDS)"

        # 3. Idempotency branch (Req 11.1, 11.2, 11.3). A missing session (the
        # cashier deleted it) or a plate that is no longer PENDING (already
        # read, already given up on, or typed by the cashier) is terminal:
        # delete without side effects. The session status is not looked at.
        if row is None:
            logger.info("session %s not found; deleting message", session_id)
            return Outcome.DELETE, None
        if row.plate_status != PLATE_PENDING:
            logger.info(
                "session %s plate already %s; deleting message (idempotent)",
                session_id,
                row.plate_status,
            )
            return Outcome.DELETE, None

        # 4. Download the photo from S3 (Req 3.x). Any retrieval error retains
        # the message (fail safe toward redelivery).
        try:
            image_bytes = self._s3.download(message.s3_key)
        except RetrievalError as exc:
            logger.error(
                "S3 download failed for %s (key=%s): kind=%s dependency=S3",
                session_id,
                message.s3_key,
                exc.kind,
            )
            return Outcome.RETAIN, f"S3 download failed: {exc.kind}"
        except Exception as exc:  # noqa: BLE001 - unexpected S3 failure -> RETAIN
            logger.error(
                "S3 download error for %s (key=%s): dependency=S3: %s",
                session_id,
                message.s3_key,
                exc,
            )
            return Outcome.RETAIN, "S3 download failed"

        # 5. OCR extraction (Req 4.x). Domain failures come back as an
        # OcrResult with ok=False; retain and log the step.
        ocr_result: OcrResult = self._ocr(image_bytes)
        if not ocr_result.ok or ocr_result.raw_text is None:
            logger.warning(
                "OCR failed for %s: error=%s",
                session_id,
                ocr_result.error,
            )
            return Outcome.RETAIN, f"OCR failed: {ocr_result.error}"

        # 6. Normalize the raw text into a canonical plate (Req 5.x). OCR is
        # deterministic, so an unreadable plate would fail again on every
        # redelivery: the photo is recorded as UNREADABLE at once and the
        # message is deleted. The session is untouched (still PARKED, ticket
        # valid); the cashier may type the plate if they want to. Only a failed
        # RDS update retains.
        plate_result: PlateResult = self._normalizer(
            ocr_result.raw_text, mercosul=ocr_result.mercosul
        )
        if not plate_result.ok or plate_result.plate is None:
            reason = f"unreadable plate: {plate_result.reason}"
            logger.warning("%s for %s; recording plate as UNREADABLE", reason, session_id)
            try:
                marked = self._sessions.mark_unreadable(session_id)
            except Exception as exc:  # noqa: BLE001 - keep the message for a retry
                logger.error(
                    "failed to mark the plate of %s as UNREADABLE: dependency=RDS: %s",
                    session_id,
                    exc,
                )
                return Outcome.RETAIN, "session update failed (RDS)"
            if marked:
                self._log_failed_best_effort(session_id, reason)
            return Outcome.DELETE, None

        # 7. Side effects. The plate is readable and still PENDING, so record
        # it and audit the reading.
        return self._apply_side_effects(session_id, plate_result.plate)

    def _receive_count(self, msg: dict[str, Any]) -> int:
        """Return the message's SQS ``ApproximateReceiveCount`` (Req 12.2).

        The value lives in the SQS system attribute map under
        ``msg["Attributes"]["ApproximateReceiveCount"]`` (a string). It is only
        present when the ``ReceiveMessage`` call requested that attribute, so a
        missing or unparseable value defaults to ``1`` (first delivery) — the
        conservative choice that never spuriously classifies a message as
        poison on count alone.
        """

        attributes = msg.get("Attributes") or {}
        raw = attributes.get("ApproximateReceiveCount")
        if raw is None:
            return 1
        try:
            count = int(raw)
        except TypeError, ValueError:
            return 1
        # Guard against a nonsensical non-positive value from the attribute.
        return count if count >= 1 else 1

    def _apply_side_effects(self, session_id: str, plate: str) -> tuple[Outcome, str | None]:
        """Apply RDS -> DynamoDB atomically; the loop deletes SQS last.

        The RDS conditional ``UPDATE`` is the idempotency gate (Req 6.1, 6.6,
        11.5), and its transaction stays open while the audit entry is written:

          1. ``UPDATE ... SET plate_status='READ' WHERE plate_status='PENDING'``
             inside a transaction. Zero rows updated means another delivery
             already recorded a result, or the cashier typed the plate in the
             meantime, so nothing else runs and the message is deleted.
          2. DynamoDB ``PutItem`` with ``action=OCR_PROCESSING`` (Req 8.1).
          3. ``COMMIT``.

        If any step fails, the transaction rolls back so the row stays
        ``PENDING`` and the message is retained (Req 9.2, 13.3): the redelivery
        re-runs the whole pipeline instead of finding a ``READ`` row that was
        never audited. The only effect that cannot be undone is an audit entry
        written right before a failed COMMIT; that leaves an extra
        ``OCR_PROCESSING`` entry, never a missing one.

        The spots counter is not involved: the API took the spot when the car
        came in.
        """

        step = "RDS"
        try:
            with self._sessions.plate_transition(session_id, plate) as transitioned:
                if not transitioned:
                    logger.info(
                        "session %s plate no longer PENDING at update time; "
                        "skipping side effects, deleting message",
                        session_id,
                    )
                    return Outcome.DELETE, None

                step = "DynamoDB"
                self._audit.log_ocr(session_id, plate)

                step = "RDS commit"
        except Exception as exc:  # noqa: BLE001 - any failure rolls back -> RETAIN
            logger.error(
                "side effects failed for %s at step=%s; RDS rolled back: %s",
                session_id,
                step,
                exc,
            )
            return Outcome.RETAIN, f"{step} failed"

        logger.info("session %s plate read: %s", session_id, plate)
        return Outcome.DELETE, None

    def _log_poison_best_effort(self, session_id: str, reason: str) -> None:
        """Record a poison classification reason without blocking (Req 12.1).

        ``AuditLogger`` already retries the write up to 3 times; a final failure
        is logged and swallowed so the Poller keeps consuming (Req 12.5).
        """

        try:
            self._audit.log_poison(session_id, reason)
        except Exception as exc:  # noqa: BLE001 - poison audit is best-effort
            logger.error(
                "failed to record poison reason for %s: %s",
                session_id,
                exc,
            )

    def _mark_unreadable_best_effort(self, session_id: str, reason: str = "") -> None:
        """Close a plate reading that will not be retried and audit it (best-effort)."""
        try:
            if self._sessions.mark_unreadable(session_id):
                self._log_failed_best_effort(session_id, reason)
        except Exception as exc:  # noqa: BLE001 - best-effort status update
            logger.error(
                "failed to mark the plate of session %s as UNREADABLE in RDS: %s",
                session_id,
                exc,
            )

    def _log_failed_best_effort(self, session_id: str, reason: str) -> None:
        """Record an OCR_FAILED audit entry without blocking."""
        try:
            self._audit.log_failed(session_id, reason)
        except Exception as exc:  # noqa: BLE001 - best-effort audit
            logger.error(
                "failed to record failed audit entry for %s: %s",
                session_id,
                exc,
            )

    # --- Loop, receive, signals, and shutdown ----------------------------

    def run(self) -> int:
        """Run the SQS long-polling loop until a stop is requested (Req 1.x, 16.x).

        The loop repeatedly calls :meth:`_receive` and processes each returned
        message sequentially through :meth:`_handle`, mapping the resulting
        :class:`Outcome` to an SQS action: ``DELETE`` performs the
        ``DeleteMessage``; ``RETAIN``/``POISON`` leave the message in the queue
        for redelivery or SQS redrive respectively (Req 1.3, 1.4, 9.2, 12.3).

        Resilience is the priority: any per-message exception is caught, logged
        with the failed step, and the loop continues to the next message within
        1 s (Req 12.4, 13.4). An empty receive simply re-polls (Req 1.2), and a
        receive connection/authorization error is absorbed inside
        :meth:`_receive` (Req 1.5), so this loop never terminates on transient
        failure — only on a stop signal.

        On stop (Req 16.1-16.4): a receive back-off is cut short at once; a long
        poll in progress runs to its end (at most 20 s) and everything it
        returns is released; a message being processed runs to completion
        (every step has its own timeout, and its RDS transaction makes an
        external kill safe); messages of the batch not yet started are released
        back to the queue with visibility 0 so another instance picks them up
        immediately instead of after the 300 s visibility timeout. Then
        connections are closed and the exit code is ``0``.
        """

        try:
            while not self._stop:
                messages = self._receive()
                for index, msg in enumerate(messages):
                    if self._stop:
                        self._release(messages[index:])
                        break
                    self._process_one(msg)
        finally:
            self.close()

        return 0

    def _process_one(self, msg: dict[str, Any]) -> None:
        """Handle one message and apply its SQS action, never raising.

        Any exception from :meth:`_handle` or the delete is caught and logged so
        the loop continues within 1 s (Req 12.4, 13.4). A message is deleted
        only on :attr:`Outcome.DELETE`; ``RETAIN`` and ``POISON`` leave it in
        the queue (Req 9.2, 12.3).
        """

        try:
            outcome = self._handle(msg)
        except Exception as exc:  # noqa: BLE001 - loop must survive any message
            logger.error(
                "unhandled error processing message; leaving in queue: "
                "step=handle dependency=worker: %s",
                exc,
            )
            return

        if outcome is Outcome.DELETE:
            self._delete(msg)
        # RETAIN / POISON: leave the message in the queue (redelivery / redrive).

    def _receive(self) -> list[dict[str, Any]]:
        """Long-poll the SQS queue and return the received messages (Req 1.1).

        Requests up to :data:`MAX_MESSAGES` messages with a
        :data:`RECEIVE_WAIT_SECONDS`-second wait and asks for the
        ``ApproximateReceiveCount`` system attribute so :meth:`_receive_count`
        can classify poison messages. An empty receive returns an empty list and
        the caller re-polls immediately (Req 1.2).

        On an SQS connection or authorization error this backs off up to
        :data:`MAX_RECEIVE_BACKOFF_SECONDS` and returns an empty list rather
        than raising, so the loop keeps running without terminating (Req 1.5).
        """

        # A stop requested between receives: do not start a new long poll.
        if self._stop:
            return []

        try:
            try:
                # Never cut short: a long poll abandoned client-side stays open
                # on the SQS side and can still take a message, which then
                # stays invisible for the whole visibility timeout (300 s).
                # A stop during the poll is handled by the loop releasing
                # whatever the poll returns.
                response = self._sqs.receive_message(
                    QueueUrl=self._cfg.sqs_queue_url,
                    WaitTimeSeconds=RECEIVE_WAIT_SECONDS,
                    MaxNumberOfMessages=MAX_MESSAGES,
                    AttributeNames=["ApproximateReceiveCount"],
                )
            except Exception as exc:  # noqa: BLE001 - connection/auth error -> back off
                backoff = self._receive_backoff
                logger.error(
                    "SQS receive failed: dependency=SQS; backing off %.1fs: %s",
                    backoff,
                    exc,
                )
                # Exponential back-off, capped so the loop retries within 30 s.
                self._receive_backoff = min(backoff * 2, float(MAX_RECEIVE_BACKOFF_SECONDS))
                self._interruptibly(self._sleep, backoff)
                return []
        except _Interrupted:
            return []  # back-off cut short by a stop signal

        # Successful receive: reset the back-off window.
        self._receive_backoff = 1.0
        messages = response.get("Messages") or []
        return messages

    def _delete(self, msg: dict[str, Any]) -> None:
        """Delete a fully-processed message from the SQS queue (Req 1.4, 9.1).

        Uses the message ``ReceiptHandle`` against the configured queue URL. A
        delete failure is logged but not raised: the message will simply be
        redelivered and short-circuit at the idempotency branch (already
        ``PARKED``), so the loop continues (Req 13.4).
        """

        receipt_handle = msg.get("ReceiptHandle")
        if not receipt_handle:
            logger.error("cannot delete message: missing ReceiptHandle")
            return
        try:
            self._sqs.delete_message(
                QueueUrl=self._cfg.sqs_queue_url,
                ReceiptHandle=receipt_handle,
            )
        except Exception as exc:  # noqa: BLE001 - delete failure -> redelivery
            logger.error(
                "SQS delete failed; message will be redelivered: dependency=SQS: %s",
                exc,
            )

    def _release(self, messages: list[dict[str, Any]]) -> None:
        """Make unprocessed messages visible again right away (best-effort).

        On failure they simply reappear after the visibility timeout.
        """

        entries = [
            {"Id": str(i), "ReceiptHandle": msg["ReceiptHandle"], "VisibilityTimeout": 0}
            for i, msg in enumerate(messages)
            if msg.get("ReceiptHandle")
        ]
        if not entries:
            return
        logger.info("stopping; releasing %d unprocessed message(s) to the queue", len(entries))
        try:
            self._sqs.change_message_visibility_batch(
                QueueUrl=self._cfg.sqs_queue_url, Entries=entries
            )
        except Exception as exc:  # noqa: BLE001 - they come back after the timeout
            logger.error(
                "failed to release messages; they return after the visibility "
                "timeout: dependency=SQS: %s",
                exc,
            )

    def _interruptibly(self, call: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking wait that a stop signal may cut short (Req 16.1)."""

        self._interruptible = True
        try:
            if self._stop:
                raise _Interrupted
            return call(*args, **kwargs)
        finally:
            self._interruptible = False

    def request_stop(self, signum: Any = None, frame: Any = None) -> None:
        """Signal handler for SIGTERM/SIGINT that requests a graceful stop.

        Sets the stop flag, which the loop checks before every receive and
        every message (Req 16.1). If the loop is sleeping in the receive
        back-off, raises :class:`_Interrupted` so it ends now instead of after
        up to 30 s. Neither the SQS long poll nor a message being processed is
        interrupted (Req 16.2). Safe to call more than once.
        """

        if not self._stop:
            logger.info("stop requested (signal=%s); draining", signum)
        self._stop = True
        if self._interruptible:
            self._interruptible = False
            raise _Interrupted

    def close(self) -> None:
        """Close RDS/AWS connections, surviving individual failures.

        Each connector is closed independently; a failure closing one is logged
        with the failed dependency and the routine proceeds to the next, so no
        single close error prevents releasing the others (Req 16.5). Connectors
        without a ``close`` are skipped. This runs exactly once at loop exit.
        """

        # (connector, dependency-label) pairs; only those exposing close() are
        # invoked.
        for connector, dependency in (
            (self._sessions, "RDS"),
            (self._sqs, "SQS"),
            (self._s3, "S3"),
            (self._audit, "DynamoDB"),
        ):
            close = getattr(connector, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - keep closing the rest
                logger.error(
                    "failed to close %s connection; continuing: %s",
                    dependency,
                    exc,
                )

    def _sleep(self, seconds: float) -> None:
        """Sleep for the receive back-off; overridable in tests."""

        time.sleep(seconds)


# --- Entrypoint -----------------------------------------------------------
#
# ``main`` is the Config_Loader -> client factory -> connector -> Poller wiring
# from design.md ("Startup / entrypoint"). It fails fast with a non-zero exit
# code before the polling loop begins when configuration is missing/invalid
# (Req 1.7, 14.2) or a connector cannot be constructed (Req 6.2, 7.3, 8.6), and
# otherwise returns the Poller's own exit code (Req 16.4).


def main(argv: list[str] | None = None) -> int:
    """Wire configuration, clients, connectors and the Poller, then run it.

    Startup order (fail fast before the loop, Req 1.7):
      1. Configure basic logging.
      2. Load + validate configuration from the environment; a
         :class:`ConfigError` lists every missing/invalid variable and returns a
         non-zero exit code without starting the loop (Req 14.2, 16.4).
      3. Build the S3/SQS/DynamoDB boto3 clients through the shared factory so
         every client resolves the same endpoint (Req 15.1, 15.5).
      4. Construct the connectors. ``AuditLogger`` fails construction on an
         absent target (Req 8.6); any construction error is caught and returns
         a non-zero exit code before the loop.
      5. Construct the Poller, injecting cfg + the SQS client + connectors +
         the pure OCR/normalizer functions.
      6. Install SIGTERM/SIGINT handlers pointing at ``poller.request_stop`` for
         graceful shutdown (Req 16.1).
      7. Run the loop and return its exit code (0 iff no in-flight message was
         abandoned, non-zero otherwise; Req 16.4).

    Returns:
        The process exit code: non-zero on any startup failure, otherwise the
        value returned by :meth:`Poller.run`.
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Step 2: load + validate configuration (fail fast, Req 1.7, 14.2).
    try:
        cfg = load_config(os.environ)
    except ConfigError as exc:
        # ConfigError already aggregates every missing/invalid variable by
        # *name only* (never value), so logging it cannot leak a secret; the
        # redact() marker is available for any place that must echo a value.
        logger.error("configuration error; aborting before the loop: %s", exc)
        return 2

    # Step 3: build the AWS clients through the shared factory (Req 15.1, 15.5).
    # Step 4: construct the connectors. AuditLogger raises on an absent target
    # (Req 8.6); catch any construction failure and fail fast rather than
    # entering the loop half-wired (Req 1.7).
    try:
        s3 = s3_client(cfg)
        sqs = sqs_client(cfg)
        dynamodb = dynamodb_client(cfg)

        s3_connector = S3Connector(s3, cfg.s3_bucket_name)
        sessions = SessionRepository(cfg.database_url)
        audit = AuditLogger(dynamodb, cfg.dynamodb_table_name)
        # Rekognition (AWS only) reads real photos far better than Tesseract
        # and falls back to it; the emulator has no Rekognition.
        ocr = extract_text
        if cfg.ocr_engine == "rekognition":
            ocr = HybridOcr(rekognition_client(cfg))
        logger.info("OCR engine: %s", cfg.ocr_engine)
    except AuditError as exc:
        logger.error("connector initialization failed; aborting: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 - any startup failure must fail fast
        logger.error("startup failed while building clients/connectors: %s", exc)
        return 2

    # Step 5: construct the Poller with all collaborators injected. The pure
    # OCR-layer functions (extract_text/normalize_plate) are passed explicitly
    # so no component from tasks 2-15 is left orphaned.
    poller = Poller(
        cfg=cfg,
        sqs=sqs,
        s3=s3_connector,
        sessions=sessions,
        audit=audit,
        ocr=ocr,
        normalizer=normalize_plate,
    )

    # Step 6: install graceful-shutdown signal handlers (Req 16.1).
    signal.signal(signal.SIGTERM, poller.request_stop)
    signal.signal(signal.SIGINT, poller.request_stop)

    # Step 7: run the loop and return its exit code (Req 16.4).
    logger.info(
        "worker started; polling %s (endpoint=%s)",
        cfg.sqs_queue_url,
        cfg.aws_endpoint_url or "default AWS",
    )
    return poller.run()


if __name__ == "__main__":
    raise SystemExit(main())
