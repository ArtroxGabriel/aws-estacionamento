"""RabbitMQ transport: acknowledgement, retry queue, DLQ, reconnection and shutdown.

The broker is a fake channel/connection (see ``fakes.py``); the business rules are
covered in ``test_poller_side_effects.py`` and ``test_poison.py``.
"""

from __future__ import annotations

import pytest
from fakes import (
    SESSION_ID,
    FakeChannel,
    FakeConnection,
    make_body,
    make_consumer,
    make_processor,
)
from pika.exceptions import AMQPConnectionError, ChannelClosedByBroker

from consumer import ATTEMPT_HEADER, MAX_BACKOFF_SECONDS, REASON_HEADER, attempt_of
from ocr.processor import OcrResult
from processor import MAX_ATTEMPTS

QUEUE = "ocr-processamento-fila"
RETRY = "ocr-processamento-fila-retry"
DLQ = "ocr-processamento-fila-dlq"


def failing_ocr(_image: bytes) -> OcrResult:
    return OcrResult(ok=False, raw_text=None, error="decode")


def body() -> bytes:
    return make_body().encode()


def test_consumes_one_message_at_a_time_from_the_main_queue():
    processor, _, _ = make_processor()
    consumer, channel, _ = make_consumer(processor)

    assert consumer.run() == 0

    assert channel.queue == QUEUE
    assert channel.qos == 1
    assert channel.confirm is True


def test_success_is_acknowledged_and_nothing_is_published():
    processor, sessions, audit = make_processor()
    consumer, channel, _ = make_consumer(processor)
    channel.backlog = [(body(), None)]

    assert consumer.run() == 0

    assert channel.acked == [1]
    assert channel.published == []
    assert sessions.rows[SESSION_ID].plate_status == "READ"
    assert audit.ocr_entries == [(SESSION_ID, "ABC1D23")]


def test_transient_failure_goes_to_the_retry_queue_with_the_next_attempt():
    processor, sessions, _ = make_processor(ocr=failing_ocr)
    consumer, channel, _ = make_consumer(processor)
    channel.backlog = [(body(), None)]  # no header = first delivery

    consumer.run()

    assert [(p["queue"], p["headers"][ATTEMPT_HEADER]) for p in channel.published] == [(RETRY, 2)]
    assert channel.published[0]["body"] == body()
    assert channel.published[0]["delivery_mode"] == 2  # persistent
    assert channel.published[0]["mandatory"] is True
    assert channel.acked == [1]  # the original is acknowledged only after the publication
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"


def test_attempt_counter_keeps_growing_through_the_retry_queue():
    processor, _, _ = make_processor(ocr=failing_ocr)
    consumer, channel, _ = make_consumer(processor)
    channel.backlog = [(body(), {ATTEMPT_HEADER: 2})]

    consumer.run()

    assert [(p["queue"], p["headers"][ATTEMPT_HEADER]) for p in channel.published] == [(RETRY, 3)]


def test_last_failed_attempt_goes_to_the_dlq_and_closes_the_plate_reading():
    processor, sessions, audit = make_processor(ocr=failing_ocr)
    consumer, channel, _ = make_consumer(processor)
    channel.backlog = [(body(), {ATTEMPT_HEADER: MAX_ATTEMPTS})]

    consumer.run()

    (published,) = channel.published
    assert published["queue"] == DLQ
    assert "OCR failed" in published["headers"][REASON_HEADER]
    assert published["headers"][ATTEMPT_HEADER] == MAX_ATTEMPTS
    assert published["body"] == body()
    assert channel.acked == [1]
    assert len(audit.poison_entries) == 1
    row = sessions.rows[SESSION_ID]
    assert (row.status, row.plate_status) == ("PARKED", "UNREADABLE")


def test_three_failures_in_a_row_end_in_the_dlq():
    """Walks the message through the retry queue the way the broker would: after
    the TTL it comes back to the main queue carrying the headers it was given."""
    processor, _, audit = make_processor(ocr=failing_ocr)
    headers = None
    destinations = []

    for _ in range(MAX_ATTEMPTS):
        consumer, channel, _ = make_consumer(processor)
        channel.backlog = [(body(), headers)]
        consumer.run()
        (published,) = channel.published
        destinations.append(published["queue"])
        headers = published["headers"]

    assert destinations == [RETRY, RETRY, DLQ]
    assert len(audit.poison_entries) == 1


def test_unreadable_plate_is_acknowledged_without_retry():
    processor, sessions, _ = make_processor(plate=None)
    consumer, channel, _ = make_consumer(processor)
    channel.backlog = [(body(), None)]

    consumer.run()

    assert channel.acked == [1]
    assert channel.published == []
    assert sessions.rows[SESSION_ID].plate_status == "UNREADABLE"


def test_failed_publication_leaves_the_original_unacknowledged():
    """The broker refused the retry copy: acknowledging the original would lose
    the message, so the consumer reconnects and the broker redelivers it."""
    processor, _, _ = make_processor(ocr=failing_ocr)
    consumer, channel, sleeps = make_consumer(processor)
    channel.backlog = [(body(), None)]
    channel.fail_publish = 1
    record_sleep = consumer._sleep

    def sleep_then_stop(seconds):
        record_sleep(seconds)
        consumer.request_stop()  # ends the run once the back-off is over

    consumer._sleep = sleep_then_stop

    assert consumer.run() == 0

    assert channel.acked == []
    assert sleeps == [0.5]  # backed off (first step of the 1 s wait) instead of acknowledging


