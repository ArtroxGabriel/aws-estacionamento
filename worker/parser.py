"""SQS message parsing and validation for the Python OCR Worker.

Shared contract (from the Go API, see design.md "SQS Session_Message"):
the SQS message body is a bare JSON object ``{"session_id", "s3_key"}`` with
no SNS envelope.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

__all__ = [
    "MAX_BODY_BYTES",
    "SESSION_ID_RE",
    "MAX_S3_KEY_LEN",
    "SessionMessage",
    "ValidationResult",
    "parse_session_message",
]

# The message body is bounded by SQS at 256 KB (Req 2.1). ``session_id`` is
# exactly 32 hexadecimal characters (Req 2.5); ``s3_key`` is non-empty and at
# most 1024 characters (Req 2.6).
MAX_BODY_BYTES = 256 * 1024
SESSION_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
MAX_S3_KEY_LEN = 1024


@dataclass(frozen=True)
class SessionMessage:
    """A parsed and validated SQS ``Session_Message`` payload.

    Both fields are UTF-8 strings extracted verbatim from the JSON body
    (Req 2.2); ``session_id`` is guaranteed to be 32 hex characters and
    ``s3_key`` to be a non-empty string of at most 1024 characters.
    """

    session_id: str
    s3_key: str


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of parsing/validating a raw SQS message body.

    ``ok`` is ``True`` only when the body is a well-formed ``Session_Message``.
    On success, ``message`` holds the parsed payload and ``reason`` is ``None``.
    On failure (poison/invalid), ``message`` is ``None`` and ``reason`` carries
    a specific, human-readable classification cause; the caller retains the
    original, unmodified body (Req 2.3-2.6).
    """

    ok: bool
    message: SessionMessage | None
    reason: str | None


def parse_session_message(body: str) -> ValidationResult:
    """Parse and validate a raw SQS message body as a ``Session_Message``.

    The body is parsed as JSON exactly as delivered, without unwrapping any
    SNS envelope (Req 2.1). On success the ``session_id`` and ``s3_key`` field
    values are extracted as UTF-8 strings (Req 2.2). Every body that is not a
    well-formed ``Session_Message`` is classified as poison/invalid with a
    specific ``reason`` while leaving ``body`` unmodified (Req 2.3-2.6).

    This function never raises for a malformed body; domain outcomes are
    reported through the returned :class:`ValidationResult`.
    """
    # Req 2.3: invalid JSON is a poison message with a malformed-JSON reason.
    try:
        parsed = json.loads(body)
    except ValueError, TypeError:
        return ValidationResult(ok=False, message=None, reason="malformed JSON")

    # A bare JSON object is required; anything else (array, string, number,
    # null, SNS envelope treated as a plain object without the expected fields)
    # cannot carry the Session_Message fields (Req 2.1, 2.4).
    if not isinstance(parsed, dict):
        return ValidationResult(ok=False, message=None, reason="body is not a JSON object")

    # Req 2.2 / 2.4: extract session_id and s3_key as UTF-8 strings, rejecting
    # missing, non-string, or empty values with a field-specific reason.
    session_id = parsed.get("session_id")
    if session_id is None:
        return ValidationResult(ok=False, message=None, reason="missing session_id")
    if not isinstance(session_id, str):
        return ValidationResult(ok=False, message=None, reason="session_id is not a string")
    if session_id == "":
        return ValidationResult(ok=False, message=None, reason="empty session_id")

    s3_key = parsed.get("s3_key")
    if s3_key is None:
        return ValidationResult(ok=False, message=None, reason="missing s3_key")
    if not isinstance(s3_key, str):
        return ValidationResult(ok=False, message=None, reason="s3_key is not a string")
    if s3_key == "":
        return ValidationResult(ok=False, message=None, reason="empty s3_key")

    # Req 2.5: session_id must be exactly 32 hexadecimal characters.
    if not SESSION_ID_RE.fullmatch(session_id):
        return ValidationResult(ok=False, message=None, reason="invalid session_id format")

    # Req 2.6: s3_key must not exceed 1024 characters (emptiness handled above).
    if len(s3_key) > MAX_S3_KEY_LEN:
        return ValidationResult(ok=False, message=None, reason="invalid s3_key format")

    return ValidationResult(
        ok=True,
        message=SessionMessage(session_id=session_id, s3_key=s3_key),
        reason=None,
    )
