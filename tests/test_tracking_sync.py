"""Endpoint tests for desktop tracking synchronization."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.api.routes as route_module
from main import app


class FakeSyncDatabaseService:
    """Read-only database replacement for synchronization endpoint tests."""

    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records
        self.received_cursors: list[datetime | None] = []

    def fetch_sync_records(
        self, updated_after: datetime | None = None
    ) -> list[dict[str, object]]:
        self.received_cursors.append(updated_after)
        return self.records


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def sync_record() -> dict[str, object]:
    return {
        "tracking_id": "sync-test-123",
        "open_count": 3,
        "click_count": 1,
        "download_count": 4,
        "first_open": datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc),
        "last_open": datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc),
        "first_click": datetime(2026, 7, 1, 9, 5, tzinfo=timezone.utc),
        "last_click": datetime(2026, 7, 1, 9, 10, tzinfo=timezone.utc),
        "first_download": datetime(2026, 7, 1, 9, 12, tzinfo=timezone.utc),
        "last_download": datetime(2026, 7, 1, 9, 20, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 1, 9, 10, tzinfo=timezone.utc),
        "last_ip": "not-returned",
    }


def test_sync_without_cursor_returns_all_required_fields(
    client: TestClient,
    sync_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeSyncDatabaseService([sync_record])
    monkeypatch.setattr(route_module, "database_service", database)

    response = client.get("/api/tracking/sync")

    assert response.status_code == 200
    assert database.received_cursors == [None]
    assert len(response.json()) == 1
    assert set(response.json()[0]) == {
        "tracking_id",
        "open_count",
        "click_count",
        "download_count",
        "first_open",
        "last_open",
        "first_click",
        "last_click",
        "first_download",
        "last_download",
        "updated_at",
    }


def test_sync_cursor_is_parsed_and_normalized_to_utc(
    client: TestClient,
    sync_record: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeSyncDatabaseService([sync_record])
    monkeypatch.setattr(route_module, "database_service", database)

    response = client.get(
        "/api/tracking/sync",
        params={"updated_after": "2026-07-01T15:30:00+06:00"},
    )

    assert response.status_code == 200
    assert database.received_cursors == [
        datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)
    ]


@pytest.mark.parametrize(
    "invalid_cursor",
    ["not-a-datetime", "", "2026-13-01T09:30:00Z"],
)
def test_invalid_sync_cursor_returns_400(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    invalid_cursor: str,
) -> None:
    database = FakeSyncDatabaseService([])
    monkeypatch.setattr(route_module, "database_service", database)

    response = client.get(
        "/api/tracking/sync",
        params={"updated_after": invalid_cursor},
    )

    assert response.status_code == 400
    assert database.received_cursors == []
