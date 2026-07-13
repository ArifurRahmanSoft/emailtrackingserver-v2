"""Tests for server-side bounce tracking registration."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

import app.api.routes as route_module
from app.models.email_tracking import Base, EmailTracking
from app.services.database_tracking import (
    BounceUpdateResult,
    DatabaseTrackingService,
    DatabaseUnavailableError,
)
from main import app


TRACKING_ID = "bounce-track-123"
MESSAGE_ID = "bounce-message@emailautomation-v2.local"


def isolated_database_url(name: str) -> str:
    temp_dir = Path(__file__).parent / "_tmp_databases"
    temp_dir.mkdir(exist_ok=True)
    return f"sqlite:///{(temp_dir / f'{name}-{uuid4().hex}.db').as_posix()}"


def build_database_service(database_url: str = "sqlite+pysqlite:///:memory:"):
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = DatabaseTrackingService(None)
    service._engine = engine
    service._session_factory = session_factory
    return service, session_factory


def test_first_bounce_marks_existing_row_without_touching_counters() -> None:
    service, session_factory = build_database_service()
    bounce_time = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)

    with session_factory() as session:
        session.add(
            EmailTracking(
                tracking_id=TRACKING_ID,
                message_id=MESSAGE_ID,
                open_count=4,
                click_count=3,
                download_count=2,
                reply_count=1,
            )
        )
        session.commit()

    result = service.record_bounce(
        MESSAGE_ID,
        "550 5.1.1 User Unknown",
        bounce_time,
    )

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.message_id == MESSAGE_ID)
        )

    assert result is not None
    assert result.message_id == MESSAGE_ID
    assert result.tracking_id == TRACKING_ID
    assert result.is_bounce == 1
    assert result.bounce_time.replace(tzinfo=None) == bounce_time.replace(tzinfo=None)
    assert result.bounce_reason == "550 5.1.1 User Unknown"
    assert result.is_bounce_before_update == 0
    assert record is not None
    assert record.is_bounce == 1
    assert record.bounce_time.replace(tzinfo=None) == bounce_time.replace(tzinfo=None)
    assert record.bounce_reason == "550 5.1.1 User Unknown"
    assert record.open_count == 4
    assert record.click_count == 3
    assert record.download_count == 2
    assert record.reply_count == 1


def test_repeated_bounce_preserves_first_bounce_time_and_updates_reason() -> None:
    service, session_factory = build_database_service()
    first_time = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)
    second_time = first_time + timedelta(hours=2)

    with session_factory() as session:
        session.add(EmailTracking(tracking_id=TRACKING_ID, message_id=MESSAGE_ID))
        session.commit()

    first = service.record_bounce(MESSAGE_ID, "first reason", first_time)
    second = service.record_bounce(MESSAGE_ID, "updated reason", second_time)

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.message_id == MESSAGE_ID)
        )

    assert first is not None
    assert second is not None
    assert first.is_bounce_before_update == 0
    assert second.is_bounce_before_update == 1
    assert second.bounce_time.replace(tzinfo=None) == first_time.replace(tzinfo=None)
    assert second.bounce_reason == "updated reason"
    assert record is not None
    assert record.bounce_time.replace(tzinfo=None) == first_time.replace(tzinfo=None)
    assert record.bounce_reason == "updated reason"


def test_unknown_message_id_returns_http_404(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBounceDatabase:
        def record_bounce(
            self,
            message_id: str,
            bounce_reason: str | None = None,
            occurred_at: datetime | None = None,
        ):
            assert message_id == "unknown-message@example.com"
            return None

    monkeypatch.setattr(route_module, "database_service", FakeBounceDatabase())
    client = TestClient(app)

    response = client.post(
        "/api/tracking/register-bounce",
        json={
            "message_id": "unknown-message@example.com",
            "bounce_reason": "550 5.1.1 User Unknown",
        },
    )

    assert response.status_code == 404


def test_bounce_endpoint_returns_success_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    bounce_time = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)

    class FakeBounceDatabase:
        def record_bounce(
            self,
            message_id: str,
            bounce_reason: str | None = None,
            occurred_at: datetime | None = None,
        ):
            assert message_id == MESSAGE_ID
            assert bounce_reason == "550 5.1.1 User Unknown"
            assert occurred_at is not None
            return BounceUpdateResult(
                message_id=message_id,
                tracking_id=TRACKING_ID,
                is_bounce=1,
                bounce_time=bounce_time,
                bounce_reason=bounce_reason,
                database_primary_key=123,
                is_bounce_before_update=0,
            )

    monkeypatch.setattr(route_module, "database_service", FakeBounceDatabase())
    client = TestClient(app)

    response = client.post(
        "/api/tracking/register-bounce",
        json={
            "message_id": MESSAGE_ID,
            "bounce_reason": "550 5.1.1 User Unknown",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["message_id"] == MESSAGE_ID
    assert response.json()["tracking_id"] == TRACKING_ID
    assert response.json()["is_bounce"] == 1
    assert response.json()["bounce_reason"] == "550 5.1.1 User Unknown"


def test_transaction_rollback_on_failure() -> None:
    service, session_factory = build_database_service()

    with session_factory() as session:
        session.add(EmailTracking(tracking_id=TRACKING_ID, message_id=MESSAGE_ID))
        session.commit()

    @event.listens_for(service._engine, "after_cursor_execute")
    def fail_after_update(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE EMAIL_TRACKING"):
            raise RuntimeError("forced failure after bounce update")

    with pytest.raises(DatabaseUnavailableError):
        service.record_bounce(MESSAGE_ID, "temporary failure")

    event.remove(service._engine, "after_cursor_execute", fail_after_update)

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.message_id == MESSAGE_ID)
        )

    assert record is not None
    assert record.is_bounce == 0
    assert record.bounce_time is None
    assert record.bounce_reason is None
