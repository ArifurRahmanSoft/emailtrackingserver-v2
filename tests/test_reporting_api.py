"""Tests for the Version 2 reporting API."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.api.routes as route_module
from app.models.email_tracking import Base, EmailTracking
from app.models.report import ReportResponse
from app.services.database_tracking import DatabaseTrackingService
from app.services.reporting import ReportingService
from main import app


def build_reporting_service() -> tuple[ReportingService, sessionmaker, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    database_service = DatabaseTrackingService(None)
    database_service._engine = engine
    database_service._session_factory = session_factory
    return ReportingService(database_service), session_factory, engine


def seed_records(
    session_factory: sessionmaker,
    count: int,
    start_time: datetime | None = None,
) -> None:
    base_time = start_time or datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                EmailTracking(
                    tracking_id=f"report-{index:03d}",
                    sender_email=f"sender-{index}@example.com",
                    recipient_email=f"receiver-{index}@example.com",
                    project_name=f"Project {index % 3}",
                    created_at=base_time - timedelta(minutes=index),
                    open_count=index,
                    click_count=index % 5,
                    download_count=index % 4,
                    reply_count=index % 3,
                    is_bounce=1 if index % 7 == 0 else 0,
                )
                for index in range(count)
            ]
        )
        session.commit()


def test_default_pagination_returns_first_20_newest_rows() -> None:
    service, session_factory, _ = build_reporting_service()
    seed_records(session_factory, 25)

    result = service.get_report()

    assert result.page == 1
    assert result.page_size == 20
    assert result.total_records == 25
    assert result.total_pages == 2
    assert result.has_next_page is True
    assert result.has_previous_page is False
    assert len(result.items) == 20
    assert [item.tracking_id for item in result.items[:3]] == [
        "report-000",
        "report-001",
        "report-002",
    ]


def test_custom_page_returns_next_rows() -> None:
    service, session_factory, _ = build_reporting_service()
    seed_records(session_factory, 45)

    result = service.get_report(page=2)

    assert result.page == 2
    assert result.page_size == 20
    assert result.total_records == 45
    assert result.total_pages == 3
    assert result.has_next_page is True
    assert result.has_previous_page is True
    assert len(result.items) == 20
    assert result.items[0].tracking_id == "report-020"


def test_custom_page_size_returns_requested_number_of_rows() -> None:
    service, session_factory, _ = build_reporting_service()
    seed_records(session_factory, 75)

    result = service.get_report(page_size=50)

    assert result.page == 1
    assert result.page_size == 50
    assert result.total_records == 75
    assert result.total_pages == 2
    assert len(result.items) == 50


def test_invalid_pagination_values_are_normalized() -> None:
    service, session_factory, _ = build_reporting_service()
    seed_records(session_factory, 150)

    result = service.get_report(page=0, page_size=500)

    assert result.page == 1
    assert result.page_size == 100
    assert result.total_records == 150
    assert result.total_pages == 2
    assert len(result.items) == 100


def test_page_greater_than_total_pages_returns_empty_items() -> None:
    service, session_factory, _ = build_reporting_service()
    seed_records(session_factory, 25)

    result = service.get_report(page=9)

    assert result.page == 9
    assert result.total_records == 25
    assert result.total_pages == 2
    assert result.has_next_page is False
    assert result.has_previous_page is True
    assert result.items == []


def test_empty_database_returns_empty_page() -> None:
    service, _, _ = build_reporting_service()

    result = service.get_report()

    assert result.page == 1
    assert result.page_size == 20
    assert result.total_records == 0
    assert result.total_pages == 0
    assert result.has_next_page is False
    assert result.has_previous_page is False
    assert result.items == []


def test_report_items_map_required_fields() -> None:
    service, session_factory, _ = build_reporting_service()
    created_at = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
    with session_factory() as session:
        session.add(
            EmailTracking(
                tracking_id="field-map",
                sender_email="sender@example.com",
                recipient_email="receiver@example.com",
                project_name="Mapped Project",
                created_at=created_at,
                open_count=10,
                click_count=3,
                download_count=2,
                reply_count=1,
                is_bounce=1,
            )
        )
        session.commit()

    item = service.get_report().items[0]

    assert item.tracking_id == "field-map"
    assert item.sender_email == "sender@example.com"
    assert item.receiver_email == "receiver@example.com"
    assert item.project_name == "Mapped Project"
    assert item.send_date.replace(tzinfo=None) == created_at.replace(tzinfo=None)
    assert item.open_count == 10
    assert item.click_count == 3
    assert item.download_count == 2
    assert item.reply_count == 1
    assert item.is_bounce is True


def test_report_executes_one_count_and_one_select_query() -> None:
    service, session_factory, engine = build_reporting_service()
    seed_records(session_factory, 10)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    service.get_report()
    event.remove(engine, "before_cursor_execute", capture_statement)

    assert len(statements) == 2
    assert statements[0].lstrip().upper().startswith("SELECT COUNT")
    assert "LIMIT" in statements[1].upper()


def test_report_endpoint_returns_paginated_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeReportingService:
        def get_report(self, page: int, page_size: int) -> ReportResponse:
            assert page == 2
            assert page_size == 5
            return ReportResponse(
                page=2,
                page_size=5,
                total_records=6,
                total_pages=2,
                has_next_page=False,
                has_previous_page=True,
                items=[
                    {
                        "tracking_id": "endpoint-row",
                        "sender_email": "sender@example.com",
                        "receiver_email": "receiver@example.com",
                        "project_name": "Endpoint Project",
                        "send_date": datetime(
                            2026, 7, 20, 10, 0, tzinfo=timezone.utc
                        ),
                        "open_count": 1,
                        "click_count": 2,
                        "download_count": 3,
                        "reply_count": 4,
                        "is_bounce": False,
                    }
                ],
            )

    monkeypatch.setattr(route_module, "reporting_service", FakeReportingService())
    client = TestClient(app)

    response = client.get("/api/report", params={"page": 2, "page_size": 5})

    assert response.status_code == 200
    assert response.json()["page"] == 2
    assert response.json()["page_size"] == 5
    assert response.json()["total_records"] == 6
    assert response.json()["items"][0]["tracking_id"] == "endpoint-row"
