"""Available-spots counter backed by Amazon ElastiCache (Redis).

The Spots_Counter (Req 7) atomically decrements the shared ``spots:available``
key when a vehicle transitions to ``PARKED``. The key name is a fixed contract
shared with the Go API (``api/internal/repository/redis.go``), which performs
the matching ``Increment``/``Decrement`` on the same key, so it MUST never be
renamed here.

Because the DECR is gated behind the RDS ``PROCESSING -> PARKED`` transition
(``SessionRepository.mark_parked() == True``), it fires exactly once per
session (Req 7.1, 7.4). The counter is clamped at zero on underflow (Req 7.5)
and connection failures are retried up to three times with the SQS message
left unacknowledged (Req 7.6).
"""

from __future__ import annotations

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

__all__ = [
    "SpotsCounter",
    "SpotsError",
    "SpotsUnderflowError",
    "SpotsDecrementError",
    "SPOTS_KEY",
    "MAX_RETRIES",
    "DECREMENT_TIMEOUT_SECONDS",
]

# Shared contract with the Go API — MUST match ``spots:available`` exactly.
SPOTS_KEY = "spots:available"

# Retry the DECR up to three times on connection error (Req 7.6).
MAX_RETRIES = 3

# The decrement must complete within 500 ms of the status update (Req 7.1).
DECREMENT_TIMEOUT_SECONDS = 0.5

# Lua scripts run atomically on the server. A missing key yields nil (None):
# the counter is never created by the Worker, only by the API's rebuild from RDS.
# The decrement returns the raw value (possibly -1) after clamping the key at 0,
# so the caller can report the underflow (Req 7.5).
_DECR_IF_EXISTS = """
if redis.call('EXISTS', KEYS[1]) == 0 then return nil end
local value = redis.call('DECR', KEYS[1])
if value < 0 then redis.call('SET', KEYS[1], 0) end
return value
"""
_INCR_IF_EXISTS = """
if redis.call('EXISTS', KEYS[1]) == 0 then return nil end
return redis.call('INCR', KEYS[1])
"""


class SpotsError(Exception):
    """Base error for available-spots counter operations."""


class SpotsUnderflowError(SpotsError):
    """Signals that a DECR drove the counter below zero (Req 7.5).

    The counter has already been clamped back to zero by the time this is
    raised; the transition that triggered the decrement has committed, so the
    Poller treats this as an emitted error condition rather than a reason to
    retry the message.
    """

    def __init__(self, observed: int) -> None:
        self.observed = observed
        super().__init__(
            f"spots counter underflow: DECR {SPOTS_KEY} returned {observed}; clamped to 0"
        )


class SpotsDecrementError(SpotsError):
    """Signals the decrement failed after exhausting connection retries (Req 7.6).

    The message remains unacknowledged so SQS can redeliver it once Redis
    becomes reachable again.
    """

    def __init__(self, attempts: int, cause: Exception | None = None) -> None:
        self.attempts = attempts
        self.__cause__ = cause
        super().__init__(f"failed to decrement {SPOTS_KEY} after {attempts} attempt(s)")


class SpotsCounter:
    """Mutates the ``spots:available`` counter in Redis (Req 7).

    The Redis connection target is read from ``REDIS_URL`` at construction; an
    absent/empty value fails startup so the polling loop never begins without a
    reachable counter (Req 7.2, 7.3).
    """

    def __init__(
        self,
        redis_url: str,
        *,
        client: Redis | None = None,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        target = (redis_url or "").strip()
        if not target:
            # Fail fast at startup — no connection target means the loop must
            # not begin processing messages (Req 7.3).
            raise SpotsError(
                "REDIS_URL is absent or empty; cannot resolve the "
                "available-spots counter connection target"
            )

        self._redis_url = target
        self._max_retries = max(1, max_retries)
        # ``socket_timeout`` bounds each DECR round trip so the operation stays
        # within the 500 ms budget (Req 7.1).
        self._client = client or Redis.from_url(
            target,
            socket_timeout=DECREMENT_TIMEOUT_SECONDS,
            socket_connect_timeout=DECREMENT_TIMEOUT_SECONDS,
        )

    def decrement(self) -> int | None:
        """Atomically decrement ``spots:available`` if the key exists.

        Runs a Lua script, atomic on the Redis server: when the key exists it
        is decremented and clamped at zero; when it does not (e.g. Redis
        restarted and nobody asked for the count yet) nothing is written. A
        plain ``DECR`` would create the key as ``-1``, clamped to ``0``: the
        API would then report a full lot and never rebuild the real count from
        RDS, because it only does so when the key is missing.

        Retries up to ``max_retries`` times on a Redis connection/timeout error
        with the message left unacknowledged (Req 7.6).

        Returns:
            The counter value after the decrement (``>= 0``), or ``None`` when
            the key is absent and nothing was changed.

        Raises:
            SpotsDecrementError: connection failed after all retries (Req 7.6).
            SpotsUnderflowError: the decrement went below zero and the key was
                clamped to ``0`` (Req 7.5); the net effect is zero, so the
                caller must not compensate it with an increment.
        """

        value = self._with_retries(_DECR_IF_EXISTS)
        if value is not None and value < 0:
            raise SpotsUnderflowError(value)
        return value

    def increment(self) -> int | None:
        """Atomically increment ``spots:available`` if the key exists.

        Used by the Poller to compensate a successful :meth:`decrement` whose
        RDS transaction was rolled back afterwards. If the key vanished in the
        meantime, the API's rebuild from RDS already has the right count, so
        nothing is written. Retries like :meth:`decrement`.

        Raises:
            SpotsDecrementError: connection failed after all retries.
        """

        return self._with_retries(_INCR_IF_EXISTS)

    def _with_retries(self, script: str) -> int | None:
        """Run a Lua ``script`` on ``SPOTS_KEY``, retrying connection errors (Req 7.6)."""

        for attempt in range(1, self._max_retries + 1):
            try:
                result = self._client.eval(script, 1, SPOTS_KEY)
            except (RedisConnectionError, RedisTimeoutError) as exc:
                # Transient connectivity problem: retry, leaving the SQS message
                # unacknowledged so it can be redelivered (Req 7.6).
                if attempt == self._max_retries:
                    raise SpotsDecrementError(self._max_retries, exc) from exc
                continue
            return None if result is None else int(result)
        raise AssertionError("unreachable")

    def close(self) -> None:
        """Release the underlying Redis connection (Req 16.4)."""

        try:
            self._client.close()
        except Exception:  # noqa: BLE001 - best-effort close during shutdown
            # Connection-close failures are surfaced by the Poller during
            # graceful shutdown; here we simply avoid masking the exit path.
            pass
