"""Endpoint tests for recipient click tracking."""

from dataclasses import dataclass
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import app.api.routes as route_module
from app.services.database_tracking import ClickUpdateResult
from main import app

TRACKING_ID = "7d19af31-2d65-49db-b52e-2c92b5d39b61"
DESTINATION = "https://powersoft.com/products?source=email&campaign=launch"


@dataclass(slots=True)
class FakeTrackingRecord:
    """Mutable click fields used by the endpoint-level fake database."""

    click_count: int = 0
    first_click: datetime | None = None
    last_click: datetime | None = None
    last_ip: str | None = None
    user_agent: str | None = None


class FakeDatabaseService:
    """Stateful replacement for Neon used only by click endpoint tests."""

    def __init__(self) -> None:
        self.records: dict[str, FakeTrackingRecord] = {}

    def add_record(self, tracking_id: str) -> FakeTrackingRecord:
        record = FakeTrackingRecord()
        self.records[tracking_id] = record
        return record

    def record_click(
        self,
        tracking_id: str,
        client_ip: str,
        user_agent: str,
        occurred_at: datetime,
    ) -> ClickUpdateResult | None:
        record = self.records.get(tracking_id)
        if record is None:
            return None

        record.click_count += 1
        if record.first_click is None:
            record.first_click = occurred_at
        record.last_click = occurred_at
        record.last_ip = client_ip
        record.user_agent = user_agent
        return ClickUpdateResult(
            click_count=record.click_count,
            first_click=record.first_click,
            last_click=record.last_click,
        )


@pytest.fixture
def fake_database(monkeypatch: pytest.MonkeyPatch) -> FakeDatabaseService:
    database = FakeDatabaseService()
    monkeypatch.setattr(route_module, "database_service", database)
    return database


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def send_click(client: TestClient, tracking_id: str = TRACKING_ID):
    """Send a click request without following the expected redirect."""
    return client.get(
        f"/email/click/{tracking_id}",
        params={"url": DESTINATION},
        headers={"User-Agent": "ClickTrackingTest/1.0"},
        follow_redirects=False,
    )


def test_valid_click_request(
    client: TestClient, fake_database: FakeDatabaseService
) -> None:
    record = fake_database.add_record(TRACKING_ID)

    response = send_click(client)

    assert response.status_code == 302
    assert record.click_count == 1
    assert record.first_click is not None
    assert record.last_click is not None
    assert record.last_ip == "testclient"
    assert record.user_agent == "ClickTrackingTest/1.0"


def test_invalid_tracking_id_returns_400(
    client: TestClient, fake_database: FakeDatabaseService
) -> None:
    response = send_click(client, tracking_id="invalid.id")

    assert response.status_code == 400
    assert fake_database.records == {}


def test_missing_url_returns_400(
    client: TestClient, fake_database: FakeDatabaseService
) -> None:
    fake_database.add_record(TRACKING_ID)

    response = client.get(
        f"/email/click/{TRACKING_ID}",
        follow_redirects=False,
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "invalid_url",
    ["not-a-url", "ftp://powersoft.com/file", "https://"],
)
def test_invalid_url_format_returns_400(
    client: TestClient,
    fake_database: FakeDatabaseService,
    invalid_url: str,
) -> None:
    fake_database.add_record(TRACKING_ID)

    response = client.get(
        f"/email/click/{TRACKING_ID}",
        params={"url": invalid_url},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_unknown_tracking_id_returns_404(
    client: TestClient, fake_database: FakeDatabaseService
) -> None:
    response = send_click(client)

    assert response.status_code == 404


def test_multiple_clicks_increment_click_count(
    client: TestClient, fake_database: FakeDatabaseService
) -> None:
    record = fake_database.add_record(TRACKING_ID)

    assert send_click(client).status_code == 302
    assert send_click(client).status_code == 302
    assert send_click(client).status_code == 302

    assert record.click_count == 3


def test_first_click_is_written_only_once(
    client: TestClient, fake_database: FakeDatabaseService
) -> None:
    record = fake_database.add_record(TRACKING_ID)

    send_click(client)
    original_first_click = record.first_click
    send_click(client)

    assert original_first_click is not None
    assert record.first_click == original_first_click


def test_last_click_updates_on_every_click(
    client: TestClient, fake_database: FakeDatabaseService
) -> None:
    record = fake_database.add_record(TRACKING_ID)

    send_click(client)
    first_last_click = record.last_click
    send_click(client)

    assert first_last_click is not None
    assert record.last_click is not None
    assert record.last_click > first_last_click


def test_redirect_location_matches_original_url(
    client: TestClient, fake_database: FakeDatabaseService
) -> None:
    fake_database.add_record(TRACKING_ID)

    response = send_click(client)

    assert response.status_code == 302
    assert response.headers["location"] == DESTINATION
