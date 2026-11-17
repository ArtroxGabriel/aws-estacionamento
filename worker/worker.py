"""Python OCR Worker entrypoint.

Wires configuration, clients, connectors, the :class:`~processor.MessageProcessor`
(business rules) and the :class:`~consumer.RabbitConsumer` (RabbitMQ transport),
then runs the consumer until a stop signal.

Shared contract (from the Go API): the message body is a bare JSON object
``{"session_id", "s3_key"}`` published on the ``ocr-processamento-fila`` queue.

The Worker only *enriches* a session: the car is already in, holding its spot,
and its ticket is valid from the moment the API created the session. All the
Worker does is read the plate from the entry photo and record it
(``plate_status`` ``PENDING`` -> ``READ`` | ``UNREADABLE``). It never changes the
session ``status`` nor the spots counter.
"""

from __future__ import annotations

import logging
import os
import signal

from config import ConfigError, load_config
from consumer import RabbitConsumer
from ocr.clean import normalize as normalize_plate
from ocr.processor import extract_text
from parser import SessionMessage, parse_session_message
from processor import MAX_ATTEMPTS, MessageProcessor, Outcome
from storage.audit import AuditError, AuditLogger
from storage.clients import dynamodb_client, s3_client
from storage.s3_store import S3Connector
from storage.session_repo import SessionRepository

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_ATTEMPTS",
    "MessageProcessor",
    "Outcome",
    "RabbitConsumer",
    "SessionMessage",
    "main",
    "parse_session_message",
]


def main(argv: list[str] | None = None) -> int:
    """Wire configuration, clients, connectors and the consumer, then run it.

    Startup order (fail fast before consuming):
      1. Configure basic logging.
      2. Load + validate configuration from the environment; a
         :class:`ConfigError` lists every missing/invalid variable and returns a
         non-zero exit code.
      3. Build the S3/DynamoDB boto3 clients (each with its own endpoint).
      4. Construct the connectors. ``AuditLogger`` fails construction on an
         absent target; any construction error returns a non-zero exit code.
      5. Construct the processor and the consumer.
      6. Install SIGTERM/SIGINT handlers pointing at ``consumer.request_stop``
         for graceful shutdown.
      7. Run the consumer and return its exit code (``2`` if the queue topology
         is missing).
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(os.environ)
    except ConfigError as exc:
        # ConfigError aggregates every missing/invalid variable by *name only*
        # (never value), so logging it cannot leak a secret.
        logger.error("configuration error; aborting before consuming: %s", exc)
        return 2

    try:
        s3 = s3_client(cfg)
        dynamodb = dynamodb_client(cfg)

        s3_connector = S3Connector(s3, cfg.s3_bucket_name)
        sessions = SessionRepository(cfg.database_url)
        audit = AuditLogger(dynamodb, cfg.dynamodb_table_name)
    except AuditError as exc:
        logger.error("connector initialization failed; aborting: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 - any startup failure must fail fast
        logger.error("startup failed while building clients/connectors: %s", exc)
        return 2

    processor = MessageProcessor(
        s3=s3_connector,
        sessions=sessions,
        audit=audit,
        ocr=extract_text,
        normalizer=normalize_plate,
    )
    consumer = RabbitConsumer(cfg, processor)

    signal.signal(signal.SIGTERM, consumer.request_stop)
    signal.signal(signal.SIGINT, consumer.request_stop)

    return consumer.run()


if __name__ == "__main__":
    raise SystemExit(main())
