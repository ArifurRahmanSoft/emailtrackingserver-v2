"""Endpoint and storage tests for the Attachment Library."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.attachment_routes as attachment_routes
from app.services.attachment_library import (
    MAX_ATTACHMENT_SIZE,
    AttachmentLibraryService,
    AttachmentNotFoundError,
    AttachmentTooLargeError,
    DuplicateAttachmentError,
)
from main import app


class FakeAttachmentService:
    """Stateful attachment service used without PostgreSQL or production files."""

    def __init__(self) -> None:
        self.items: dict[UUID, SimpleNamespace] = {}

    def upload(
        self,
        source,
        original_file_name: str,
        content_type: str | None,
    ) -> SimpleNamespace:
        if any(
            item.original_file_name == original_file_name and item.is_active
            for item in self.items.values()
        ):
            raise DuplicateAttachmentError(
                "An active attachment with this original filename already exists."
            )
        content = source.read()
        if len(content) > MAX_ATTACHMENT_SIZE:
            raise AttachmentTooLargeError(
                "Attachment exceeds the maximum upload size of 50 MB."
            )
        item = SimpleNamespace(
            attachment_id=uuid4(),
            original_file_name=original_file_name,
            stored_file_name=f"{uuid4().hex}.bin",
            content_type=content_type or "application/octet-stream",
            file_size=len(content),
            uploaded_at=datetime.now(timezone.utc),
            is_active=True,
        )
        self.items[item.attachment_id] = item
        return item

    def list_active(self) -> list[SimpleNamespace]:
        return sorted(
            (item for item in self.items.values() if item.is_active),
            key=lambda item: item.uploaded_at,
            reverse=True,
        )

    def deactivate(self, attachment_id: UUID) -> SimpleNamespace:
        item = self.items.get(attachment_id)
        if item is None or not item.is_active:
            raise AttachmentNotFoundError("Active attachment not found.")
        item.is_active = False
        return item


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> FakeAttachmentService:
    service = FakeAttachmentService()
    monkeypatch.setattr(attachment_routes, "attachment_service", service)
    return service


def upload_file(client: TestClient, name: str = "proposal.pdf"):
    return client.post(
        "/api/attachments/upload",
        files={"file": (name, b"attachment contents", "application/pdf")},
    )


def test_upload_returns_required_metadata(
    client: TestClient, fake_service: FakeAttachmentService
) -> None:
    response = upload_file(client)

    assert response.status_code == 201
    payload = response.json()
    assert set(payload) == {
        "attachment_id",
        "original_file_name",
        "file_size",
        "uploaded_at",
    }
    assert payload["original_file_name"] == "proposal.pdf"
    assert payload["file_size"] == len(b"attachment contents")


def test_duplicate_active_filename_is_rejected(
    client: TestClient, fake_service: FakeAttachmentService
) -> None:
    assert upload_file(client).status_code == 201

    response = upload_file(client)

    assert response.status_code == 409
    assert "already exists" in response.json()["error"]["message"]


def test_list_returns_only_active_items_newest_first(
    client: TestClient, fake_service: FakeAttachmentService
) -> None:
    older = fake_service.upload(BytesIO(b"old"), "older.txt", "text/plain")
    newer = fake_service.upload(BytesIO(b"new"), "newer.txt", "text/plain")
    older.uploaded_at = datetime.now(timezone.utc) - timedelta(days=1)
    newer.uploaded_at = datetime.now(timezone.utc)
    older.is_active = False

    response = client.get("/api/attachments/list")

    assert response.status_code == 200
    assert [item["original_file_name"] for item in response.json()] == ["newer.txt"]


def test_delete_soft_deactivates_and_hides_attachment(
    client: TestClient, fake_service: FakeAttachmentService
) -> None:
    item = fake_service.upload(BytesIO(b"keep me"), "archive.zip", "application/zip")

    response = client.delete(f"/api/attachments/{item.attachment_id}")

    assert response.status_code == 200
    assert item.is_active is False
    assert client.get("/api/attachments/list").json() == []


def test_delete_unknown_attachment_returns_404(
    client: TestClient, fake_service: FakeAttachmentService
) -> None:
    response = client.delete(f"/api/attachments/{uuid4()}")

    assert response.status_code == 404


class OversizeStream:
    """Generate a payload over the limit without retaining 50 MB in memory."""

    def __init__(self) -> None:
        self.remaining = MAX_ATTACHMENT_SIZE + 1

    def seek(self, _: int) -> None:
        self.remaining = MAX_ATTACHMENT_SIZE + 1

    def read(self, size: int) -> bytes:
        if self.remaining <= 0:
            return b""
        count = min(size, self.remaining)
        self.remaining -= count
        return b"x" * count


def test_storage_rejects_oversize_and_removes_partial_file(tmp_path: Path) -> None:
    service = AttachmentLibraryService(None, tmp_path)

    with pytest.raises(AttachmentTooLargeError):
        service._write_unique_file(
            OversizeStream(),
            uuid4(),
            "oversize.bin",
        )

    assert list(tmp_path.iterdir()) == []


def test_unique_stored_names_never_overwrite(tmp_path: Path) -> None:
    service = AttachmentLibraryService(None, tmp_path)
    attachment_id = uuid4()

    first_path, _ = service._write_unique_file(
        BytesIO(b"first"), attachment_id, "same.txt"
    )
    second_path, _ = service._write_unique_file(
        BytesIO(b"second"), attachment_id, "same.txt"
    )

    assert first_path != second_path
    assert first_path.read_bytes() == b"first"
    assert second_path.read_bytes() == b"second"
