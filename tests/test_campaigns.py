"""Tests for Version 2 independent campaign management APIs."""

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.campaign_routes as campaign_route_module
from app.models.campaign import Campaign, CampaignBase
from app.models.email_tracking import Base, EmailTracking
from app.services.campaigns import (
    CampaignNotFoundError,
    CampaignService,
    DuplicateCampaignCodeError,
)
from main import app


def build_campaign_service() -> tuple[CampaignService, sessionmaker]:
    """Create an isolated campaign service backed by shared in-memory SQLite."""
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CampaignBase.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = CampaignService(None)
    service._engine = engine
    service._session_factory = session_factory
    return service, session_factory


def campaign_payload(**overrides):
    """Return a complete valid campaign payload with optional overrides."""
    payload = {
        "campaign_name": "Test Campaign",
        "campaign_code": "C021",
        "start_date": "2026-08-20",
        "end_date": "2026-08-30",
        "file_name": "campaign.xlsx",
        "client_name": "ABC Client",
        "campaign_offer": "20% discount",
    }
    payload.update(overrides)
    return payload


def test_campaigns_table_has_only_required_campaign_columns() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    CampaignBase.metadata.create_all(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("campaigns")}
    engine.dispose()

    assert columns == {
        "id",
        "campaign_name",
        "campaign_code",
        "start_date",
        "end_date",
        "file_name",
        "client_name",
        "campaign_offer",
        "created_at",
        "updated_at",
    }


def test_create_campaign() -> None:
    service, _ = build_campaign_service()
    created_at = datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc)

    campaign = service.create_campaign(
        campaign_name="Test Campaign",
        campaign_code="C021",
        start_date=date(2026, 8, 20),
        end_date=date(2026, 8, 30),
        file_name="campaign.xlsx",
        client_name="ABC Client",
        campaign_offer="20% discount",
        created_at=created_at,
    )

    assert isinstance(campaign.id, UUID)
    assert campaign.campaign_name == "Test Campaign"
    assert campaign.campaign_code == "C021"
    assert campaign.start_date == date(2026, 8, 20)
    assert campaign.end_date == date(2026, 8, 30)
    assert campaign.file_name == "campaign.xlsx"
    assert campaign.client_name == "ABC Client"
    assert campaign.campaign_offer == "20% discount"


