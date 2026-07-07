"""Unit tests for the Attachment Mapping API."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.attachment_mapping_routes as mapping_routes
from app.services.attachment_library import AttachmentValidationError
from main import app

TRACKING_ID = "mapping-test-tracking-id"


class FakeMappingService:
    """Idempotent in-memory replacement for mapping endpoint tests."""

    def __init__(self, active_ids: set[UUID]) -> None:
        self.active_ids = active_ids
        self.mappings: set[tuple[str, UUID]] = set()
        self.calls: list[tuple[str, list[UUID], datetime]] = []

    def register_mappings(
        self,
        tracking_id: str,
        attachment_ids: list[UUID],
        created_at: datetime,
    ) -> int:
        self.calls.append((tracking_id, attachment_ids, created_at))
        if not set(attachment_ids).issubset(self.active_ids):
            raise AttachmentValidationError(
                "Every attachment_id must exist and be active."
            )

        created = 0
        for attachment_id in attachment_ids:
            mapping = (tracking_id, attachment_id)
            if mapping not in self.mappings:
                self.mappings.add(mapping)
                created += 1
        return created


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def install_fake(
    monkeypatch: pytest.MonkeyPatch,
    attachment_ids: set[UUID],
) -> FakeMappingService:
    service = FakeMappingService(attachment_ids)
    monkeypatch.setattr(mapping_routes, "attachment_service", service)
    return service


def post_mapping(client: TestClient, attachment_ids: list[str], tracking_id=TRACKING_ID):
    return client.post(
        "/api/tracking/attachments",
        json={
            "tracking_id": tracking_id,
            "attachment_ids": attachment_ids,
        },
    )


def test_registers_one_row_per_active_attachment(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment_ids = [uuid4(), uuid4(), uuid4()]
    service = install_fake(monkeypatch, set(attachment_ids))

    response = post_mapping(client, [str(value) for value in attachment_ids])

    assert response.status_code == 200
    assert response.json() == {"success": True, "created": 3}
    assert len(service.mappings) == 3


def test_existing_mappings_are_ignored(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment_ids = [uuid4(), uuid4()]
    install_fake(monkeypatch, set(attachment_ids))

    first = post_mapping(client, [str(value) for value in attachment_ids])
    second = post_mapping(client, [str(value) for value in attachment_ids])

    assert first.json()["created"] == 2
    assert second.json() == {"success": True, "created": 0}


def test_duplicate_ids_in_one_request_create_one_mapping(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachment_id = uuid4()
    service = install_fake(monkeypatch, {attachment_id})

    response = post_mapping(client, [str(attachment_id), str(attachment_id)])

    assert response.json() == {"success": True, "created": 1}
    assert len(service.calls[0][1]) == 1


def test_empty_tracking_id_returns_400(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake(monkeypatch, set())

    response = post_mapping(client, [str(uuid4())], tracking_id="   ")

    assert response.status_code == 400


def test_empty_attachment_ids_returns_400(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake(monkeypatch, set())

    response = post_mapping(client, [])

    assert response.status_code == 400


def test_invalid_attachment_uuid_returns_400(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake(monkeypatch, set())

    response = post_mapping(client, ["not-a-uuid"])

    assert response.status_code == 400


def test_missing_attachment_returns_400(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake(monkeypatch, set())

    response = post_mapping(client, [str(uuid4())])

    assert response.status_code == 400
    assert "must exist and be active" in response.json()["error"]["message"]


def test_inactive_attachment_returns_400(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inactive_id = uuid4()
    install_fake(monkeypatch, set())

    response = post_mapping(client, [str(inactive_id)])

    assert response.status_code == 400
