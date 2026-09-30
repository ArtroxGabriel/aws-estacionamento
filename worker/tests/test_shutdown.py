"""Graceful shutdown (Req 16): the stop signal is simulated by calling
``request_stop`` from inside the blocking call, as the real signal handler runs
in the main thread while the call is in progress."""

from __future__ import annotations

import time

from fakes import SESSION_ID, make_message, make_poller


def test_stop_during_long_poll_ends_the_wait_at_once():
    """Regression: the loop kept waiting for the in-flight long poll (up to
    20 s), so `docker stop` with its default 10 s timeout killed the worker."""
    poller, _, _, _, sqs = make_poller()

    def blocked_long_poll():
        poller.request_stop(15, None)
        time.sleep(5)  # never reached: the handler cuts the wait short

    sqs.on_receive = blocked_long_poll

    started = time.monotonic()
    exit_code = poller.run()

    assert exit_code == 0
    assert time.monotonic() - started < 1
    assert sqs.receives == 1


def test_stop_during_receive_backoff_ends_the_wait_at_once():
    poller, _, _, _, sqs = make_poller()

    def failing_receive():
        raise ConnectionError("SQS unreachable")

    def backoff(seconds):
        poller.request_stop(15, None)
        time.sleep(5)  # never reached

    sqs.on_receive = failing_receive
    poller._sleep = backoff

    started = time.monotonic()
    assert poller.run() == 0
    assert time.monotonic() - started < 1


def test_stop_mid_batch_finishes_current_message_and_releases_the_rest():
    """Regression: after SIGTERM the loop kept processing the rest of a batch
    of up to 10 messages."""
    poller, sessions, spots, audit, sqs = make_poller()
    sqs.batches = [
        [
            make_message(handle="rh-1"),
            make_message(handle="rh-2"),
            make_message(handle="rh-3"),
        ]
    ]

    def ocr_then_signal(_image):
        poller.request_stop(15, None)  # arrives while message 1 is processed
        return original_ocr(_image)

    original_ocr = poller._ocr
    poller._ocr = ocr_then_signal

    assert poller.run() == 0

    # The in-flight message was not interrupted: fully processed and deleted.
    assert sessions.rows[SESSION_ID].status == "PARKED"
    assert spots.decrements == 1 and len(audit.ocr_entries) == 1
    assert sqs.deleted == ["rh-1"]
    # The others go straight back to the queue instead of waiting 300 s.
    assert sqs.released == ["rh-2", "rh-3"]


def test_stop_before_receive_does_not_poll():
    poller, _, _, _, sqs = make_poller()
    poller.request_stop(15, None)

    assert poller.run() == 0
    assert sqs.receives == 0
