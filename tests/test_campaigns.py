"""Tests for Version 2 independent campaign management APIs."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.campaign_routes as campaign_route_module
from app.models.auth import AuthBase, SystemUser
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
    Base.metadata.create_all(engine)
    AuthBase.metadata.create_all(engine)
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
        "client_code": None,
        "campaign_offer": "20% discount",
    }
    payload.update(overrides)
    return payload


def add_system_user(session_factory: sessionmaker, user_id: str = "USER001") -> None:
    """Create one system user for client-code validation."""
    with session_factory() as session:
        session.add(SystemUser(user_id=user_id, password="123456", role="CLIENT"))
        session.commit()


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
        "client_code",
        "campaign_offer",
        "created_at",
        "updated_at",
    }


def test_client_code_migration_adds_campaigns_client_code(tmp_path) -> None:
    from app.services.alembic_migrations import run_pending_migrations

    database_path = tmp_path / "client-code-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE email_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_id VARCHAR(128) NOT NULL UNIQUE,
                    recipient_email VARCHAR(320),
                    sender_email VARCHAR(320),
                    open_count INTEGER NOT NULL DEFAULT 0,
                    click_count INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    engine.dispose()

    run_pending_migrations(database_url)

    engine = create_engine(database_url)
    columns = {column["name"] for column in inspect(engine).get_columns("campaigns")}
    engine.dispose()

    assert "client_code" in columns


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
        client_code=None,
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
    assert campaign.client_code is None
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
    assert body["campaign"]["client_code"] is None


def test_create_campaign_saves_client_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_factory = build_campaign_service()
    add_system_user(session_factory, "USER001")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.post(
        "/api/campaigns",
        json=campaign_payload(client_code="USER001"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["campaign"]["client_code"] == "USER001"
    with session_factory() as session:
        campaign = session.scalar(
            select(Campaign).where(Campaign.campaign_code == "C021")
        )
    assert campaign is not None
    assert campaign.client_code == "USER001"


def test_create_campaign_rejects_invalid_client_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.post(
        "/api/campaigns",
        json=campaign_payload(client_code="UNKNOWN"),
    )

    assert response.status_code == 400


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


def test_get_campaign_codes_returns_200_with_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/codes")

    assert response.status_code == 200
    assert response.json() == {"success": True, "campaign_codes": []}


def test_get_campaign_codes_returns_multiple_codes_sorted_ascending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    service.create_campaign("Campaign 21", "C021")
    service.create_campaign("Campaign 01", "C001")
    service.create_campaign("Campaign 10", "C010")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/codes")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "campaign_codes": ["C001", "C010", "C021"],
    }


def test_get_campaign_codes_excludes_null_empty_and_whitespace_values() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE campaigns (campaign_code VARCHAR(100))"))
        connection.execute(
            text(
                "INSERT INTO campaigns (campaign_code) VALUES "
                "(NULL), (''), ('   '), ('C001'), ('C002')"
            )
        )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = CampaignService(None)
    service._engine = engine
    service._session_factory = session_factory

    assert service.get_campaign_codes() == ["C001", "C002"]


def test_campaign_code_unique_constraint_prevents_duplicates() -> None:
    service, _ = build_campaign_service()
    service.create_campaign("First Campaign", "C001")

    with pytest.raises(DuplicateCampaignCodeError):
        service.create_campaign("Duplicate Campaign", "C001")


def test_get_campaign_codes_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    service, session_factory = build_campaign_service()
    service.create_campaign("Campaign 01", "C001")
    service.create_campaign("Campaign 02", "C002")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    with session_factory() as session:
        before = [
            (campaign.id, campaign.campaign_name, campaign.campaign_code)
            for campaign in session.scalars(select(Campaign)).all()
        ]

    response = client.get("/api/campaigns/codes")

    with session_factory() as session:
        after = [
            (campaign.id, campaign.campaign_name, campaign.campaign_code)
            for campaign in session.scalars(select(Campaign)).all()
        ]

    assert response.status_code == 200
    assert after == before


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
    assert "client_code" in response.json()


def test_get_campaign_apis_return_client_code(monkeypatch: pytest.MonkeyPatch) -> None:
    service, session_factory = build_campaign_service()
    add_system_user(session_factory, "USER001")
    campaign = service.create_campaign(
        "Test Campaign",
        "C021",
        client_code="USER001",
    )
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    list_response = client.get("/api/campaigns")
    get_response = client.get(f"/api/campaigns/{campaign.id}")

    assert list_response.status_code == 200
    assert list_response.json()[0]["client_code"] == "USER001"
    assert get_response.status_code == 200
    assert get_response.json()["client_code"] == "USER001"


def test_campaign_codes_route_is_not_interpreted_as_campaign_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    service.create_campaign("Test Campaign", "C021")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/codes")

    assert response.status_code == 200
    assert response.json()["campaign_codes"] == ["C021"]


def test_campaign_dashboard_all_campaigns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_factory = build_campaign_service()
    service.create_campaign(
        "Campaign One",
        "C001",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
        client_name="Arifur Rahman",
    )
    service.create_campaign("Campaign Two", "C002")
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                EmailTracking(
                    tracking_id="c001-001",
                    campaign_code="C001",
                    click_count=3,
                    reply_count=1,
                    is_bounce=0,
                    download_count=2,
                    open_count=0,
                    created_at=now,
                ),
                EmailTracking(
                    tracking_id="c001-002",
                    campaign_code="C001",
                    click_count=2,
                    reply_count=2,
                    is_bounce=1,
                    download_count=4,
                    open_count=0,
                    created_at=now,
                ),
                EmailTracking(
                    tracking_id="c002-001",
                    campaign_code="C002",
                    click_count=7,
                    reply_count=0,
                    is_bounce=0,
                    download_count=1,
                    open_count=0,
                    created_at=now,
                ),
            ]
        )
        session.commit()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["filter"] == {"campaign_code": None}
    assert [item["campaign_code"] for item in body["campaigns"]] == [
        "C001",
        "C002",
        "",
    ]
    c001 = body["campaigns"][0]
    assert c001["campaign_name"] == "Campaign One"
    assert c001["clint_name"] == "Arifur Rahman"
    assert c001["is_total"] is False
    assert c001["start_date"] == "2026-08-01"
    assert c001["end_date"] == "2026-08-31"
    assert c001["total_mail_sent"] == 2
    assert c001["total_click"] == 5
    assert c001["total_reply"] == 3
    assert c001["total_bounce"] == 1
    assert c001["total_download"] == 6
    assert c001["success_rate"] == 50.0
    assert c001["failure_rate"] == 50.0
    assert c001["monthly_sent"] == 2
    assert c001["weekly_sent"] == 2
    total = body["campaigns"][-1]
    assert total["is_total"] is True
    assert total["campaign_code"] == ""
    assert total["campaign_name"] == ""
    assert total["clint_name"] == ""
    assert total["start_date"] == ""
    assert total["end_date"] == ""
    assert total["total_mail_sent"] == 3
    assert total["total_click"] == 12
    assert total["total_reply"] == 3
    assert total["total_bounce"] == 1
    assert total["total_download"] == 7
    assert total["success_rate"] == 66.67
    assert total["failure_rate"] == 33.33
    assert total["monthly_sent"] == 3
    assert total["weekly_sent"] == 3


def test_campaign_dashboard_single_campaign_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_factory = build_campaign_service()
    service.create_campaign("Campaign One", "C001")
    service.create_campaign("Campaign Two", "C002")
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                EmailTracking(
                    tracking_id="c001-001",
                    campaign_code="C001",
                    click_count=1,
                    reply_count=1,
                    download_count=1,
                    is_bounce=0,
                    open_count=0,
                    created_at=now,
                ),
                EmailTracking(
                    tracking_id="c002-001",
                    campaign_code="C002",
                    click_count=9,
                    reply_count=9,
                    download_count=9,
                    is_bounce=1,
                    open_count=0,
                    created_at=now,
                ),
            ]
        )
        session.commit()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/dashboard?campaign_code=C001")

    assert response.status_code == 200
    body = response.json()
    assert body["filter"] == {"campaign_code": "C001"}
    assert len(body["campaigns"]) == 1
    assert body["campaigns"][0]["campaign_code"] == "C001"
    assert body["campaigns"][0]["is_total"] is False
    assert body["campaigns"][0]["total_click"] == 1
    assert body["campaigns"][0]["total_reply"] == 1
    assert body["campaigns"][0]["total_download"] == 1
    assert body["campaigns"][0]["total_bounce"] == 0


def test_campaign_dashboard_monthly_and_weekly_sent_use_existing_rolling_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_factory = build_campaign_service()
    service.create_campaign("Campaign One", "C001")
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                EmailTracking(
                    tracking_id="weekly",
                    campaign_code="C001",
                    created_at=now - timedelta(days=3),
                    open_count=0,
                    click_count=0,
                ),
                EmailTracking(
                    tracking_id="monthly",
                    campaign_code="C001",
                    created_at=now - timedelta(days=20),
                    open_count=0,
                    click_count=0,
                ),
                EmailTracking(
                    tracking_id="older",
                    campaign_code="C001",
                    created_at=now - timedelta(days=50),
                    open_count=0,
                    click_count=0,
                ),
            ]
        )
        session.commit()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/dashboard?campaign_code=C001")

    assert response.status_code == 200
    campaign = response.json()["campaigns"][0]
    assert campaign["total_mail_sent"] == 3
    assert campaign["monthly_sent"] == 2
    assert campaign["weekly_sent"] == 1


def test_campaign_dashboard_campaign_with_zero_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    service.create_campaign("No Tracking Campaign", "C001")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/dashboard?campaign_code=C001")

    assert response.status_code == 200
    campaign = response.json()["campaigns"][0]
    assert campaign["campaign_name"] == "No Tracking Campaign"
    assert campaign["total_mail_sent"] == 0
    assert campaign["total_click"] == 0
    assert campaign["total_reply"] == 0
    assert campaign["total_bounce"] == 0
    assert campaign["total_download"] == 0
    assert campaign["success_rate"] == 0.0
    assert campaign["failure_rate"] == 0.0
    assert campaign["monthly_sent"] == 0
    assert campaign["weekly_sent"] == 0


def test_campaign_dashboard_unknown_campaign_code_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    service.create_campaign("Campaign One", "C001")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/dashboard?campaign_code=UNKNOWN")

    assert response.status_code == 404


def test_campaign_dashboard_multiple_campaigns_remain_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, session_factory = build_campaign_service()
    service.create_campaign("Campaign One", "C001")
    service.create_campaign("Campaign Two", "C002")
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                EmailTracking(
                    tracking_id="c001",
                    campaign_code="C001",
                    click_count=1,
                    reply_count=2,
                    download_count=3,
                    is_bounce=0,
                    open_count=0,
                    created_at=now,
                ),
                EmailTracking(
                    tracking_id="c002",
                    campaign_code="C002",
                    click_count=10,
                    reply_count=20,
                    download_count=30,
                    is_bounce=1,
                    open_count=0,
                    created_at=now,
                ),
            ]
        )
        session.commit()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/dashboard")

    assert response.status_code == 200
    by_code = {item["campaign_code"]: item for item in response.json()["campaigns"]}
    assert by_code["C001"]["total_click"] == 1
    assert by_code["C001"]["total_reply"] == 2
    assert by_code["C001"]["total_download"] == 3
    assert by_code["C001"]["total_bounce"] == 0
    assert by_code["C002"]["total_click"] == 10
    assert by_code["C002"]["total_reply"] == 20
    assert by_code["C002"]["total_download"] == 30
    assert by_code["C002"]["total_bounce"] == 1
    assert by_code[""]["is_total"] is True
    assert by_code[""]["total_mail_sent"] == 2
    assert by_code[""]["total_click"] == 11
    assert by_code[""]["total_reply"] == 22
    assert by_code[""]["total_download"] == 33
    assert by_code[""]["total_bounce"] == 1


def test_campaign_dashboard_without_campaigns_returns_total_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert len(body["campaigns"]) == 1
    total = body["campaigns"][0]
    assert total["is_total"] is True
    assert total["campaign_code"] == ""
    assert total["total_mail_sent"] == 0
    assert total["total_click"] == 0
    assert total["total_reply"] == 0
    assert total["total_bounce"] == 0
    assert total["total_download"] == 0
    assert total["monthly_sent"] == 0
    assert total["weekly_sent"] == 0
    assert total["success_rate"] == 0.0
    assert total["failure_rate"] == 0.0


def test_campaign_dashboard_route_is_not_interpreted_as_campaign_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    service.create_campaign("Campaign One", "C001")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get("/api/campaigns/dashboard")

    assert response.status_code == 200
    assert response.json()["campaigns"][0]["campaign_code"] == "C001"


def test_unknown_campaign_id_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = build_campaign_service()
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.get(f"/api/campaigns/{uuid4()}")

    assert response.status_code == 404


def test_update_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    service, session_factory = build_campaign_service()
    add_system_user(session_factory, "USER002")
    created_at = datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc)
    campaign = service.create_campaign("Old Campaign", "C020", created_at=created_at)
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.put(
        f"/api/campaigns/{campaign.id}",
        json=campaign_payload(
            campaign_name="Updated Campaign",
            campaign_code="C021",
            client_code="USER002",
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["campaign"]["campaign_name"] == "Updated Campaign"
    assert body["campaign"]["campaign_code"] == "C021"
    assert body["campaign"]["client_code"] == "USER002"
    with session_factory() as session:
        updated = session.get(Campaign, campaign.id)
    assert updated is not None
    assert updated.created_at.replace(tzinfo=None) == created_at.replace(tzinfo=None)
    assert updated.client_code == "USER002"


def test_update_campaign_rejects_invalid_client_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_campaign_service()
    campaign = service.create_campaign("Old Campaign", "C020")
    monkeypatch.setattr(campaign_route_module, "campaign_service", service)
    client = TestClient(app)

    response = client.put(
        f"/api/campaigns/{campaign.id}",
        json=campaign_payload(client_code="UNKNOWN"),
    )

    assert response.status_code == 400


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
            "client_code": None,
            "campaign_offer": None,
        },
    )

    assert response.status_code == 201
    campaign = response.json()["campaign"]
    assert campaign["start_date"] is None
    assert campaign["end_date"] is None
    assert campaign["file_name"] is None
    assert campaign["client_name"] is None
    assert campaign["client_code"] is None
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


def test_existing_campaigns_remain_null_when_client_code_not_supplied() -> None:
    service, session_factory = build_campaign_service()

    campaign = service.create_campaign("Existing Campaign", "C021")

    with session_factory() as session:
        stored = session.get(Campaign, campaign.id)

    assert stored is not None
    assert stored.client_code is None


def test_system_users_remain_unchanged_after_campaign_client_code_crud() -> None:
    service, session_factory = build_campaign_service()
    add_system_user(session_factory, "USER001")
    add_system_user(session_factory, "USER002")
    with session_factory() as session:
        before = [
            (user.id, user.user_id, user.password, user.role)
            for user in session.scalars(select(SystemUser)).all()
        ]

    campaign = service.create_campaign(
        "Test Campaign",
        "C021",
        client_code="USER001",
    )
    service.update_campaign(
        campaign.id,
        "Updated Campaign",
        "C022",
        client_code="USER002",
    )

    with session_factory() as session:
        after = [
            (user.id, user.user_id, user.password, user.role)
            for user in session.scalars(select(SystemUser)).all()
        ]

    assert after == before


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
