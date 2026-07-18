"""Tests for Version 2 dashboard statistics."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.api.routes as route_module
from app.models.email_tracking import Base, EmailTracking
from app.models.statistics import DashboardStatisticsResponse
from app.services.dashboard_statistics import DashboardStatisticsService
from app.services.database_tracking import DatabaseTrackingService
from main import app


def build_dashboard_service() -> tuple[DashboardStatisticsService, sessionmaker]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    database_service = DatabaseTrackingService(None)
    database_service._engine = engine
    database_service._session_factory = session_factory
    return DashboardStatisticsService(database_service), session_factory


def test_empty_database_returns_zero_statistics() -> None:
    service, _ = build_dashboard_service()
    generated_at = datetime(2026, 7, 18, 10, 30, tzinfo=timezone.utc)

    result = service.get_statistics(generated_at)

    assert result.total_sent == 0
    assert result.total_open == 0
    assert result.total_click == 0
    assert result.total_download == 0
    assert result.total_reply == 0
    assert result.total_bounce == 0
    assert result.weekly_sent == 0
    assert result.monthly_sent == 0
    assert result.success_rate == 0.0
    assert result.failure_rate == 0.0
    assert result.last_updated == generated_at


def test_existing_tracking_data_returns_aggregate_statistics() -> None:
    service, session_factory = build_dashboard_service()
    generated_at = datetime(2026, 7, 18, 10, 30, tzinfo=timezone.utc)

    with session_factory() as session:
        session.add_all(
            [
                EmailTracking(
                    tracking_id="sent-this-week",
                    open_count=2,
                    click_count=1,
                    download_count=3,
                    reply_count=1,
                    is_bounce=0,
                    created_at=generated_at - timedelta(days=2),
                ),
                EmailTracking(
                    tracking_id="bounced-this-month",
                    open_count=0,
                    click_count=0,
                    download_count=1,
                    reply_count=0,
                    is_bounce=1,
                    created_at=generated_at - timedelta(days=20),
                ),
                EmailTracking(
                    tracking_id="older-than-month",
                    open_count=5,
                    click_count=2,
                    download_count=None,
                    reply_count=None,
                    is_bounce=1,
                    created_at=generated_at - timedelta(days=45),
                ),
            ]
        )
        session.commit()

    result = service.get_statistics(generated_at)

    assert result.total_sent == 3
    assert result.total_open == 7
    assert result.total_click == 3
    assert result.total_download == 4
    assert result.total_reply == 1
    assert result.total_bounce == 2
    assert result.weekly_sent == 1
    assert result.monthly_sent == 2
    assert result.success_rate == 33.33
    assert result.failure_rate == 66.67
    assert result.last_updated == generated_at


def test_dashboard_statistics_endpoint_returns_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_at = datetime(2026, 7, 18, 10, 30, tzinfo=timezone.utc)

    class FakeDashboardStatisticsService:
        def get_statistics(self) -> DashboardStatisticsResponse:
            return DashboardStatisticsResponse(
                total_sent=3,
                total_open=7,
                total_click=3,
                total_download=4,
                total_reply=1,
                total_bounce=2,
                weekly_sent=1,
                monthly_sent=2,
                success_rate=33.33,
                failure_rate=66.67,
                last_updated=generated_at,
            )

    monkeypatch.setattr(
        route_module,
        "dashboard_statistics_service",
        FakeDashboardStatisticsService(),
    )
    client = TestClient(app)

    response = client.get("/api/dashboard/statistics")

    assert response.status_code == 200
    assert response.json() == {
        "total_sent": 3,
        "total_open": 7,
        "total_click": 3,
        "total_download": 4,
        "total_reply": 1,
        "total_bounce": 2,
        "weekly_sent": 1,
        "monthly_sent": 2,
        "success_rate": 33.33,
        "failure_rate": 66.67,
        "last_updated": "2026-07-18T10:30:00Z",
    }