def test_create_campaign_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.post("/api/campaigns", json=campaign_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["campaign"]["campaign_name"] == "Test Campaign"
    assert body["campaign"]["campaign_code"] == "C021"


def test_missing_campaign_name_returns_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    payload = campaign_payload()
    payload.pop("campaign_name")
    response = client.post("/api/campaigns", json=payload)

    assert response.status_code == 422


def test_missing_campaign_code_returns_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    payload = campaign_payload()
    payload.pop("campaign_code")
    response = client.post("/api/campaigns", json=payload)

    assert response.status_code == 422


def test_empty_campaign_name_returns_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.post(
        "/api/campaigns", json=campaign_payload(campaign_name="   ")
    )

    assert response.status_code == 400


def test_empty_campaign_code_returns_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.post(
        "/api/campaigns", json=campaign_payload(campaign_code="   ")
    )

    assert response.status_code == 400


def test_duplicate_campaign_code_returns_http_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    service.create_campaign("Existing", "C021")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.post("/api/campaigns", json=campaign_payload())

    assert response.status_code == 409


def test_get_all_campaigns_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    service.create_campaign(
        "Old Campaign",
        "C020",
        created_at=datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc),
    )
    service.create_campaign(
        "New Campaign",
        "C021",
        created_at=datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns")

    assert response.status_code == 200
    assert [item["campaign_code"] for item in response.json()] == ["C021", "C020"]


def test_get_one_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = build_campaign_service()
    campaign = service.create_campaign("Test Campaign", "C021")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get(f"/api/campaigns/{campaign.id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(campaign.id)
    assert response.json()["campaign_code"] == "C021"


def test_unknown_campaign_id_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get(f"/api/campaigns/{uuid4()}")

    assert response.status_code == 404


def test_update_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    service, session_factory = build_campaign_service()
    created_at = datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc)
    campaign = service.create_campaign("Old Campaign", "C020", created_at=created_at)
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.put(
        f"/api/campaigns/{campaign.id}",
        json=campaign_payload(campaign_name="Updated Campaign", campaign_code="C021"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["campaign"]["campaign_name"] == "Updated Campaign"
    assert body["campaign"]["campaign_code"] == "C021"
    with session_factory() as session:
        updated = session.get(Campaign, campaign.id)
    assert updated is not None
    assert updated.created_at.replace(tzinfo=None) == created_at.replace(tzinfo=None)


def test_update_duplicate_campaign_code_returns_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    service.create_campaign("First", "C020")
    second = service.create_campaign("Second", "C021")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.put(
        f"/api/campaigns/{second.id}",
        json=campaign_payload(campaign_name="Second", campaign_code="C020"),
    )

    assert response.status_code == 409


def test_delete_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    service, session_factory = build_campaign_service()
    campaign = service.create_campaign("Test Campaign", "C021")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.delete(f"/api/campaigns/{campaign.id}")

    assert response.status_code == 200
    assert response.json()["success"] is True
    with session_factory() as session:
        assert session.get(Campaign, campaign.id) is None


def test_delete_unknown_campaign_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.delete(f"/api/campaigns/{uuid4()}")

    assert response.status_code == 404


def test_valid_date_range() -> None:
    service, _ = build_campaign_service()

    campaign = service.create_campaign(
        "Date Campaign",
        "C021",
        start_date=date(2026, 8, 20),
        end_date=date(2026, 8, 30),
    )

    assert campaign.start_date == date(2026, 8, 20)
    assert campaign.end_date == date(2026, 8, 30)


def test_invalid_date_range_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.post(
        "/api/campaigns",
        json=campaign_payload(start_date="2026-08-30", end_date="2026-08-20"),
    )

    assert response.status_code == 400


def test_optional_fields_can_be_null(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.post(
        "/api/campaigns",
        json={
            "campaign_name": "Minimal Campaign",
            "campaign_code": "C021",
            "start_date": None,
            "end_date": None,
            "file_name": None,
            "client_name": None,
            "campaign_offer": None,
        },
    )

    assert response.status_code == 201
    campaign = response.json()["campaign"]
    assert campaign["start_date"] is None
    assert campaign["end_date"] is None
    assert campaign["file_name"] is None
    assert campaign["client_name"] is None
    assert campaign["campaign_offer"] is None


def test_existing_email_tracking_table_is_unchanged_by_campaign_metadata() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    before_columns = {
        column["name"] for column in inspect(engine).get_columns("email_tracking")
    }

    CampaignBase.metadata.create_all(engine)

    after_columns = {
        column["name"] for column in inspect(engine).get_columns("email_tracking")
    }
    engine.dispose()
    assert after_columns == before_columns


def test_existing_tracking_data_remains_unchanged_after_campaign_crud() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    CampaignBase.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = CampaignService(None)
    service._engine = engine
    service._session_factory = session_factory

    with session_factory() as session:
        tracking = EmailTracking(
            tracking_id="tracking-001",
            recipient_email="recipient@example.com",
            sender_email="sender@example.com",
            open_count=7,
            click_count=2,
        )
        session.add(tracking)
        session.commit()
        before = (
            tracking.id,
            tracking.tracking_id,
            tracking.recipient_email,
            tracking.sender_email,
            tracking.open_count,
            tracking.click_count,
        )

    campaign = service.create_campaign("Test Campaign", "C021")
    service.update_campaign(campaign.id, "Updated Campaign", "C022")
    service.delete_campaign(campaign.id)

    with session_factory() as session:
        tracking_after = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == "tracking-001")
        )
        campaign_count = len(session.scalars(select(Campaign)).all())

    assert tracking_after is not None
    assert (
        tracking_after.id,
        tracking_after.tracking_id,
        tracking_after.recipient_email,
        tracking_after.sender_email,
        tracking_after.open_count,
        tracking_after.click_count,
    ) == before
    assert campaign_count == 0


def test_service_unknown_campaign_errors_are_explicit() -> None:
    service, _ = build_campaign_service()

    with pytest.raises(CampaignNotFoundError):
        service.get_campaign(uuid4())

    with pytest.raises(CampaignNotFoundError):
        service.delete_campaign(uuid4())


def test_service_duplicate_campaign_code_error_is_explicit() -> None:
    service, _ = build_campaign_service()
    service.create_campaign("First", "C021")

    with pytest.raises(DuplicateCampaignCodeError):
        service.create_campaign("Duplicate", "C021")
