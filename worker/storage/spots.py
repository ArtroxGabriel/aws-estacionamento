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

    def decrement(self) -> int:
        """Atomically ``DECR spots:available`` and return the resulting count.

        Retries up to ``max_retries`` times on a Redis connection/timeout error
        with the message left unacknowledged (Req 7.6). When the DECR drives the
        counter below zero, the key is reset to ``0`` and a
        :class:`SpotsUnderflowError` is emitted (Req 7.5). The DECR is atomic on
        the single-node Redis server, so no distributed lock is required.

        Returns:
            The clamped counter value (always ``>= 0``).

        Raises:
            SpotsDecrementError: connection failed after all retries (Req 7.6).
            SpotsUnderflowError: the decrement underflowed and was clamped
                to zero (Req 7.5).
        """

        value = self._with_retries(self._client.decr)

        if value < 0:
            # Underflow: restore the invariant (>= 0) then signal the
            # condition. The net effect on the counter is zero, so the caller
            # must not compensate it with an increment (Req 7.5).
            self._client.set(SPOTS_KEY, 0)
            raise SpotsUnderflowError(value)

        return value

    def increment(self) -> int:
        """Atomically ``INCR spots:available`` and return the resulting count.

        Used by the Poller to compensate a successful :meth:`decrement` whose
        RDS transaction was rolled back afterwards, so the counter never drifts
        from the committed session state. Retries like :meth:`decrement`.

        Raises:
            SpotsDecrementError: connection failed after all retries.
        """

        return self._with_retries(self._client.incr)

    def _with_retries(self, op) -> int:
        """Run ``op(SPOTS_KEY)``, retrying on connection/timeout errors (Req 7.6)."""

        for attempt in range(1, self._max_retries + 1):
            try:
                return int(op(SPOTS_KEY))
            except (RedisConnectionError, RedisTimeoutError) as exc:
                # Transient connectivity problem: retry, leaving the SQS message
                # unacknowledged so it can be redelivered (Req 7.6).
                if attempt == self._max_retries:
                    raise SpotsDecrementError(self._max_retries, exc) from exc
        raise AssertionError("unreachable")

    def close(self) -> None:
        """Release the underlying Redis connection (Req 16.4)."""

        try:
            self._client.close()
        except Exception:  # noqa: BLE001 - best-effort close during shutdown
            # Connection-close failures are surfaced by the Poller during
            # graceful shutdown; here we simply avoid masking the exit path.
            pass
