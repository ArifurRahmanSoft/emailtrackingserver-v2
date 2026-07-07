"""Database-backed tests for BYTEA attachment storage compatibility."""

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.attachment import Attachment, AttachmentBase, TrackingAttachment
from app.services.attachment_library import AttachmentLibraryService


@pytest.fixture
def bytea_service(tmp_path: Path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AttachmentBase.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = AttachmentLibraryService(None, tmp_path)
    service._engine = engine
    service._session_factory = session_factory
    return service, session_factory


def add_mapping(session_factory, tracking_id: str, attachment_id) -> None:
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            TrackingAttachment(
                id=uuid4(),
                tracking_id=tracking_id,
                attachment_id=attachment_id,
                download_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def test_upload_stores_bytes_in_database_without_writing_file(
    bytea_service,
) -> None:
    service, session_factory = bytea_service
    payload = b"stored in PostgreSQL"

    uploaded = service.upload(
        BytesIO(payload),
        "database-only.pdf",
        "application/pdf",
    )

    with session_factory() as session:
        stored = session.get(Attachment, uploaded.attachment_id)
        assert stored is not None
        assert stored.file_data == payload
        assert stored.file_size == len(payload)
        assert stored.stored_file_name
    assert list(service.attachment_folder.iterdir()) == []


def test_download_reads_bytea_before_local_file(
    bytea_service,
) -> None:
    service, session_factory = bytea_service
    attachment_id = uuid4()
    tracking_id = "bytea-download"
    payload = b"database download"
    with session_factory() as session:
        session.add(
            Attachment(
                attachment_id=attachment_id,
                original_file_name="bytea.bin",
                stored_file_name="missing-local-file.bin",
                content_type="application/octet-stream",
                file_size=len(payload),
                file_data=payload,
                is_active=True,
            )
        )
        session.commit()
    add_mapping(session_factory, tracking_id, attachment_id)

    result = service.track_download(tracking_id, attachment_id)

    assert result.file_bytes == payload


def test_existing_local_file_attachment_still_downloads(
    bytea_service,
) -> None:
    service, session_factory = bytea_service
    attachment_id = uuid4()
    tracking_id = "legacy-download"
    payload = b"legacy local contents"
    stored_name = "legacy-file.dat"
    (service.attachment_folder / stored_name).write_bytes(payload)
    with session_factory() as session:
        session.add(
            Attachment(
                attachment_id=attachment_id,
                original_file_name="legacy.dat",
                stored_file_name=stored_name,
                content_type="application/octet-stream",
                file_size=len(payload),
                file_data=None,
                is_active=True,
            )
        )
        session.commit()
    add_mapping(session_factory, tracking_id, attachment_id)

    result = service.track_download(tracking_id, attachment_id)

    assert result.file_bytes == payload


def test_download_tracking_still_updates_for_bytea(
    bytea_service,
) -> None:
    service, session_factory = bytea_service
    attachment_id = uuid4()
    tracking_id = "counter-download"
    with session_factory() as session:
        session.add(
            Attachment(
                attachment_id=attachment_id,
                original_file_name="counter.txt",
                stored_file_name="unused.txt",
                content_type="text/plain",
                file_size=4,
                file_data=b"data",
                is_active=True,
            )
        )
        session.commit()
    add_mapping(session_factory, tracking_id, attachment_id)

    first = service.track_download(tracking_id, attachment_id)
    second = service.track_download(tracking_id, attachment_id)

    assert first.download_count == 1
    assert second.download_count == 2
    assert second.first_download.replace(tzinfo=None) == first.first_download.replace(
        tzinfo=None
    )
    assert second.last_download >= first.last_download
    with session_factory() as session:
        mapping = session.scalar(
            select(TrackingAttachment).where(
                TrackingAttachment.tracking_id == tracking_id,
                TrackingAttachment.attachment_id == attachment_id,
            )
        )
        assert mapping is not None
        assert mapping.download_count == 2
