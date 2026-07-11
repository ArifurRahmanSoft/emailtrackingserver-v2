"""Tests for updating existing email_tracking rows from open/download events."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.models.attachment import Attachment, AttachmentBase, TrackingAttachment
from app.models.email_tracking import Base, EmailTracking
from app.services.attachment_library import AttachmentLibraryService
from app.services.database_tracking import (
    DatabaseTrackingService,
    SentEmailRegistration,
)


TRACKING_ID = "event-update-123"


def _database_service() -> tuple[DatabaseTrackingService, sessionmaker]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = DatabaseTrackingService(None)
    service._engine = engine
    service._session_factory = session_factory
    return service, session_factory


def test_open_tracking_updates_existing_email_tracking_row_only() -> None:
    service, session_factory = _database_service()
    sent_at = datetime(2026, 7, 9, 8, 0, tzinfo=timezone.utc)
    first_open = sent_at + timedelta(minutes=5)
    second_open = sent_at + timedelta(minutes=10)

    service.register_sent_email(
        SentEmailRegistration(
            tracking_id=TRACKING_ID,
            sender_mail="sender@example.com",
            recipient_mail="recipient@example.com",
            mail_subject="Subject",
            project_name="Project",
        ),
        registered_at=sent_at,
    )

    assert service.record_open(
        TRACKING_ID,
        open_count=99,
        client_ip="203.0.113.1",
        user_agent="Test/1.0",
        occurred_at=first_open,
    )
    assert service.record_open(
        TRACKING_ID,
        open_count=100,
        client_ip="203.0.113.2",
        user_agent="Test/2.0",
        occurred_at=second_open,
    )

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == TRACKING_ID)
        )
        row_count = session.scalar(select(func.count()).select_from(EmailTracking))

    assert row_count == 1
    assert record is not None
    assert record.open_count == 2
    assert record.first_open == first_open.replace(tzinfo=None)
    assert record.last_open == second_open.replace(tzinfo=None)
    assert record.sender_mail == "sender@example.com"
    assert record.recipient_email == "recipient@example.com"
    assert record.mail_subject == "Subject"
    assert record.project_name == "Project"


def test_open_tracking_does_not_create_missing_email_tracking_row() -> None:
    service, session_factory = _database_service()

    updated = service.record_open(
        "missing-tracking-id",
        open_count=1,
        client_ip="203.0.113.1",
        user_agent="Test/1.0",
        occurred_at=datetime(2026, 7, 9, 8, 0, tzinfo=timezone.utc),
    )

    with session_factory() as session:
        row_count = session.scalar(select(func.count()).select_from(EmailTracking))

    assert updated is False
    assert row_count == 0


def _attachment_service(
    tmp_path: Path,
) -> tuple[AttachmentLibraryService, sessionmaker, UUID]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    AttachmentBase.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = AttachmentLibraryService(None, tmp_path)
    service._engine = engine
    service._session_factory = session_factory

    attachment_id = uuid4()
    with session_factory() as session:
        session.add(
            EmailTracking(
                tracking_id=TRACKING_ID,
                sender_mail="sender@example.com",
                recipient_email="recipient@example.com",
                mail_subject="Subject",
                project_name="Project",
            )
        )
        session.add(
            Attachment(
                attachment_id=attachment_id,
                original_file_name="report.pdf",
                stored_file_name="unused.pdf",
                content_type="application/pdf",
                file_size=7,
                file_data=b"PDFDATA",
                is_active=True,
            )
        )
        session.add(
            TrackingAttachment(
                id=uuid4(),
                tracking_id=TRACKING_ID,
                attachment_id=attachment_id,
                download_count=0,
            )
        )
        session.commit()

    return service, session_factory, attachment_id


def test_download_tracking_updates_existing_email_tracking_row(
    tmp_path: Path,
) -> None:
    service, session_factory, attachment_id = _attachment_service(tmp_path)
    first_download = datetime(2026, 7, 9, 9, 0, tzinfo=timezone.utc)
    second_download = first_download + timedelta(minutes=5)

    first_result = service.track_download(
        TRACKING_ID,
        attachment_id,
        downloaded_at=first_download,
    )
    second_result = service.track_download(
        TRACKING_ID,
        attachment_id,
        downloaded_at=second_download,
    )

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == TRACKING_ID)
        )
        row_count = session.scalar(select(func.count()).select_from(EmailTracking))

    assert row_count == 1
    assert first_result.download_count == 1
    assert second_result.download_count == 2
    assert record is not None
    assert record.download_count == 2
    assert record.first_download == first_download.replace(tzinfo=None)
    assert record.last_download == second_download.replace(tzinfo=None)
    assert record.sender_mail == "sender@example.com"
    assert record.recipient_email == "recipient@example.com"
    assert record.mail_subject == "Subject"
    assert record.project_name == "Project"


def test_mark_synchronized_updates_only_last_synchronize_time() -> None:
    service, session_factory = _database_service()
    original_updated_at = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)
    synchronized_at = datetime(2026, 7, 9, 10, 30, tzinfo=timezone.utc)

    with session_factory() as session:
        session.add(
            EmailTracking(
                tracking_id=TRACKING_ID,
                open_count=4,
                click_count=3,
                download_count=2,
                updated_at=original_updated_at,
            )
        )
        session.commit()

    assert service.mark_synchronized(TRACKING_ID, synchronized_at)

    with session_factory() as session:
        record = session.scalar(
            select(EmailTracking).where(EmailTracking.tracking_id == TRACKING_ID)
        )

    assert record is not None
    assert record.last_synchronize_time.replace(tzinfo=None) == synchronized_at.replace(
        tzinfo=None
    )
    assert record.open_count == 4
    assert record.click_count == 3
    assert record.download_count == 2
    assert record.updated_at == original_updated_at.replace(tzinfo=None)
