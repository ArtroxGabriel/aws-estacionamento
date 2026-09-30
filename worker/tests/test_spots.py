"""Spots_Counter against an in-memory Redis that runs the counter's two Lua
scripts (a real Redis/Valkey run is covered by the Floci integration check)."""

from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from storage.spots import (
    _DECR_IF_EXISTS,
    _INCR_IF_EXISTS,
    SPOTS_KEY,
    SpotsCounter,
    SpotsDecrementError,
    SpotsUnderflowError,
)


class FakeRedis:
    def __init__(self, value: int | None) -> None:
        self.store = {} if value is None else {SPOTS_KEY: value}
        self.fail = 0

    def eval(self, script, numkeys, key):
        if self.fail:
            self.fail -= 1
            raise RedisConnectionError("down")
        if key not in self.store:
            return None
        if script == _DECR_IF_EXISTS:
            self.store[key] -= 1
            value = self.store[key]
            if value < 0:
                self.store[key] = 0
            return value
        assert script == _INCR_IF_EXISTS
        self.store[key] += 1
        return self.store[key]

    def close(self):
        pass


def counter(value: int | None) -> tuple[SpotsCounter, FakeRedis]:
    redis = FakeRedis(value)
    return SpotsCounter("redis://localhost:6379", client=redis), redis


def test_decrement_existing_counter():
    spots, redis = counter(10)
    assert spots.decrement() == 9
    assert redis.store[SPOTS_KEY] == 9


def test_missing_key_is_left_missing():
    """Regression: a plain DECR created the key as -1 (clamped to 0) after a
    Redis restart, so the API reported a full lot and never rebuilt the real
    count from RDS, since it only does that when the key is missing."""
    spots, redis = counter(None)

    assert spots.decrement() is None
    assert spots.increment() is None
    assert SPOTS_KEY not in redis.store


def test_underflow_is_clamped_and_reported():
    spots, redis = counter(0)
    with pytest.raises(SpotsUnderflowError):
        spots.decrement()
    assert redis.store[SPOTS_KEY] == 0


def test_connection_errors_are_retried():
    spots, redis = counter(10)
    redis.fail = 2
    assert spots.decrement() == 9

    redis.fail = 3
    with pytest.raises(SpotsDecrementError):
        spots.decrement()
    assert redis.store[SPOTS_KEY] == 9
