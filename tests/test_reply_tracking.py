"""Tests for server-side reply tracking registration."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.api.routes as route_module
from app.models.email_tracking import Base, EmailTracking
from app.services.database_tracking import DatabaseTrackingService, ReplyUpdateResult
from main import app


TRACKING_ID = "reply-track-123"


def build_database_service(database_url: str = "sqlite+pysqlite:///:memory:"):
    connect_args = {}
    if database_url.startswith("sqlite:///"):
        connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(database_url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = DatabaseTrackingService(None)
    service._engine = engine
    service._session_factory = session_factory
    return service, session_factory


def test_unknown_tracking_id_returns_http_404(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeReplyDatabase:
        def record_reply(self, tracking_id: str, occurred_at: datetime | None = None):
            return None

    monkeypatch.setattr(route_module, "database_service", FakeReplyDatabase())
    client = TestClient(app)

    response = client.post(
        "/api/tracking/register-reply",
        json={
            "tracking_id": "unknown-track",
            "from_email": "recipient@example.com",
            "message_id": "<reply-1@example.com>",
        },
    )

    assert response.status_code == 404


def test_first_and_second_reply_update_existing_row() -> None:
    service, session_factory = build_database_service()
    first_reply = datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)
    second_reply = first_reply + timedelta(minutes=5)

    with session_factory() as session:
        session.add(EmailTracking(tracking_id=TRACKING_ID))
        session.commit()

    first = service.record_reply(TRACKING_ID, first_reply)
    second = service.record_reply(TRACKING_ID, second_reply)

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == TRACKING_ID)
        )

    assert first is not None
    assert second is not None
    assert first.reply_count == 1
    assert second.reply_count == 2
    assert first.first_reply.replace(tzinfo=None) == first_reply.replace(tzinfo=None)
    assert second.first_reply.replace(tzinfo=None) == first_reply.replace(tzinfo=None)
    assert second.last_reply.replace(tzinfo=None) == second_reply.replace(tzinfo=None)
    assert record is not None
    assert record.reply_count == 2
    assert record.first_reply.replace(tzinfo=None) == first_reply.replace(tzinfo=None)
    assert record.last_reply.replace(tzinfo=None) == second_reply.replace(tzinfo=None)


def test_reply_endpoint_returns_updated_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeReplyDatabase:
        def record_reply(self, tracking_id: str, occurred_at: datetime | None = None):
            assert tracking_id == TRACKING_ID
            assert occurred_at is not None
            return ReplyUpdateResult(
                reply_count=1,
                first_reply=occurred_at,
                last_reply=occurred_at,
            )

    monkeypatch.setattr(route_module, "database_service", FakeReplyDatabase())
    client = TestClient(app)

    response = client.post(
        "/api/tracking/register-reply",
        json={
            "tracking_id": TRACKING_ID,
            "reply_time": "2026-07-11T08:00:00Z",
            "from_email": "recipient@example.com",
            "message_id": "<reply-1@example.com>",
        },
        headers={"User-Agent": "ReplyTrackingTest/1.0"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["tracking_id"] == TRACKING_ID
    assert response.json()["reply_count"] == 1


def test_concurrent_replies_increment_without_lost_updates(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'reply.db').as_posix()}"
    service, session_factory = build_database_service(database_url)

    with session_factory() as session:
        session.add(EmailTracking(tracking_id=TRACKING_ID, reply_count=0))
        session.commit()

    base_time = datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc)

    def register_reply(index: int):
        return service.record_reply(
            TRACKING_ID,
            base_time + timedelta(seconds=index),
        )

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(register_reply, range(10)))

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == TRACKING_ID)
        )

    assert all(result is not None for result in results)
    assert record is not None
    assert record.reply_count == 10
