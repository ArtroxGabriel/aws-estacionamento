"""Ordered side effects: RDS transition, Redis DECR, DynamoDB audit, SQS delete.

Feature: python-ocr-worker, Property 5: Exactly-once transition and spots
decrement per session. Property 9: Ordered side effects never partially commit.
"""

from __future__ import annotations

from fakes import (
    SESSION_ID,
    FakeSessionRepository,
    FakeSpotsCounter,
    make_message,
    make_poller,
    processing_row,
)

from worker import Outcome


def deliver(poller, times: int = 1) -> None:
    for attempt in range(1, times + 1):
        poller._process_one(make_message(receive_count=attempt, handle=f"rh-{attempt}"))


def test_happy_path_parks_decrements_audits_and_deletes():
    poller, sessions, spots, audit, sqs = make_poller()

    assert poller._handle(make_message()) is Outcome.DELETE

    row = sessions.rows[SESSION_ID]
    assert (row.status, row.license_plate) == ("PARKED", "ABC1D23")
    assert spots.value == 9
    assert audit.ocr_entries == [(SESSION_ID, "ABC1D23")]


def test_redelivery_after_success_is_a_no_op():
    poller, sessions, spots, audit, sqs = make_poller()

    deliver(poller, times=3)

    assert spots.value == 9
    assert len(audit.ocr_entries) == 1
    assert sqs.deleted == ["rh-1", "rh-2", "rh-3"]


def test_redis_failure_does_not_lose_the_decrement_on_redelivery():
    """Regression: RDS used to commit PARKED before Redis failed, so the
    redelivery short-circuited on PARKED and the DECR was lost forever."""
    poller, sessions, spots, audit, sqs = make_poller()
    spots.fail_decrement = 1

    poller._process_one(make_message(receive_count=1, handle="rh-1"))
    assert sessions.rows[SESSION_ID].status == "PROCESSING"
    assert sqs.deleted == []

    poller._process_one(make_message(receive_count=2, handle="rh-2"))
    assert sessions.rows[SESSION_ID].status == "PARKED"
    assert spots.value == 9
    assert audit.ocr_entries == [(SESSION_ID, "ABC1D23")]
    assert sqs.deleted == ["rh-2"]


def test_audit_failure_rolls_back_and_compensates_the_decrement():
    """Regression: an audit failure after the RDS commit lost both the audit
    entry and (on redelivery) any chance of recording it."""
    poller, sessions, spots, audit, sqs = make_poller()
    audit.fail_ocr = 1

    poller._process_one(make_message(receive_count=1, handle="rh-1"))
    assert sessions.rows[SESSION_ID].status == "PROCESSING"
    assert spots.value == 10  # DECR compensated by INCR
    assert sqs.deleted == []

    poller._process_one(make_message(receive_count=2, handle="rh-2"))
    assert sessions.rows[SESSION_ID].status == "PARKED"
    assert spots.value == 9
    assert audit.ocr_entries == [(SESSION_ID, "ABC1D23")]
    assert sqs.deleted == ["rh-2"]


def test_commit_failure_compensates_the_decrement():
    sessions = FakeSessionRepository({SESSION_ID: processing_row()})
    sessions.fail_commit = 1
    poller, _, spots, _, sqs = make_poller(sessions=sessions)

    assert poller._handle(make_message()) is Outcome.RETAIN
    assert sessions.rows[SESSION_ID].status == "PROCESSING"
    assert spots.value == 10
    assert (spots.decrements, spots.increments) == (1, 1)


def test_underflow_is_clamped_and_still_parks():
    poller, sessions, spots, audit, sqs = make_poller(spots=FakeSpotsCounter(value=0))

    assert poller._handle(make_message()) is Outcome.DELETE
    assert sessions.rows[SESSION_ID].status == "PARKED"
    assert spots.value == 0
    assert spots.increments == 0
    assert len(audit.ocr_entries) == 1


def test_lookup_failure_retains_without_side_effects():
    poller, sessions, spots, audit, sqs = make_poller()
    sessions.fail_get = 1

    assert poller._handle(make_message()) is Outcome.RETAIN
    assert spots.decrements == 0
    assert audit.ocr_entries == []


def test_terminal_statuses_and_missing_session_are_deleted_without_effects():
    for status in ("PARKED", "PAID"):
        row = processing_row()
        row.status = status
        poller, _, spots, audit, _ = make_poller(sessions=FakeSessionRepository({SESSION_ID: row}))
        assert poller._handle(make_message()) is Outcome.DELETE
        assert spots.decrements == 0 and audit.ocr_entries == []

    poller, _, spots, _, _ = make_poller(sessions=FakeSessionRepository({}))
    assert poller._handle(make_message()) is Outcome.DELETE
    assert spots.decrements == 0


def test_unreadable_plate_retains_and_keeps_processing():
    poller, sessions, spots, audit, sqs = make_poller(plate=None)

    assert poller._handle(make_message()) is Outcome.RETAIN
    assert sessions.rows[SESSION_ID].status == "PROCESSING"
    assert sessions.rows[SESSION_ID].license_plate is None
    assert spots.decrements == 0


def test_missing_counter_key_still_parks_without_touching_the_counter():
    """After a Redis restart the key is absent until the API rebuilds it from
    RDS; the Worker must neither create it nor compensate a DECR it never did."""
    poller, sessions, spots, audit, sqs = make_poller(spots=FakeSpotsCounter(value=None))
    audit.fail_ocr = 1

    assert poller._handle(make_message(receive_count=1)) is Outcome.RETAIN
    assert (spots.value, spots.increments) == (None, 0)

    assert poller._handle(make_message(receive_count=2)) is Outcome.DELETE
    assert sessions.rows[SESSION_ID].status == "PARKED"
    assert spots.value is None
