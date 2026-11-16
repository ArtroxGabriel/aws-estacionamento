"""Audit_Logger item shapes — the contract read by the API's GET /audit."""

from __future__ import annotations

from fakes import SESSION_ID

from storage.audit import AuditLogger


class FakeDynamo:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def put_item(self, TableName: str, Item: dict, ConditionExpression: str) -> None:  # noqa: N803
        assert ConditionExpression == "attribute_not_exists(id)"
        self.items.append(Item)


def logged(action):
    client = FakeDynamo()
    action(AuditLogger(client, "AuditoriaEstacionamento"))
    (item,) = client.items
    assert item["entity_id"] == {"S": SESSION_ID}
    assert item["id"]["S"].startswith(f"{SESSION_ID}#")
    return item


def test_plate_read_is_audited_as_ocr_processing():
    item = logged(lambda audit: audit.log_ocr(SESSION_ID, "ABC1D23"))

    assert item["action"] == {"S": "OCR_PROCESSING"}
    assert item["details"]["M"] == {
        "license_plate": {"S": "ABC1D23"},
        "session_id": {"S": SESSION_ID},
    }


def test_unreadable_photo_is_audited_without_failing_the_session():
    """The entry says the *plate* is UNREADABLE. It must not claim the session
    is FAILED: that status no longer exists and the ticket is still valid."""
    item = logged(lambda audit: audit.log_failed(SESSION_ID, "unreadable plate: no_match"))

    assert item["action"] == {"S": "OCR_FAILED"}
    details = item["details"]["M"]
    assert details == {
        "session_id": {"S": SESSION_ID},
        "plate_status": {"S": "UNREADABLE"},
        "reason": {"S": "unreadable plate: no_match"},
    }
    assert "status" not in details
