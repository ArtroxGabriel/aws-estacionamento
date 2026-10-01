"""In-memory fakes for the Poller's connectors.

They model only the behavior the Poller relies on: the RDS row transition with
commit/rollback semantics, the Redis counter, the DynamoDB audit trail, and the
SQS delete call. Each fake can be told to fail the next N calls so tests can
inject failures at any step of the pipeline.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace

from config import Config, load_config
from ocr.clean import PlateResult
from ocr.processor import OcrResult
from storage.audit import AuditWriteError
from storage.session_repo import SessionRow
from storage.spots import SpotsDecrementError, SpotsUnderflowError
from worker import Poller

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
    def parking_transition(self, session_id: str, plate: str) -> Iterator[bool]:
        row = self.rows.get(session_id)
        if row is None or row.status != "PROCESSING":
            yield False
            return
        snapshot = replace(row)
        self.rows[session_id] = replace(row, status="PARKED", license_plate=plate)
        try:
            yield True
        except BaseException:
            self.rows[session_id] = snapshot  # ROLLBACK
            raise
        if self.fail_commit:
            self.fail_commit -= 1
            self.rows[session_id] = snapshot
            raise CommitError("COMMIT failed")

    def mark_failed(self, session_id: str) -> bool:
        row = self.rows.get(session_id)
        if row is not None and row.status == "PROCESSING":
            self.rows[session_id] = replace(row, status="FAILED")
            return True
        return False


class FakeSpotsCounter:
    """``value=None`` models the ``spots:available`` key being absent."""

    def __init__(self, value: int | None = 10) -> None:
        self.value = value
        self.fail_decrement = 0
        self.decrements = 0
        self.increments = 0

    def decrement(self) -> int | None:
        if self.fail_decrement:
            self.fail_decrement -= 1
            raise SpotsDecrementError(3)
        if self.value is None:
            return None
        self.decrements += 1
        self.value -= 1
        if self.value < 0:
            observed = self.value
            self.value = 0
            raise SpotsUnderflowError(observed)
        return self.value

    def increment(self) -> int | None:
        if self.value is None:
            return None
        self.increments += 1
        self.value += 1
        return self.value


class FakeAuditLogger:
    def __init__(self) -> None:
        self.ocr_entries: list[tuple[str, str]] = []
        self.poison_entries: list[tuple[str, str]] = []
        self.fail_ocr = 0

    def log_ocr(self, session_id: str, plate: str) -> None:
        if self.fail_ocr:
            self.fail_ocr -= 1
            raise AuditWriteError(session_id, 3)
        self.ocr_entries.append((session_id, plate))

    def log_poison(self, session_id: str, reason: str) -> None:
        self.poison_entries.append((session_id, reason))


class FakeS3:
    def download(self, key: str) -> bytes:
        return b"image-bytes"


class FakeSQS:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.released: list[str] = []
        self.receives = 0
        # Each receive pops the next batch; ``on_receive`` runs inside the call
        # (e.g. to deliver a stop signal while the long poll is blocked).
        self.batches: list[list[dict]] = []
        self.on_receive = None

    def receive_message(self, **kwargs) -> dict:
        self.receives += 1
        if self.on_receive is not None:
            self.on_receive()
        return {"Messages": self.batches.pop(0) if self.batches else []}

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> None:  # noqa: N803
        self.deleted.append(ReceiptHandle)

    def change_message_visibility_batch(self, QueueUrl: str, Entries: list[dict]) -> None:  # noqa: N803
        assert all(e["VisibilityTimeout"] == 0 for e in Entries)
        self.released.extend(e["ReceiptHandle"] for e in Entries)


BASE_ENV = {
    "AWS_ENDPOINT_URL": "http://localhost:4566",
    "AWS_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "SQS_QUEUE_URL": "http://localhost:4566/000000000000/ocr-processamento-fila",
    "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
    "REDIS_URL": "redis://localhost:6379",
    "S3_BUCKET_NAME": "bucket",
    "DYNAMODB_TABLE_NAME": "AuditoriaEstacionamento",
}


def make_config(**overrides: str) -> Config:
    return load_config({**BASE_ENV, **overrides})


def make_message(body: str | None = None, receive_count: int = 1, handle: str = "rh-1") -> dict:
    if body is None:
        body = f'{{"session_id": "{SESSION_ID}", "s3_key": "{S3_KEY}"}}'
    return {
        "Body": body,
        "ReceiptHandle": handle,
        "Attributes": {"ApproximateReceiveCount": str(receive_count)},
    }


def processing_row(session_id: str = SESSION_ID) -> SessionRow:
    return SessionRow(
        id=session_id,
        license_plate=None,
        status="PROCESSING",
        s3_photo_key=S3_KEY,
    )


def make_poller(
    sessions: FakeSessionRepository | None = None,
    spots: FakeSpotsCounter | None = None,
    audit: FakeAuditLogger | None = None,
    plate: str | None = "ABC1D23",
) -> tuple[Poller, FakeSessionRepository, FakeSpotsCounter, FakeAuditLogger, FakeSQS]:
    sessions = sessions or FakeSessionRepository({SESSION_ID: processing_row()})
    spots = spots or FakeSpotsCounter()
    audit = audit or FakeAuditLogger()
    sqs = FakeSQS()

    def fake_ocr(_image: bytes) -> OcrResult:
        return OcrResult(ok=True, raw_text=plate or "???", error=None)

    def fake_normalizer(_raw: str, **_kwargs) -> PlateResult:
        if plate is None:
            return PlateResult(ok=False, plate=None, reason="no_match")
        return PlateResult(ok=True, plate=plate, reason=None)

    poller = Poller(
        cfg=make_config(),
        sqs=sqs,
        s3=FakeS3(),
        sessions=sessions,
        spots=spots,
        audit=audit,
        ocr=fake_ocr,
        normalizer=fake_normalizer,
    )
    return poller, sessions, spots, audit, sqs
