"""Poison classification driven by ApproximateReceiveCount.

Feature: python-ocr-worker, Property 8: Poison classification is driven by
receive count. The SQS redrive policy moves a message to the DLQ once it has
been received MAX_RECEIVE_COUNT times, so the Worker must record the poison
audit entry on that *last* delivery — it never sees a later one.
"""

from __future__ import annotations

from fakes import SESSION_ID, make_message, make_poller
from hypothesis import given
from hypothesis import strategies as st

from ocr.processor import OcrResult
from worker import MAX_RECEIVE_COUNT, Outcome


def failing_ocr(_image: bytes) -> OcrResult:
    return OcrResult(ok=False, raw_text=None, error="decode")


def test_last_delivery_that_fails_records_poison():
    """Regression: the check was `count > 3`, which never fires when the
    redrive policy already moved the message after the 3rd receive."""
    poller, sessions, spots, audit, sqs = make_poller(ocr=failing_ocr)

    outcome = poller._handle(make_message(receive_count=MAX_RECEIVE_COUNT))

    assert outcome is Outcome.POISON
    assert len(audit.poison_entries) == 1
    session_id, reason = audit.poison_entries[0]
    assert session_id == SESSION_ID
    assert "OCR failed" in reason
    assert sessions.rows[SESSION_ID].status == "FAILED"
    assert len(audit.failed_entries) == 1
    assert audit.failed_entries[0][0] == SESSION_ID


class ErrorS3:
    def download(self, key: str) -> bytes:
        raise RuntimeError("network timeout to S3")


def test_transient_failure_on_last_delivery_keeps_session_processing():
    poller, sessions, _, audit, _ = make_poller(s3=ErrorS3())

    outcome = poller._handle(make_message(receive_count=MAX_RECEIVE_COUNT))

    assert outcome is Outcome.POISON
    assert len(audit.poison_entries) == 1
    assert "S3 download failed" in audit.poison_entries[0][1]
    assert sessions.rows[SESSION_ID].status == "PROCESSING"
    assert audit.failed_entries == []


def test_session_already_failed_is_terminal_delete():
    from fakes import FakeSessionRepository, SessionRow

    sessions = FakeSessionRepository(
        {
            SESSION_ID: SessionRow(
                id=SESSION_ID,
                license_plate=None,
                status="FAILED",
                s3_photo_key="photos/test.jpg",
            )
        }
    )
    poller, _, spots, audit, _ = make_poller(sessions=sessions)

    outcome = poller._handle(make_message(receive_count=1))

    assert outcome is Outcome.DELETE
    assert spots.decrements == 0
    assert audit.ocr_entries == []
    assert audit.poison_entries == []


def test_last_delivery_that_succeeds_is_not_poison():
    poller, _, _, audit, _ = make_poller()

    assert poller._handle(make_message(receive_count=MAX_RECEIVE_COUNT)) is Outcome.DELETE
    assert audit.poison_entries == []


def test_malformed_body_records_poison_only_on_last_delivery():
    poller, _, _, audit, _ = make_poller()

    assert poller._handle(make_message(body="not json", receive_count=1)) is Outcome.RETAIN
    assert audit.poison_entries == []

    outcome = poller._handle(make_message(body="not json", receive_count=MAX_RECEIVE_COUNT))
    assert outcome is Outcome.POISON
    assert audit.poison_entries == [("unknown", "malformed JSON")]


def test_beyond_threshold_is_not_reprocessed_nor_re_audited():
    """Without a working redrive the message keeps coming back; it must not
    be reprocessed nor spam the audit table on every delivery."""
    poller, _, spots, audit, _ = make_poller()

    outcome = poller._handle(make_message(receive_count=MAX_RECEIVE_COUNT + 1))

    assert outcome is Outcome.POISON
    assert spots.decrements == 0
    assert audit.poison_entries == []


@given(st.integers(min_value=1, max_value=50))
def test_failing_message_is_poison_iff_count_reaches_threshold(count):
    poller, _, _, audit, _ = make_poller(ocr=failing_ocr)

    outcome = poller._handle(make_message(receive_count=count))

    if count >= MAX_RECEIVE_COUNT:
        assert outcome is Outcome.POISON
    else:
        assert outcome is Outcome.RETAIN
    assert len(audit.poison_entries) == (1 if count == MAX_RECEIVE_COUNT else 0)
