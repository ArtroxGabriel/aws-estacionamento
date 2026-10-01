"""Tests for SQS message parsing and validation."""

from __future__ import annotations

import pytest

from parser import (
    MAX_S3_KEY_LEN,
    SessionMessage,
    parse_session_message,
)


def test_parse_valid_session_message():
    session_id = "0123456789abcdef0123456789abcdef"
    s3_key = "photos/car.jpg"
    body = f'{{"session_id": "{session_id}", "s3_key": "{s3_key}"}}'

    result = parse_session_message(body)

    assert result.ok is True
    assert result.reason is None
    assert result.message == SessionMessage(session_id=session_id, s3_key=s3_key)


@pytest.mark.parametrize(
    ("body", "expected_reason"),
    [
        ("invalid json", "malformed JSON"),
        ('["array", "not", "object"]', "body is not a JSON object"),
        ('{"s3_key": "photos/car.jpg"}', "missing session_id"),
        ('{"session_id": 12345, "s3_key": "photos/car.jpg"}', "session_id is not a string"),
        ('{"session_id": "", "s3_key": "photos/car.jpg"}', "empty session_id"),
        ('{"session_id": "invalid-hex", "s3_key": "photos/car.jpg"}', "invalid session_id format"),
        ('{"session_id": "0123456789abcdef0123456789abcdef"}', "missing s3_key"),
        (
            '{"session_id": "0123456789abcdef0123456789abcdef", "s3_key": 123}',
            "s3_key is not a string",
        ),
        ('{"session_id": "0123456789abcdef0123456789abcdef", "s3_key": ""}', "empty s3_key"),
        (
            '{"session_id": "0123456789abcdef0123456789abcdef", "s3_key": "'
            + "a" * (MAX_S3_KEY_LEN + 1)
            + '"}',
            "invalid s3_key format",
        ),
    ],
)
def test_parse_invalid_session_message(body: str, expected_reason: str):
    result = parse_session_message(body)

    assert result.ok is False
    assert result.message is None
    assert result.reason == expected_reason
