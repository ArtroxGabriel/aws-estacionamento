"""Audit_Logger — immutable audit trail in Amazon DynamoDB.

The Audit_Logger (Req 8, Req 12.1) writes one item per state change to the
``AuditoriaEstacionamento`` table using the *exact* item shape the Go API
produces (``api/internal/repository/dynamodb.go``). The contract MUST NOT drift:

| Field       | Type | Value                                     |
|-------------|------|-------------------------------------------|
| ``id``      | S    | ``<session_id>#<timestamp_nano>``         |
| ``action``  | S    | ``OCR_PROCESSING`` / poison classification|
| ``entity_id`` | S  | ``session_id``                            |
| ``timestamp`` | S  | RFC3339 with nanosecond precision         |
| ``details`` | M    | ``{ license_plate, session_id }`` / reason|

The injected boto3 client is the *low-level* DynamoDB client (as built by
``storage.clients.dynamodb_client``), so items are expressed directly as
AttributeValue maps (``{"S": ...}``, ``{"M": ...}``) matching the Go SDK
marshalling.

Writes are idempotent: each ``PutItem`` is guarded by
``attribute_not_exists(id)`` so a redelivered message can never overwrite an
existing Audit_Entry (Req 8.4). Transient write failures are retried up to
three attempts; exhausting them raises :class:`AuditWriteError` identifying the
affected ``session_id`` without touching the Session_Record (Req 8.5).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

__all__ = [
    "AuditLogger",
    "AuditError",
    "AuditWriteError",
    "ACTION_OCR_PROCESSING",
    "ACTION_OCR_FAILED",
    "ACTION_POISON_MESSAGE",
    "MAX_RETRIES",
]

# Audit actions — shared contract with the Go API. ``OCR_PROCESSING`` matches
# the action the worker records on a successful PROCESSING -> PARKED transition
# (Req 8.1). ``POISON_MESSAGE`` labels a poison classification record (Req 12.1).
ACTION_OCR_PROCESSING = "OCR_PROCESSING"
ACTION_OCR_FAILED = "OCR_FAILED"
ACTION_POISON_MESSAGE = "POISON_MESSAGE"

# Retry each PutItem up to three attempts on a transient failure (Req 8.5).
MAX_RETRIES = 3

# DynamoDB error codes that signal the conditional guard rejected the write
# because the ``id`` already exists (Req 8.4). This is a success from the
# idempotency standpoint: the existing Audit_Entry is preserved unchanged.
_CONDITION_FAILED_CODES = frozenset({"ConditionalCheckFailedException"})


class AuditError(Exception):
    """Base error for Audit_Logger operations."""


class AuditWriteError(AuditError):
    """Signals an Audit_Entry write failed after exhausting retries (Req 8.5).

    Carries the affected ``session_id`` so the Poller can log an error entry
    identifying the session. The write failing does NOT alter the
    Session_Record; the transition has already committed and the message is
    left for the Poller to handle per its retry policy.
    """

    def __init__(self, session_id: str, attempts: int, cause: Exception | None = None) -> None:
        self.session_id = session_id
        self.attempts = attempts
        self.__cause__ = cause
        super().__init__(
            f"failed to write audit entry for session {session_id!r} after {attempts} attempt(s)"
        )


class AuditLogger:
    """Writes immutable audit entries to the ``AuditoriaEstacionamento`` table.

    The DynamoDB table name is read from the ``DYNAMODB_TABLE_NAME`` environment
    variable at construction (supplied here via ``table_name``, sourced from
    :class:`worker.config.Config`). An absent/empty table name fails
    initialization so the polling loop never begins without a target table
    (Req 8.6).
    """

    def __init__(
        self,
        dynamodb_client: Any,
        table_name: str,
        *,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        name = (table_name or "").strip()
        if not name:
            # Fail fast at startup — no table name means the loop must not begin
            # (Req 8.6). The message identifies the missing configuration.
            raise AuditError(
                "DYNAMODB_TABLE_NAME is unset or empty; cannot resolve the audit table name"
            )

        self._client = dynamodb_client
        self._table = name
        self._max_retries = max(1, max_retries)

    def log_ocr(self, session_id: str, plate: str) -> None:
        """Record one ``OCR_PROCESSING`` Audit_Entry for a parked session.

        Writes an item whose ``id`` is ``<session_id>#<timestamp_nano>``,
        ``action`` is ``OCR_PROCESSING``, ``entity_id`` is ``session_id``,
        ``timestamp`` is RFC3339 with nanosecond precision, and ``details``
        carries the identified ``license_plate`` and ``session_id`` (Req 8.1,
        8.2, 8.3). The write is conditional on ``attribute_not_exists(id)`` and
        retried up to three attempts (Req 8.4, 8.5).

        Raises:
            AuditWriteError: the write failed after all retries (Req 8.5).
        """

        self._put(
            session_id,
            ACTION_OCR_PROCESSING,
            {
                "license_plate": {"S": plate},
                "session_id": {"S": session_id},
            },
        )

    def log_poison(self, session_id: str, reason: str) -> None:
        """Record a Poison_Message classification Audit_Entry (Req 12.1).

        Writes an item labelled ``POISON_MESSAGE`` carrying the failing
        ``session_id`` and the classification ``reason`` in ``details``, using
        the same idempotent, retried write path as :meth:`log_ocr`.

        Raises:
            AuditWriteError: the write failed after all retries (Req 8.5).
        """

        self._put(
            session_id,
            ACTION_POISON_MESSAGE,
            {
                "session_id": {"S": session_id},
                "reason": {"S": reason},
            },
        )

    def log_failed(self, session_id: str, reason: str) -> None:
        """Record a session failure Audit_Entry (Req 8).

        Writes an item labelled ``OCR_FAILED`` carrying the failing
        ``session_id``, ``status='FAILED'``, and ``reason`` in ``details``, using
        the same idempotent, retried write path as :meth:`log_ocr`.

        Raises:
            AuditWriteError: the write failed after all retries (Req 8.5).
        """

        self._put(
            session_id,
            ACTION_OCR_FAILED,
            {
                "session_id": {"S": session_id},
                "status": {"S": "FAILED"},
                "reason": {"S": reason},
            },
        )

    def _put(self, session_id: str, action: str, details: dict[str, Any]) -> None:
        """PutItem the audit record, idempotent and retried (Req 8.4, 8.5).

        Builds the AttributeValue item with a unique ``id`` per attempt so a
        transient retry does not collide with itself, and guards each write with
        ``attribute_not_exists(id)`` so a redelivered message never overwrites an
        existing entry (Req 8.4). A conditional-check failure is treated as
        success: the existing entry is preserved unchanged.
        """

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            timestamp_nano = time.time_ns()
            item = {
                "id": {"S": f"{session_id}#{timestamp_nano}"},
                "action": {"S": action},
                "entity_id": {"S": session_id},
                "timestamp": {"S": _rfc3339_nano(timestamp_nano)},
                "details": {"M": details},
            }
            try:
                self._client.put_item(
                    TableName=self._table,
                    Item=item,
                    ConditionExpression="attribute_not_exists(id)",
                )
                return
            except ClientError as exc:
                if _is_condition_failed(exc):
                    # The id already exists: the entry is preserved unchanged.
                    # This is the idempotent no-op success path (Req 8.4).
                    return
                last_error = exc
            except BotoCoreError as exc:
                # Transient client/network failure: retry (Req 8.5).
                last_error = exc

            if attempt < self._max_retries:
                continue
            raise AuditWriteError(session_id, self._max_retries, last_error)

        # Unreachable: the loop returns, no-ops on conditional failure, or raises
        # on the final attempt. Kept for exhaustiveness.
        raise AuditWriteError(session_id, self._max_retries, last_error)


def _rfc3339_nano(timestamp_nano: int) -> str:
    """Format a Unix nanosecond timestamp as RFC3339 with nanosecond precision.

    Matches the Go API's ``time.RFC3339Nano`` output shape
    (``2006-01-02T15:04:05.999999999Z07:00``) so the ``timestamp`` field is a
    consistent contract across producers. ``datetime`` only offers microsecond
    resolution, so the sub-second fraction is composed directly from the
    nanosecond remainder to preserve full precision.
    """

    seconds, nanos = divmod(timestamp_nano, 1_000_000_000)
    moment = datetime.fromtimestamp(seconds, tz=UTC)
    base = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanos:09d}Z"


def _is_condition_failed(error: ClientError) -> bool:
    """True when a boto3 error denotes a failed ``attribute_not_exists`` guard."""

    code = str(error.response.get("Error", {}).get("Code", ""))
    return code in _CONDITION_FAILED_CODES
