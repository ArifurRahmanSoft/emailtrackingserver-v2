"""Unit tests for tracked attachment downloads."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.attachment_download_routes as download_routes
from app.services.attachment_library import (
    AttachmentDownloadResult,
    AttachmentNotFoundError,
)
from main import app

TRACKING_ID = "download-test-tracking-id"


@dataclass(slots=True)
class FakeDownloadAttachment:
    attachment_id: UUID
    file_bytes: bytes
    original_file_name: str
    content_type: str
    is_active: bool = True


@dataclass(slots=True)
class FakeCounter:
    download_count: int = 0
    first_download: datetime | None = None
    last_download: datetime | None = None


class FakeDownloadService:
    """Stateful transaction replacement for endpoint behavior tests."""

    def __init__(self) -> None:
        self.attachments: dict[UUID, FakeDownloadAttachment] = {}
        self.counters: dict[tuple[str, UUID], FakeCounter] = {}

    def add_attachment(
        self,
        file_path: Path,
        *,
        active: bool = True,
        mapped: bool = True,
    ) -> FakeDownloadAttachment:
        attachment = FakeDownloadAttachment(
            attachment_id=uuid4(),
            file_bytes=file_path.read_bytes(),
            original_file_name="customer-guide.pdf",
            content_type="application/pdf",
            is_active=active,
        )
        self.attachments[attachment.attachment_id] = attachment
        if mapped:
            self.counters[(TRACKING_ID, attachment.attachment_id)] = FakeCounter()
        return attachment

    def track_download(
        self,
        tracking_id: str,
        attachment_id: UUID,
        downloaded_at: datetime,
    ) -> AttachmentDownloadResult:
        counter = self.counters.get((tracking_id, attachment_id))
        if counter is None:
            raise AttachmentNotFoundError("Attachment mapping not found.")
        attachment = self.attachments.get(attachment_id)
        if attachment is None or not attachment.is_active:
            raise AttachmentNotFoundError("Active attachment not found.")

        counter.download_count += 1
        if counter.first_download is None:
            counter.first_download = downloaded_at
        counter.last_download = downloaded_at
        return AttachmentDownloadResult(
            file_bytes=attachment.file_bytes,
            original_file_name=attachment.original_file_name,
            content_type=attachment.content_type,
            download_count=counter.download_count,
            first_download=counter.first_download,
            last_download=counter.last_download,
        )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def fake_service(
    monkeypatch: pytest.MonkeyPatch,
) -> FakeDownloadService:
    service = FakeDownloadService()
    monkeypatch.setattr(download_routes, "attachment_service", service)
    return service


def make_file(tmp_path: Path) -> Path:
    file_path = tmp_path / "stored-file.bin"
    file_path.write_bytes(b"download contents")
    return file_path


def download(client: TestClient, attachment_id: UUID, tracking_id: str = TRACKING_ID):
    return client.get(f"/download/{tracking_id}/{attachment_id}")


def test_first_download_returns_original_file(
    client: TestClient, fake_service: FakeDownloadService, tmp_path: Path
) -> None:
    attachment = fake_service.add_attachment(make_file(tmp_path))

    response = download(client, attachment.attachment_id)

    assert response.status_code == 200
    assert response.content == b"download contents"
    assert response.headers["content-type"] == "application/pdf"
    assert "customer-guide.pdf" in response.headers["content-disposition"]


def test_mapping_allows_download_without_email_tracking_row(
    client: TestClient, fake_service: FakeDownloadService, tmp_path: Path
) -> None:
    attachment = fake_service.add_attachment(make_file(tmp_path))

    response = download(client, attachment.attachment_id)

    assert response.status_code == 200
    assert fake_service.counters[
        (TRACKING_ID, attachment.attachment_id)
    ].download_count == 1


def test_first_download_creates_count_and_timestamps(
    client: TestClient, fake_service: FakeDownloadService, tmp_path: Path
) -> None:
    attachment = fake_service.add_attachment(make_file(tmp_path))

    assert download(client, attachment.attachment_id).status_code == 200
    counter = fake_service.counters[(TRACKING_ID, attachment.attachment_id)]

    assert counter.download_count == 1
    assert counter.first_download is not None
    assert counter.last_download == counter.first_download
    assert counter.first_download.tzinfo == timezone.utc


def test_multiple_downloads_increment_count(
    client: TestClient, fake_service: FakeDownloadService, tmp_path: Path
) -> None:
    attachment = fake_service.add_attachment(make_file(tmp_path))

    download(client, attachment.attachment_id)
    download(client, attachment.attachment_id)
    download(client, attachment.attachment_id)

    assert fake_service.counters[
        (TRACKING_ID, attachment.attachment_id)
    ].download_count == 3


def test_first_download_timestamp_is_written_only_once(
    client: TestClient, fake_service: FakeDownloadService, tmp_path: Path
) -> None:
    attachment = fake_service.add_attachment(make_file(tmp_path))

    download(client, attachment.attachment_id)
    first_download = fake_service.counters[
        (TRACKING_ID, attachment.attachment_id)
    ].first_download
    download(client, attachment.attachment_id)

    assert fake_service.counters[
        (TRACKING_ID, attachment.attachment_id)
    ].first_download == first_download


def test_last_download_timestamp_updates_each_time(
    client: TestClient, fake_service: FakeDownloadService, tmp_path: Path
) -> None:
    attachment = fake_service.add_attachment(make_file(tmp_path))

    download(client, attachment.attachment_id)
    first_last_download = fake_service.counters[
        (TRACKING_ID, attachment.attachment_id)
    ].last_download
    download(client, attachment.attachment_id)

    assert first_last_download is not None
    assert fake_service.counters[
        (TRACKING_ID, attachment.attachment_id)
    ].last_download > first_last_download


def test_invalid_tracking_id_returns_404(
    client: TestClient, fake_service: FakeDownloadService, tmp_path: Path
) -> None:
    attachment = fake_service.add_attachment(make_file(tmp_path))

    response = download(client, attachment.attachment_id, "unknown-tracking-id")

    assert response.status_code == 404


def test_invalid_attachment_id_returns_404(client: TestClient) -> None:
    response = client.get(f"/download/{TRACKING_ID}/not-a-uuid")

    assert response.status_code == 404


def test_unknown_attachment_returns_404(
    client: TestClient, fake_service: FakeDownloadService
) -> None:
    unknown_id = uuid4()
    fake_service.counters[(TRACKING_ID, unknown_id)] = FakeCounter()

    assert download(client, unknown_id).status_code == 404


def test_inactive_attachment_returns_404(
    client: TestClient, fake_service: FakeDownloadService, tmp_path: Path
) -> None:
    attachment = fake_service.add_attachment(make_file(tmp_path), active=False)

    assert download(client, attachment.attachment_id).status_code == 404
