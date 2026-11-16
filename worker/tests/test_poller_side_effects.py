"""Side effects of reading a plate: RDS plate transition, DynamoDB audit, SQS delete.

The Worker only enriches a session. The car is already ``PARKED`` and holding
its spot when the message arrives; whatever happens to the photo, the session
``status`` must not change and no counter is touched.

Feature: python-ocr-worker, Property 5: Exactly-once plate transition per
session. Property 9: Ordered side effects never partially commit.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fakes import (
    SESSION_ID,
    FakeSessionRepository,
    make_message,
    make_poller,
    pending_row,
)

from worker import Outcome, Poller


def deliver(poller, times: int = 1) -> None:
    for attempt in range(1, times + 1):
        poller._process_one(make_message(receive_count=attempt, handle=f"rh-{attempt}"))


def test_happy_path_records_the_plate_audits_and_deletes():
    poller, sessions, audit, sqs = make_poller()

    assert poller._handle(make_message()) is Outcome.DELETE

    row = sessions.rows[SESSION_ID]
    assert (row.plate_status, row.license_plate) == ("READ", "ABC1D23")
    assert row.status == "PARKED"  # owned by the API, never changed here
    assert audit.ocr_entries == [(SESSION_ID, "ABC1D23")]
    assert audit.failed_entries == []


def test_redelivery_after_success_is_a_no_op():
    poller, sessions, audit, sqs = make_poller()

    deliver(poller, times=3)

    assert sessions.rows[SESSION_ID].plate_status == "READ"
    assert len(audit.ocr_entries) == 1
    assert sqs.deleted == ["rh-1", "rh-2", "rh-3"]


def test_audit_failure_rolls_back_the_plate_and_retries():
    """An audit failure must not leave a READ plate that was never audited."""
    poller, sessions, audit, sqs = make_poller()
    audit.fail_ocr = 1

    poller._process_one(make_message(receive_count=1, handle="rh-1"))
    row = sessions.rows[SESSION_ID]
    assert (row.plate_status, row.license_plate) == ("PENDING", None)
    assert sqs.deleted == []

    poller._process_one(make_message(receive_count=2, handle="rh-2"))
    row = sessions.rows[SESSION_ID]
    assert (row.plate_status, row.license_plate) == ("READ", "ABC1D23")
    assert audit.ocr_entries == [(SESSION_ID, "ABC1D23")]
    assert sqs.deleted == ["rh-2"]


def test_commit_failure_keeps_the_plate_pending():
    sessions = FakeSessionRepository({SESSION_ID: pending_row()})
    sessions.fail_commit = 1
    poller, _, _, sqs = make_poller(sessions=sessions)

    assert poller._handle(make_message()) is Outcome.RETAIN
    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status, row.license_plate) == ("PARKED", "PENDING", None)


def test_lookup_failure_retains_without_side_effects():
    poller, sessions, audit, sqs = make_poller()
    sessions.fail_get = 1

    assert poller._handle(make_message()) is Outcome.RETAIN
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.ocr_entries == []


@pytest.mark.parametrize("plate_status", ["READ", "UNREADABLE", "MANUAL"])
def test_plate_no_longer_pending_is_deleted_without_effects(plate_status):
    """Already read, already given up on, or typed by the cashier: terminal."""
    row = replace(pending_row(), plate_status=plate_status, license_plate="XYZ9A88")
    poller, sessions, audit, _ = make_poller(sessions=FakeSessionRepository({SESSION_ID: row}))

    assert poller._handle(make_message()) is Outcome.DELETE

    assert sessions.rows[SESSION_ID] == row
    assert audit.ocr_entries == [] and audit.failed_entries == []


def test_missing_session_is_deleted_without_effects():
    """The cashier deleted the session before its photo was read."""
    poller, _, audit, _ = make_poller(sessions=FakeSessionRepository({}))

    assert poller._handle(make_message()) is Outcome.DELETE
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
    poller, _, audit, _ = make_poller(sessions=sessions)

    assert poller._handle(make_message()) is Outcome.DELETE

    row = sessions.rows[SESSION_ID]
    assert (row.plate_status, row.license_plate) == ("MANUAL", "LSN4I49")
    assert audit.ocr_entries == []


def test_paid_session_still_gets_its_plate_recorded():
    """The car paid and left before the photo was read (queue backlog). The
    plate is evidence for the audit trail, so it is recorded all the same and
    the session stays PAID."""
    sessions = FakeSessionRepository({SESSION_ID: pending_row(status="PAID")})
    poller, _, audit, _ = make_poller(sessions=sessions)

    assert poller._handle(make_message()) is Outcome.DELETE

    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status, row.license_plate) == ("PAID", "READ", "ABC1D23")
    assert audit.ocr_entries == [(SESSION_ID, "ABC1D23")]


def test_unreadable_plate_is_recorded_at_once_and_keeps_the_session_parked():
    """OCR is deterministic: retrying the same photo changes nothing. The photo
    is recorded as UNREADABLE on the first delivery and the message is deleted.
    The session is not failed: it stays PARKED and its ticket pays."""
    poller, sessions, audit, sqs = make_poller(plate=None)

    assert poller._handle(make_message(receive_count=1)) is Outcome.DELETE

    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status, row.license_plate) == ("PARKED", "UNREADABLE", None)
    assert len(audit.failed_entries) == 1
    assert audit.failed_entries[0][0] == SESSION_ID
    assert "unreadable" in audit.failed_entries[0][1]
    assert audit.ocr_entries == []
    assert audit.poison_entries == []


def test_unreadable_plate_redelivery_is_not_audited_twice():
    poller, sessions, audit, sqs = make_poller(plate=None)

    deliver(poller, times=2)

    assert len(audit.failed_entries) == 1
    assert sqs.deleted == ["rh-1", "rh-2"]


def test_unreadable_plate_retains_when_rds_cannot_record_it():
    class BrokenMarkUnreadable(FakeSessionRepository):
        def mark_unreadable(self, session_id: str) -> bool:
            raise RuntimeError("RDS unreachable")

    poller, sessions, audit, _ = make_poller(
        sessions=BrokenMarkUnreadable({SESSION_ID: pending_row()}), plate=None
    )

    assert poller._handle(make_message(receive_count=1)) is Outcome.RETAIN
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.failed_entries == []


def test_poller_has_no_spots_counter():
    """The spot is taken by the API when the car comes in. A Worker that also
    decremented would count every car twice."""
    poller, _, _, _ = make_poller()

    assert not hasattr(poller, "_spots")
    with pytest.raises(TypeError):
        Poller(cfg=None, sqs=None, s3=None, sessions=None, spots=object(), audit=None)
