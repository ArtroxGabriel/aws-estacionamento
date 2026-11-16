"""Poison classification driven by ApproximateReceiveCount.

Feature: python-ocr-worker, Property 8: Poison classification is driven by
receive count. The SQS redrive policy moves a message to the DLQ once it has
been received MAX_RECEIVE_COUNT times, so the Worker must record the poison
audit entry on that *last* delivery — it never sees a later one.

Giving up on a message also closes the plate reading (``PENDING`` ->
``UNREADABLE``); the session itself stays ``PARKED`` and its ticket pays.
"""

from __future__ import annotations

from dataclasses import replace

from fakes import SESSION_ID, FakeSessionRepository, make_message, make_poller, pending_row
from hypothesis import given
from hypothesis import strategies as st

from ocr.processor import OcrResult
from worker import MAX_RECEIVE_COUNT, Outcome


def failing_ocr(_image: bytes) -> OcrResult:
    return OcrResult(ok=False, raw_text=None, error="decode")


def test_last_delivery_that_fails_records_poison():
    """Regression: the check was ``count > 3``, which never fires when the
    redrive policy already moved the message after the 3rd receive."""
    poller, sessions, audit, sqs = make_poller(ocr=failing_ocr)

    outcome = poller._handle(make_message(receive_count=MAX_RECEIVE_COUNT))

    assert outcome is Outcome.POISON
    assert len(audit.poison_entries) == 1
    session_id, reason = audit.poison_entries[0]
    assert session_id == SESSION_ID
    assert "OCR failed" in reason
    # Giving up closes the plate reading; the session itself is untouched.
    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status) == ("PARKED", "UNREADABLE")
    assert len(audit.failed_entries) == 1
    assert audit.failed_entries[0][0] == SESSION_ID


def test_failure_before_the_last_delivery_keeps_the_plate_pending():
    poller, sessions, audit, _ = make_poller(ocr=failing_ocr)

    outcome = poller._handle(make_message(receive_count=MAX_RECEIVE_COUNT - 1))

    assert outcome is Outcome.RETAIN
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.poison_entries == [] and audit.failed_entries == []


class ErrorS3:
    def download(self, key: str) -> bytes:
        raise RuntimeError("network timeout to S3")


def test_outage_on_last_delivery_also_closes_the_plate_reading():
    """An outage that outlasts the 3 deliveries must not leave the session
    showing "reading the plate" forever: nothing will read that photo again.
    The session stays PARKED; only the plate is given up on."""
    poller, sessions, audit, _ = make_poller(s3=ErrorS3())

    outcome = poller._handle(make_message(receive_count=MAX_RECEIVE_COUNT))

    assert outcome is Outcome.POISON
    assert len(audit.poison_entries) == 1
    assert "S3 download failed" in audit.poison_entries[0][1]
    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status) == ("PARKED", "UNREADABLE")
    assert len(audit.failed_entries) == 1
    assert "S3 download failed" in audit.failed_entries[0][1]


def test_poison_survives_rds_being_down():
    """With RDS down the plate cannot be closed; the poison is still audited
    and the loop goes on."""

    class DownRds(FakeSessionRepository):
        def get(self, session_id):
            raise ConnectionError("RDS unreachable")

        def mark_unreadable(self, session_id):
            raise ConnectionError("RDS unreachable")

    sessions = DownRds({SESSION_ID: pending_row()})
    poller, _, audit, _ = make_poller(sessions=sessions)

    outcome = poller._handle(make_message(receive_count=MAX_RECEIVE_COUNT))

    assert outcome is Outcome.POISON
    assert len(audit.poison_entries) == 1
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.failed_entries == []


def test_plate_already_unreadable_is_terminal_delete():
    sessions = FakeSessionRepository(
        {SESSION_ID: replace(pending_row(), plate_status="UNREADABLE")}
    )
    poller, _, audit, _ = make_poller(sessions=sessions)

    outcome = poller._handle(make_message(receive_count=1))

    assert outcome is Outcome.DELETE
    assert audit.ocr_entries == []
    assert audit.failed_entries == []
    assert audit.poison_entries == []


def test_last_delivery_that_succeeds_is_not_poison():
    poller, sessions, audit, _ = make_poller()

    assert poller._handle(make_message(receive_count=MAX_RECEIVE_COUNT)) is Outcome.DELETE
    assert audit.poison_entries == []
    assert sessions.rows[SESSION_ID].plate_status == "READ"


def test_malformed_body_records_poison_only_on_last_delivery():
    poller, sessions, audit, _ = make_poller()

    assert poller._handle(make_message(body="not json", receive_count=1)) is Outcome.RETAIN
    assert audit.poison_entries == []

    outcome = poller._handle(make_message(body="not json", receive_count=MAX_RECEIVE_COUNT))
    assert outcome is Outcome.POISON
    assert audit.poison_entries == [("unknown", "malformed JSON")]
    # No session can be told from a malformed body: nothing is touched.
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.failed_entries == []


def test_beyond_threshold_is_not_reprocessed_nor_re_audited():
    """Without a working redrive the message keeps coming back; it must not
    be reprocessed nor spam the audit table on every delivery."""
    poller, sessions, audit, _ = make_poller()

    outcome = poller._handle(make_message(receive_count=MAX_RECEIVE_COUNT + 1))

    assert outcome is Outcome.POISON
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.poison_entries == [] and audit.ocr_entries == []


@given(st.integers(min_value=1, max_value=50))
def test_failing_message_is_poison_iff_count_reaches_threshold(count):
    poller, _, audit, _ = make_poller(ocr=failing_ocr)

    outcome = poller._handle(make_message(receive_count=count))

    if count >= MAX_RECEIVE_COUNT:
        assert outcome is Outcome.POISON
    else:
        assert outcome is Outcome.RETAIN
    assert len(audit.poison_entries) == (1 if count == MAX_RECEIVE_COUNT else 0)
