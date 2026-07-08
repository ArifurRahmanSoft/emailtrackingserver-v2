"""Tests for Version 2 sent-email tracking registration."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.api.routes as route_module
from app.models.email_tracking import Base, EmailTracking
from app.services.database_tracking import (
    DatabaseTrackingService,
    SentEmailRegistration,
    SentEmailRegistrationResult,
)
from main import app


TRACKING_ID = "sent-email-123"


@pytest.fixture
def database_service() -> tuple[DatabaseTrackingService, sessionmaker]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    service = DatabaseTrackingService(None)
    service._engine = engine
    service._session_factory = session_factory
    return service, session_factory


def test_sent_email_registration_populates_new_v2_columns(
    database_service: tuple[DatabaseTrackingService, sessionmaker],
) -> None:
    service, session_factory = database_service

    result = service.register_sent_email(
        SentEmailRegistration(
            tracking_id=TRACKING_ID,
            sender_mail="sender@example.com",
            recipient_mail="recipient@example.com",
            mail_subject="Quarterly Update",
            project_name="Q3 Outreach",
            excel_file_path=r"F:\CODEX\EmailAutomation\data\mail_list.xlsx",
        ),
        registered_at=datetime(2026, 7, 8, 9, 30, tzinfo=timezone.utc),
    )

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == TRACKING_ID)
        )

    assert result.excel_file_name == "mail_list.xlsx"
    assert record is not None
    assert record.sender_email == "sender@example.com"
    assert record.sender_mail == "sender@example.com"
    assert record.recipient_email == "recipient@example.com"
    assert record.mail_subject == "Quarterly Update"
    assert record.project_name == "Q3 Outreach"
    assert record.excel_file_path == r"F:\CODEX\EmailAutomation\data\mail_list.xlsx"
    assert record.excel_file_name == "mail_list.xlsx"
    assert record.last_synchronize_time is None
    assert record.open_count == 0
    assert record.click_count == 0


def test_sent_email_registration_keeps_existing_tracking_counters(
    database_service: tuple[DatabaseTrackingService, sessionmaker],
) -> None:
    service, session_factory = database_service
    first_open = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
    with session_factory() as session:
        session.add(
            EmailTracking(
                tracking_id=TRACKING_ID,
                open_count=3,
                click_count=2,
                first_open=first_open,
            )
        )
        session.commit()

    service.register_sent_email(
        SentEmailRegistration(
            tracking_id=TRACKING_ID,
            sender_mail="sender@example.com",
            recipient_mail="recipient@example.com",
            excel_file_path="/home/user/mail_list.xlsx",
        )
    )

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == TRACKING_ID)
        )

    assert record is not None
    assert record.open_count == 3
    assert record.click_count == 2
    assert record.first_open == first_open.replace(tzinfo=None)
    assert record.excel_file_name == "mail_list.xlsx"


def test_missing_v2_metadata_remains_null_for_backward_compatibility(
    database_service: tuple[DatabaseTrackingService, sessionmaker],
) -> None:
    service, session_factory = database_service

    service.register_sent_email(SentEmailRegistration(tracking_id=TRACKING_ID))

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == TRACKING_ID)
        )

    assert record is not None
    assert record.sender_email is None
    assert record.sender_mail is None
    assert record.recipient_email is None
    assert record.mail_subject is None
    assert record.project_name is None
    assert record.excel_file_path is None
    assert record.excel_file_name is None
    assert record.last_synchronize_time is None


class FakeRegistrationDatabase:
    """Endpoint fake that captures V2 registration payloads."""

    def __init__(self) -> None:
        self.registrations: list[SentEmailRegistration] = []

    def register_sent_email(
        self,
        registration: SentEmailRegistration,
    ) -> SentEmailRegistrationResult:
        self.registrations.append(registration)
        return SentEmailRegistrationResult(
            tracking_id=registration.tracking_id,
            excel_file_name="mail_list.xlsx",
        )


def test_register_send_endpoint_accepts_v2_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeRegistrationDatabase()
    monkeypatch.setattr(route_module, "database_service", database)
    client = TestClient(app)

    response = client.post(
        "/api/tracking/register-send",
        json={
            "tracking_id": TRACKING_ID,
            "sender_mail": "sender@example.com",
            "recipient_mail": "recipient@example.com",
            "mail_subject": "Quarterly Update",
            "project_name": "Q3 Outreach",
            "excel_file_path": r"F:\CODEX\EmailAutomation\data\mail_list.xlsx",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "tracking_id": TRACKING_ID,
        "excel_file_name": "mail_list.xlsx",
    }
    assert database.registrations[0].project_name == "Q3 Outreach"


def test_register_send_endpoint_rejects_invalid_tracking_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeRegistrationDatabase()
    monkeypatch.setattr(route_module, "database_service", database)
    client = TestClient(app)

    response = client.post(
        "/api/tracking/register-send",
        json={"tracking_id": "invalid.id"},
    )

    assert response.status_code == 400
    assert database.registrations == []