def test_connection_errors_back_off_exponentially_and_reconnect():
    processor, _, _ = make_processor()
    channel = FakeChannel()
    attempts = {"n": 0}

    def flaky_connect():
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise AMQPConnectionError("broker down")
        return FakeConnection(channel)

    consumer, _, sleeps = make_consumer(processor, channel=channel, connect=flaky_connect)
    channel.backlog = [(body(), None)]

    assert consumer.run() == 0

    assert attempts["n"] == 4
    # back-off 1 s, 2 s, 4 s, taken in short steps
    assert sum(sleeps) == pytest.approx(7.0)
    assert channel.acked == [1]


def test_backoff_is_capped_at_30_seconds():
    processor, _, _ = make_processor()
    attempts = {"n": 0}
    holder = {}

    def always_down():
        attempts["n"] += 1
        if attempts["n"] == 9:
            holder["consumer"].request_stop()
        raise AMQPConnectionError("broker down")

    consumer, _, sleeps = make_consumer(processor, connect=always_down)
    holder["consumer"] = consumer

    assert consumer.run() == 0

    assert sum(sleeps) == pytest.approx(1 + 2 + 4 + 8 + 16 + 30 + 30 + 30)
    assert consumer._backoff == MAX_BACKOFF_SECONDS


def test_missing_queue_aborts_with_exit_code_2():
    processor, _, _ = make_processor()
    channel = FakeChannel(existing_queues={QUEUE, RETRY})  # the DLQ is missing
    consumer, _, sleeps = make_consumer(processor, channel=channel)

    assert consumer.run() == 2

    assert sleeps == []  # no reconnection loop: the topology is not the Worker's to create


def test_channel_closed_for_another_reason_is_retried():
    processor, _, _ = make_processor()
    channel = FakeChannel()
    calls = {"n": 0}
    original = channel.queue_declare

    def declare(queue, passive=False, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ChannelClosedByBroker(406, "PRECONDITION_FAILED")
        return original(queue, passive=passive, **kw)

    channel.queue_declare = declare
    consumer, _, sleeps = make_consumer(processor, channel=channel)
    channel.backlog = [(body(), None)]

    assert consumer.run() == 0
    assert sleeps and channel.acked == [1]


def test_stop_while_processing_finishes_the_message_and_leaves_the_rest_queued():
    """prefetch=1: the broker holds the other messages; SIGTERM during one must
    finish and acknowledge it, then stop without taking the next."""
    processor, sessions, audit = make_processor()
    consumer, channel, _ = make_consumer(processor)
    channel.backlog = [(body(), None), (body(), None), (body(), None)]
    original_ocr = processor._ocr

    def ocr_then_signal(image):
        consumer.request_stop(15, None)  # arrives while message 1 is processed
        return original_ocr(image)

    processor._ocr = ocr_then_signal

    assert consumer.run() == 0

    # The in-flight message was not interrupted: fully processed and acknowledged.
    assert sessions.rows[SESSION_ID].plate_status == "READ"
    assert len(audit.ocr_entries) == 1
    assert channel.acked == [1]
    assert len(channel.backlog) == 2  # never delivered; the broker keeps them


def test_a_delivery_that_races_the_stop_is_handed_back():
    processor, sessions, audit = make_processor()
    consumer, channel, _ = make_consumer(processor)
    channel.backlog = [(body(), None)]
    channel.on_deliver = lambda: setattr(consumer, "_stop", True)  # stop set, delivery on its way

    assert consumer.run() == 0

    assert channel.nacked == [(1, True)]
    assert channel.acked == []
    assert sessions.rows[SESSION_ID].plate_status == "PENDING"
    assert audit.ocr_entries == []


def test_stop_before_connecting_does_not_consume():
    processor, _, _ = make_processor()
    consumer, channel, _ = make_consumer(processor)
    consumer.request_stop(15, None)

    assert consumer.run() == 0
    assert channel.callback is None


def test_stop_while_idle_ends_consumption_through_the_connection_thread():
    processor, _, _ = make_processor()
    consumer, channel, _ = make_consumer(processor)
    channel.consuming = True
    consumer._channel = channel
    consumer._connection = FakeConnection(channel)

    consumer.request_stop(15, None)

    assert channel.consuming is False
    assert consumer._connection.callbacks  # scheduled on the connection thread


def test_connections_are_closed_on_exit():
    processor, _, _ = make_processor()
    channel = FakeChannel()
    connection = FakeConnection(channel)
    consumer, _, _ = make_consumer(processor, channel=channel, connect=lambda: connection)

    consumer.run()

    assert connection.is_open is False


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (None, 1),
        ({}, 1),
        ({ATTEMPT_HEADER: 2}, 2),
        ({ATTEMPT_HEADER: "3"}, 3),
        ({ATTEMPT_HEADER: "abc"}, 1),
        ({ATTEMPT_HEADER: 0}, 1),
        ({ATTEMPT_HEADER: -4}, 1),
    ],
)
def test_attempt_of_is_conservative(headers, expected):
    class Props:
        pass

    props = Props()
    props.headers = headers
    assert attempt_of(props) == expected
