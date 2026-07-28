"""Tests for Version 2 recipient unsubscribe tracking."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.api.routes as route_module
from app.models.email_tracking import Base, EmailTracking
from app.services.database_tracking import DatabaseTrackingService, UnsubscribeResult
from main import app


TRACKING_ID = "unsubscribe-track-123"


def build_database_service() -> tuple[DatabaseTrackingService, sessionmaker]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = DatabaseTrackingService(None)
    service._engine = engine
    service._session_factory = session_factory
    return service, session_factory


def test_valid_tracking_id_updates_unsubscribe_correctly() -> None:
    service, session_factory = build_database_service()
    unsubscribe_time = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)

    with session_factory() as session:
        session.add(
            EmailTracking(
                tracking_id=TRACKING_ID,
                sender_email="sender@example.com",
                recipient_email="recipient@example.com",
                unsubscribe=0,
            )
        )
        session.commit()

    result = service.record_unsubscribe(
        TRACKING_ID,
        client_ip="203.0.113.10",
        user_agent="UnsubscribeTest/1.0",
        occurred_at=unsubscribe_time,
    )

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == TRACKING_ID)
        )

    assert result is not None
    assert result.tracking_id == TRACKING_ID
    assert result.recipient_email == "recipient@example.com"
    assert result.sender_email == "sender@example.com"
    assert result.previous_unsubscribe == 0
    assert result.unsubscribe == 1
    assert result.commit_success is True
    assert record is not None
    assert record.unsubscribe == 1
    assert record.unsubscribe_time.replace(tzinfo=None) == unsubscribe_time.replace(
        tzinfo=None
    )
    assert record.updated_at.replace(tzinfo=None) == unsubscribe_time.replace(
        tzinfo=None
    )


def test_invalid_tracking_id_returns_none_without_database_update() -> None:
    service, session_factory = build_database_service()

    result = service.record_unsubscribe(
        "missing-tracking-id",
        client_ip="203.0.113.10",
        user_agent="UnsubscribeTest/1.0",
    )

    with session_factory() as session:
        total_records = len(session.scalars(select(EmailTracking)).all())

    assert result is None
    assert total_records == 0


def test_unsubscribe_changes_from_false_to_true_only_once() -> None:
    service, session_factory = build_database_service()
    first_time = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    second_time = first_time + timedelta(hours=1)

    with session_factory() as session:
        session.add(
            EmailTracking(
                tracking_id=TRACKING_ID,
                sender_email="sender@example.com",
                recipient_email="recipient@example.com",
                unsubscribe=0,
            )
        )
        session.commit()

    first = service.record_unsubscribe(
        TRACKING_ID,
        client_ip="203.0.113.10",
        user_agent="UnsubscribeTest/1.0",
        occurred_at=first_time,
    )
    second = service.record_unsubscribe(
        TRACKING_ID,
        client_ip="203.0.113.10",
        user_agent="UnsubscribeTest/1.0",
        occurred_at=second_time,
    )

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == TRACKING_ID)
        )

    assert first is not None
    assert second is not None
    assert first.previous_unsubscribe == 0
    assert first.unsubscribe == 1
    assert first.commit_success is True
    assert second.previous_unsubscribe == 1
    assert second.unsubscribe == 1
    assert second.commit_success is False
    assert second.unsubscribe_time.replace(tzinfo=None) == first_time.replace(
        tzinfo=None
    )
    assert record is not None
    assert record.unsubscribe == 1
    assert record.unsubscribe_time.replace(tzinfo=None) == first_time.replace(
        tzinfo=None
    )
    assert record.updated_at.replace(tzinfo=None) == first_time.replace(tzinfo=None)


def test_unsubscribe_endpoint_returns_success_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDatabaseService:
        def record_unsubscribe(
            self,
            tracking_id: str,
            client_ip: str,
            user_agent: str,
            occurred_at: datetime | None = None,
        ) -> UnsubscribeResult:
            assert tracking_id == TRACKING_ID
            assert user_agent == "UnsubscribeEndpointTest/1.0"
            assert occurred_at is not None
            return UnsubscribeResult(
                tracking_id=tracking_id,
                recipient_email="recipient@example.com",
                sender_email="sender@example.com",
                previous_unsubscribe=0,
                unsubscribe=1,
                unsubscribe_time=occurred_at,
                database_primary_key=123,
                commit_success=True,
            )

    monkeypatch.setattr(route_module, "database_service", FakeDatabaseService())
    client = TestClient(app)

    response = client.get(
        f"/unsubscribe/{TRACKING_ID}",
        headers={"User-Agent": "UnsubscribeEndpointTest/1.0"},
    )

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>Successfully Unsubscribed</title>" in response.text
    assert "You have successfully unsubscribed." in response.text
    assert "You will no longer receive emails from us." in response.text


def test_unsubscribe_endpoint_returns_http_404_for_unknown_tracking_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeDatabaseService:
        def record_unsubscribe(
            self,
            tracking_id: str,
            client_ip: str,
            user_agent: str,
            occurred_at: datetime | None = None,
        ) -> None:
            assert tracking_id == "missing-tracking-id"
            return None

    monkeypatch.setattr(route_module, "database_service", FakeDatabaseService())
    client = TestClient(app)

    response = client.get("/unsubscribe/missing-tracking-id")

    assert response.status_code == 404
