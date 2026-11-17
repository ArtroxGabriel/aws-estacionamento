"""In-memory fakes for the processor's connectors and for RabbitMQ.

They model only the behavior the Worker relies on: the RDS plate transition with
commit/rollback semantics, the DynamoDB audit trail, and the pika channel/
connection used by the consumer. Each fake can be told to fail the next N calls
so tests can inject failures at any step.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from pika.exceptions import ChannelClosedByBroker, UnroutableError

from config import Config, load_config
from consumer import RabbitConsumer
from ocr.clean import PlateResult
from ocr.processor import OcrResult
from processor import MessageProcessor
from storage.audit import AuditWriteError
from storage.session_repo import SessionRow

SESSION_ID = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
S3_KEY = f"photos/{SESSION_ID}_frente.jpg"


class CommitError(Exception):
    """Simulates the RDS COMMIT failing after the body ran."""


class FakeSessionRepository:
    def __init__(self, rows: dict[str, SessionRow] | None = None) -> None:
        self.rows: dict[str, SessionRow] = dict(rows or {})
        self.fail_get = 0
        self.fail_commit = 0

    def get(self, session_id: str) -> SessionRow | None:
        if self.fail_get:
            self.fail_get -= 1
            raise ConnectionError("RDS unreachable")
        row = self.rows.get(session_id)
        return replace(row) if row is not None else None

    @contextmanager
    def plate_transition(self, session_id: str, plate: str) -> Iterator[bool]:
        row = self.rows.get(session_id)
        if row is None or row.plate_status != "PENDING":
            yield False
            return
        snapshot = replace(row)
        self.rows[session_id] = replace(row, plate_status="READ", license_plate=plate)
        try:
            yield True
        except BaseException:
            self.rows[session_id] = snapshot  # ROLLBACK
            raise
        if self.fail_commit:
            self.fail_commit -= 1
            self.rows[session_id] = snapshot
            raise CommitError("COMMIT failed")

    def mark_unreadable(self, session_id: str) -> bool:
        row = self.rows.get(session_id)
        if row is not None and row.plate_status == "PENDING":
            self.rows[session_id] = replace(row, plate_status="UNREADABLE")
            return True
        return False


class FakeAuditLogger:
    def __init__(self) -> None:
        self.ocr_entries: list[tuple[str, str]] = []
        self.poison_entries: list[tuple[str, str]] = []
        self.failed_entries: list[tuple[str, str]] = []
        self.fail_ocr = 0

    def log_ocr(self, session_id: str, plate: str) -> None:
        if self.fail_ocr:
            self.fail_ocr -= 1
            raise AuditWriteError(session_id, 3)
        self.ocr_entries.append((session_id, plate))

    def log_poison(self, session_id: str, reason: str) -> None:
        self.poison_entries.append((session_id, reason))

    def log_failed(self, session_id: str, reason: str) -> None:
        self.failed_entries.append((session_id, reason))


class FakeS3:
    def download(self, key: str) -> bytes:
        return b"image-bytes"


class FakeChannel:
    """The slice of a pika ``BlockingChannel`` the consumer uses.

    ``start_consuming`` delivers the queued messages to the registered callback,
    one at a time, like ``prefetch_count=1``, until ``stop_consuming`` is called
    or the backlog runs out (then it blocks forever in real life; here it returns
    so tests finish).
    """

    def __init__(self, existing_queues: set[str] | None = None) -> None:
        self.existing_queues = existing_queues
        self.backlog: list[tuple[bytes, dict | None]] = []
        self.published: list[dict] = []
        self.acked: list[int] = []
        self.nacked: list[tuple[int, bool]] = []
        self.qos: int | None = None
        self.confirm = False
        self.consuming = False
        self.is_open = True
        self.callback = None
        self.queue = None
        self.fail_publish = 0
        self.on_deliver = None  # runs right before each delivery (e.g. a stop signal)
        self.on_idle = None  # runs when the backlog is empty (real life: blocks until a stop)
        self._tag = 0

    def queue_declare(self, queue: str, passive: bool = False, **_kw) -> None:
        if self.existing_queues is not None and queue not in self.existing_queues:
            raise ChannelClosedByBroker(404, f"NOT_FOUND - no queue '{queue}'")

    def confirm_delivery(self) -> None:
        self.confirm = True

    def basic_qos(self, prefetch_count: int) -> None:
        self.qos = prefetch_count

    def basic_consume(self, queue: str, on_message_callback) -> None:
        self.queue = queue
        self.callback = on_message_callback

    def basic_ack(self, delivery_tag: int) -> None:
        self.acked.append(delivery_tag)

    def basic_nack(self, delivery_tag: int, requeue: bool = True) -> None:
        self.nacked.append((delivery_tag, requeue))

    def basic_publish(self, exchange, routing_key, body, properties, mandatory=False) -> None:
        if self.fail_publish:
            self.fail_publish -= 1
            raise UnroutableError([])
        self.published.append(
            {
                "queue": routing_key,
                "body": body,
                "headers": dict(properties.headers or {}),
                "delivery_mode": properties.delivery_mode,
                "mandatory": mandatory,
            }
        )

    def start_consuming(self) -> None:
        self.consuming = True
        while self.consuming and self.backlog:
            if self.on_deliver is not None:
                self.on_deliver()
            body, headers = self.backlog.pop(0)
            self._tag += 1
            method = SimpleNamespace(delivery_tag=self._tag)
            props = SimpleNamespace(headers=headers, content_type="application/json")
            self.callback(self, method, props, body)
        if self.consuming and self.on_idle is not None:
            self.on_idle()
        self.consuming = False

    def stop_consuming(self) -> None:
        self.consuming = False


class FakeConnection:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel
        self.is_open = True
        self.callbacks: list = []

    def channel(self) -> FakeChannel:
        return self._channel

    def add_callback_threadsafe(self, callback) -> None:
        self.callbacks.append(callback)
        callback()  # the real one runs it on the connection thread; same effect here

    def close(self) -> None:
        self.is_open = False


BASE_ENV = {
    "AWS_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "S3_ENDPOINT_URL": "http://localhost:9000",
    "S3_BUCKET_NAME": "bucket",
    "DYNAMODB_ENDPOINT_URL": "http://localhost:8000",
    "DYNAMODB_TABLE_NAME": "AuditoriaEstacionamento",
    "RABBITMQ_URL": "amqp://guest:guest@localhost:5672/",
    "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
}


def make_config(**overrides: str) -> Config:
    return load_config({**BASE_ENV, **overrides})


def make_body(session_id: str = SESSION_ID, s3_key: str = S3_KEY) -> str:
    return f'{{"session_id": "{session_id}", "s3_key": "{s3_key}"}}'


def pending_row(session_id: str = SESSION_ID, status: str = "PARKED") -> SessionRow:
    """A session as the API creates it: in the lot, plate still to be read."""
    return SessionRow(
        id=session_id,
        license_plate=None,
        status=status,
        s3_photo_key=S3_KEY,
        plate_status="PENDING",
    )


def make_processor(
    sessions: FakeSessionRepository | None = None,
    audit: FakeAuditLogger | None = None,
    plate: str | None = "ABC1D23",
    s3: Any = None,
    ocr: Any = None,
) -> tuple[MessageProcessor, FakeSessionRepository, FakeAuditLogger]:
    sessions = sessions or FakeSessionRepository({SESSION_ID: pending_row()})
    audit = audit or FakeAuditLogger()
    s3 = s3 or FakeS3()

    def fake_ocr(_image: bytes) -> OcrResult:
        return OcrResult(ok=True, raw_text=plate or "???", error=None)

    def fake_normalizer(_raw: str, **_kwargs) -> PlateResult:
        if plate is None:
            return PlateResult(ok=False, plate=None, reason="no_match")
        return PlateResult(ok=True, plate=plate, reason=None)

    processor = MessageProcessor(
        s3=s3,
        sessions=sessions,
        audit=audit,
        ocr=ocr or fake_ocr,
        normalizer=fake_normalizer,
    )
    return processor, sessions, audit


def make_consumer(
    processor: MessageProcessor,
    channel: FakeChannel | None = None,
    connect=None,
) -> tuple[RabbitConsumer, FakeChannel, list[float]]:
    """A consumer wired to a fake broker; ``sleeps`` records the back-off waits."""
    channel = channel or FakeChannel()
    sleeps: list[float] = []
    consumer = RabbitConsumer(
        make_config(),
        processor,
        connect=connect or (lambda: FakeConnection(channel)),
        sleep=sleeps.append,
    )
    # Real consumption blocks until a stop; once the backlog is drained the fake
    # delivers the "SIGTERM" that ends the run.
    if channel.on_idle is None:
        channel.on_idle = consumer.request_stop
    return consumer, channel, sleeps
