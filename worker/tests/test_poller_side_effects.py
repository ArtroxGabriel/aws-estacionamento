"""Side effects of reading a plate: RDS plate transition and DynamoDB audit.

The Worker only enriches a session. The car is already ``PARKED`` and holding
its spot when the message arrives; whatever happens to the photo, the session
``status`` must not change and no counter is touched.

Feature: python-ocr-worker, Property 5: Exactly-once plate transition per
session. Property 9: Ordered side effects never partially commit.
"""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest
from fakes import (
    SESSION_ID,
    FakeSessionRepository,
    make_body,
    make_processor,
    pending_row,
)

from processor import MessageProcessor, Outcome


def outcome_of(processor, attempt: int = 1, body: str | None = None) -> Outcome:
    return processor.handle(make_body() if body is None else body, attempt).outcome


def test_happy_path_records_the_plate_and_audits():
    processor, sessions, audit = make_processor()

    assert outcome_of(processor) is Outcome.DELETE

    row = sessions.rows[SESSION_ID]
    assert (row.plate_status, row.license_plate) == ("READ", "ABC1D23")
    assert row.status == "PARKED"  # owned by the API, never changed here
    assert audit.ocr_entries == [(SESSION_ID, "ABC1D23")]
    assert audit.failed_entries == []


def test_redelivery_after_success_is_a_no_op():
    processor, sessions, audit = make_processor()

    outcomes = [outcome_of(processor, attempt) for attempt in (1, 2, 3)]

    assert outcomes == [Outcome.DELETE] * 3
    assert sessions.rows[SESSION_ID].plate_status == "READ"
    assert len(audit.ocr_entries) == 1


def test_audit_failure_rolls_back_the_plate_and_retries():
    """An audit failure must not leave a READ plate that was never audited."""
    processor, sessions, audit = make_processor()
    audit.fail_ocr = 1

    assert outcome_of(processor, attempt=1) is Outcome.RETAIN
    row = sessions.rows[SESSION_ID]
    assert (row.plate_status, row.license_plate) == ("PENDING", None)

    assert outcome_of(processor, attempt=2) is Outcome.DELETE
    row = sessions.rows[SESSION_ID]
    assert (row.plate_status, row.license_plate) == ("READ", "ABC1D23")
    assert audit.ocr_entries == [(SESSION_ID, "ABC1D23")]


def test_commit_failure_keeps_the_plate_pending():
    sessions = FakeSessionRepository({SESSION_ID: pending_row()})
    sessions.fail_commit = 1
    processor, _, _ = make_processor(sessions=sessions)

    assert outcome_of(processor) is Outcome.RETAIN
    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status, row.license_plate) == ("PARKED", "PENDING", None)


def test_lookup_failure_retains_without_side_effects():
    processor, sessions, audit = make_processor()
    sessions.fail_get = 1

    assert outcome_of(processor) is Outcome.RETAIN
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.ocr_entries == []


def test_unexpected_exception_is_a_retry_not_a_crash():
    """The consumer must survive any message: a bug becomes a retry (and, on the
    last attempt, a poison message), never an exception out of ``handle``."""

    def exploding_ocr(_image: bytes):
        raise RuntimeError("bug in the OCR")

    processor, sessions, _ = make_processor(ocr=exploding_ocr)

    result = processor.handle(make_body(), 1)

    assert (result.outcome, result.reason) == (Outcome.RETAIN, "unhandled error")
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"


@pytest.mark.parametrize("plate_status", ["READ", "UNREADABLE", "MANUAL"])
def test_plate_no_longer_pending_is_acknowledged_without_effects(plate_status):
    """Already read, already given up on, or typed by the cashier: terminal."""
    row = replace(pending_row(), plate_status=plate_status, license_plate="XYZ9A88")
    processor, sessions, audit = make_processor(sessions=FakeSessionRepository({SESSION_ID: row}))

    assert outcome_of(processor) is Outcome.DELETE

    assert sessions.rows[SESSION_ID] == row
    assert audit.ocr_entries == [] and audit.failed_entries == []


def test_missing_session_is_acknowledged_without_effects():
    """The cashier deleted the session before its photo was read."""
    processor, _, audit = make_processor(sessions=FakeSessionRepository({}))

    assert outcome_of(processor) is Outcome.DELETE
    assert audit.ocr_entries == [] and audit.failed_entries == []


def test_plate_typed_by_the_cashier_is_never_overwritten():
    """The cashier types the plate while the photo is being read: the OCR
    result arrives late and must lose to the person."""

    class CashierWinsTheRace(FakeSessionRepository):
        def get(self, session_id):
            row = super().get(session_id)  # the Worker still sees PENDING...
            self.rows[session_id] = replace(
                self.rows[session_id], plate_status="MANUAL", license_plate="LSN4I49"
            )  # ...and the cashier saves right after
            return row

    sessions = CashierWinsTheRace({SESSION_ID: pending_row()})
    processor, _, audit = make_processor(sessions=sessions)

    assert outcome_of(processor) is Outcome.DELETE

    row = sessions.rows[SESSION_ID]
    assert (row.plate_status, row.license_plate) == ("MANUAL", "LSN4I49")
    assert audit.ocr_entries == []


def test_paid_session_still_gets_its_plate_recorded():
    """The car paid and left before the photo was read (queue backlog). The
    plate is evidence for the audit trail, so it is recorded all the same and
    the session stays PAID."""
    sessions = FakeSessionRepository({SESSION_ID: pending_row(status="PAID")})
    processor, _, audit = make_processor(sessions=sessions)

    assert outcome_of(processor) is Outcome.DELETE

    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status, row.license_plate) == ("PAID", "READ", "ABC1D23")
    assert audit.ocr_entries == [(SESSION_ID, "ABC1D23")]


def test_unreadable_plate_is_recorded_at_once_and_keeps_the_session_parked():
    """OCR is deterministic: retrying the same photo changes nothing. The photo
    is recorded as UNREADABLE on the first attempt and the message is
    acknowledged. The session is not failed: it stays PARKED and its ticket
    pays."""
    processor, sessions, audit = make_processor(plate=None)

    assert outcome_of(processor, attempt=1) is Outcome.DELETE

    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status, row.license_plate) == ("PARKED", "UNREADABLE", None)
    assert len(audit.failed_entries) == 1
    assert audit.failed_entries[0][0] == SESSION_ID
    assert "unreadable" in audit.failed_entries[0][1]
    assert audit.ocr_entries == []
    assert audit.poison_entries == []


def test_unreadable_plate_redelivery_is_not_audited_twice():
    processor, sessions, audit = make_processor(plate=None)

    assert [outcome_of(processor, attempt) for attempt in (1, 2)] == [Outcome.DELETE] * 2

    assert len(audit.failed_entries) == 1


def test_unreadable_plate_retains_when_rds_cannot_record_it():
    class BrokenMarkUnreadable(FakeSessionRepository):
        def mark_unreadable(self, session_id: str) -> bool:
            raise RuntimeError("RDS unreachable")

    processor, sessions, audit = make_processor(
        sessions=BrokenMarkUnreadable({SESSION_ID: pending_row()}), plate=None
    )

    assert outcome_of(processor, attempt=1) is Outcome.RETAIN
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.failed_entries == []


def test_processor_has_no_spots_counter():
    """The spot is taken by the API when the car comes in. A Worker that also
    decremented would count every car twice."""
    processor, _, _ = make_processor()

    assert not hasattr(processor, "_spots")
    assert "spots" not in inspect.signature(MessageProcessor.__init__).parameters
