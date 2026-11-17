"""Poison classification driven by the delivery attempt.

Feature: python-ocr-worker, Property 8: Poison classification is driven by the
attempt number. The Worker itself counts the attempts (``x-attempt`` header), so
a failure on attempt ``MAX_ATTEMPTS`` is the last chance: the poison audit entry
is recorded then and the consumer moves the message to the DLQ.

Giving up on a message also closes the plate reading (``PENDING`` ->
``UNREADABLE``); the session itself stays ``PARKED`` and its ticket pays.
"""

from __future__ import annotations

from dataclasses import replace

from fakes import SESSION_ID, FakeSessionRepository, make_body, make_processor, pending_row
from hypothesis import given
from hypothesis import strategies as st

from ocr.processor import OcrResult
from processor import MAX_ATTEMPTS, Outcome


def failing_ocr(_image: bytes) -> OcrResult:
    return OcrResult(ok=False, raw_text=None, error="decode")


def test_last_attempt_that_fails_records_poison():
    processor, sessions, audit = make_processor(ocr=failing_ocr)

    result = processor.handle(make_body(), MAX_ATTEMPTS)

    assert result.outcome is Outcome.POISON
    assert "OCR failed" in (result.reason or "")
    assert len(audit.poison_entries) == 1
    session_id, reason = audit.poison_entries[0]
    assert session_id == SESSION_ID
    assert "OCR failed" in reason
    # Giving up closes the plate reading; the session itself is untouched.
    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status) == ("PARKED", "UNREADABLE")
    assert len(audit.failed_entries) == 1
    assert audit.failed_entries[0][0] == SESSION_ID


def test_failure_before_the_last_attempt_keeps_the_plate_pending():
    processor, sessions, audit = make_processor(ocr=failing_ocr)

    result = processor.handle(make_body(), MAX_ATTEMPTS - 1)

    assert result.outcome is Outcome.RETAIN
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.poison_entries == [] and audit.failed_entries == []


class ErrorS3:
    def download(self, key: str) -> bytes:
        raise RuntimeError("network timeout to S3")


def test_outage_on_last_attempt_also_closes_the_plate_reading():
    """An outage that outlasts the attempts must not leave the session showing
    "reading the plate" forever: nothing will read that photo again. The session
    stays PARKED; only the plate is given up on."""
    processor, sessions, audit = make_processor(s3=ErrorS3())

    result = processor.handle(make_body(), MAX_ATTEMPTS)

    assert result.outcome is Outcome.POISON
    assert len(audit.poison_entries) == 1
    assert "S3 download failed" in audit.poison_entries[0][1]
    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status) == ("PARKED", "UNREADABLE")
    assert len(audit.failed_entries) == 1
    assert "S3 download failed" in audit.failed_entries[0][1]


def test_poison_survives_rds_being_down():
    """With RDS down the plate cannot be closed; the poison is still audited
    and the consumer goes on."""

    class DownRds(FakeSessionRepository):
        def get(self, session_id):
            raise ConnectionError("RDS unreachable")

        def mark_unreadable(self, session_id):
            raise ConnectionError("RDS unreachable")

    sessions = DownRds({SESSION_ID: pending_row()})
    processor, _, audit = make_processor(sessions=sessions)

    result = processor.handle(make_body(), MAX_ATTEMPTS)

    assert result.outcome is Outcome.POISON
    assert len(audit.poison_entries) == 1
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.failed_entries == []


def test_plate_already_unreadable_is_terminal_delete():
    sessions = FakeSessionRepository(
        {SESSION_ID: replace(pending_row(), plate_status="UNREADABLE")}
    )
    processor, _, audit = make_processor(sessions=sessions)

    result = processor.handle(make_body(), 1)

    assert result.outcome is Outcome.DELETE
    assert audit.ocr_entries == []
    assert audit.failed_entries == []
    assert audit.poison_entries == []


def test_last_attempt_that_succeeds_is_not_poison():
    processor, sessions, audit = make_processor()

    assert processor.handle(make_body(), MAX_ATTEMPTS).outcome is Outcome.DELETE
    assert audit.poison_entries == []
    assert sessions.rows[SESSION_ID].plate_status == "READ"


def test_malformed_body_records_poison_only_on_last_attempt():
    processor, sessions, audit = make_processor()

    assert processor.handle("not json", 1).outcome is Outcome.RETAIN
    assert audit.poison_entries == []

    assert processor.handle("not json", MAX_ATTEMPTS).outcome is Outcome.POISON
    assert audit.poison_entries == [("unknown", "malformed JSON")]
    # No session can be told from a malformed body: nothing is touched.
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.failed_entries == []


@given(st.integers(min_value=1, max_value=50))
def test_failing_message_is_poison_iff_attempt_reaches_the_limit(attempt):
    processor, _, audit = make_processor(ocr=failing_ocr)

    outcome = processor.handle(make_body(), attempt).outcome

    if attempt >= MAX_ATTEMPTS:
        assert outcome is Outcome.POISON
    else:
        assert outcome is Outcome.RETAIN
    assert len(audit.poison_entries) == (1 if attempt >= MAX_ATTEMPTS else 0)
