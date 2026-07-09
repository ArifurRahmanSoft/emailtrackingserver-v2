"""File and PostgreSQL operations for the attachment library."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session, sessionmaker

from app.models.attachment import Attachment, AttachmentBase, TrackingAttachment
from app.models.email_tracking import EmailTracking

MAX_ATTACHMENT_SIZE = 50 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024
logger = logging.getLogger(__name__)


class AttachmentLibraryError(RuntimeError):
    """Base error for attachment-library operations."""


class AttachmentValidationError(AttachmentLibraryError):
    """Raised when uploaded attachment metadata is invalid."""


class AttachmentTooLargeError(AttachmentValidationError):
    """Raised when an upload exceeds the 50 MB limit."""


class DuplicateAttachmentError(AttachmentValidationError):
    """Raised when an active attachment already uses the original filename."""


class AttachmentNotFoundError(AttachmentLibraryError):
    """Raised when an attachment is absent or already inactive."""


@dataclass(frozen=True, slots=True)
class AttachmentDownloadResult:
    """Stored file and counter metadata returned after a tracked download."""

    file_bytes: bytes
    original_file_name: str
    content_type: str
    download_count: int
    first_download: datetime
    last_download: datetime


class AttachmentLibraryService:
    """Persist attachment files and metadata without affecting tracking data."""

    def __init__(self, database_url: str | None, attachment_folder: Path) -> None:
        self.attachment_folder = attachment_folder
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._configuration_error: str | None = None

        if database_url:
            try:
                self._engine = create_engine(
                    self._normalize_database_url(database_url),
                    pool_pre_ping=True,
                    pool_recycle=300,
                    connect_args={"connect_timeout": 10},
                )
                self._session_factory = sessionmaker(
                    bind=self._engine,
                    expire_on_commit=False,
                )
            except Exception as exc:
                self._configuration_error = str(exc)

    def initialize(self) -> None:
        """Create the upload folder and attachment table when absent."""
        self.attachment_folder.mkdir(parents=True, exist_ok=True)
        engine = self._require_engine()
        with engine.begin() as connection:
            AttachmentBase.metadata.create_all(connection)
            if connection.dialect.name == "postgresql":
                connection.execute(
                    text(
                        "ALTER TABLE attachments "
                        "ADD COLUMN IF NOT EXISTS file_data BYTEA"
                    )
                )

    def upload(
        self,
        source: BinaryIO,
        original_file_name: str,
        content_type: str | None,
    ) -> Attachment:
        """Validate, store, and register one uploaded file atomically."""
        safe_original_name = self._normalize_original_name(original_file_name)
        session_factory = self._require_session_factory()
        try:
            with session_factory() as session:
                # Serialize same-name uploads without changing the table schema.
                if session.get_bind().dialect.name == "postgresql":
                    session.execute(
                        text(
                            "SELECT pg_advisory_xact_lock("
                            "hashtextextended(:file_name, 0))"
                        ),
                        {"file_name": safe_original_name},
                    )
                duplicate = session.scalar(
                    select(Attachment.attachment_id).where(
                        Attachment.original_file_name == safe_original_name,
                        Attachment.is_active.is_(True),
                    )
                )
                if duplicate is not None:
                    raise DuplicateAttachmentError(
                        "An active attachment with this original filename "
                        "already exists."
                    )

                attachment_id = uuid4()
                file_data, file_size = self._read_file_bytes(source)
                stored_file_name = self._generate_stored_name(
                    attachment_id, safe_original_name
                )
                attachment = Attachment(
                    attachment_id=attachment_id,
                    original_file_name=safe_original_name,
                    stored_file_name=stored_file_name,
                    content_type=content_type or "application/octet-stream",
                    file_size=file_size,
                    file_data=file_data,
                    is_active=True,
                )
                session.add(attachment)
                session.commit()
                return attachment
        except AttachmentLibraryError:
            raise
        except Exception as exc:
            raise AttachmentLibraryError(
                f"Unable to store attachment: {exc}"
            ) from exc

    def list_active(self) -> list[Attachment]:
        """Return active attachments newest first."""
        session_factory = self._require_session_factory()
        try:
            with session_factory() as session:
                return list(
                    session.scalars(
                        select(Attachment)
                        .where(Attachment.is_active.is_(True))
                        .order_by(Attachment.uploaded_at.desc())
                    )
                )
        except Exception as exc:
            raise AttachmentLibraryError(
                f"Unable to list attachments: {exc}"
            ) from exc

    def deactivate(self, attachment_id: UUID) -> Attachment:
        """Soft-delete an active attachment while retaining its file."""
        session_factory = self._require_session_factory()
        try:
            with session_factory() as session:
                attachment = session.scalar(
                    select(Attachment)
                    .where(
                        Attachment.attachment_id == attachment_id,
                        Attachment.is_active.is_(True),
                    )
                    .with_for_update()
                )
                if attachment is None:
                    raise AttachmentNotFoundError("Active attachment not found.")
                attachment.is_active = False
                session.commit()
                return attachment
        except AttachmentNotFoundError:
            raise
        except Exception as exc:
            raise AttachmentLibraryError(
                f"Unable to delete attachment: {exc}"
            ) from exc

    def track_download(
        self,
        tracking_id: str,
        attachment_id: UUID,
        downloaded_at: datetime | None = None,
    ) -> AttachmentDownloadResult:
        """Validate and increment an attachment download in one transaction."""
        session_factory = self._require_session_factory()
        timestamp = downloaded_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)

        try:
            with session_factory() as session:
                mapping = session.scalar(
                    select(TrackingAttachment)
                    .where(
                        TrackingAttachment.tracking_id == tracking_id,
                        TrackingAttachment.attachment_id == attachment_id,
                    )
                    .with_for_update()
                )
                if mapping is None:
                    raise AttachmentNotFoundError(
                        "Attachment mapping not found."
                    )

                attachment = session.scalar(
                    select(Attachment)
                    .where(
                        Attachment.attachment_id == attachment_id,
                        Attachment.is_active.is_(True),
                    )
                    .with_for_update()
                )
                if attachment is None:
                    raise AttachmentNotFoundError("Active attachment not found.")

                if attachment.file_data is not None:
                    file_bytes = attachment.file_data
                else:
                    file_path = self._resolve_stored_file(
                        attachment.stored_file_name
                    )
                    if not file_path.is_file():
                        raise AttachmentNotFoundError("Attachment file not found.")
                    file_bytes = file_path.read_bytes()

                mapping.download_count = (mapping.download_count or 0) + 1
                if mapping.first_download is None:
                    mapping.first_download = timestamp
                mapping.last_download = timestamp
                mapping.updated_at = timestamp
                self._update_email_tracking_download_summary(
                    session,
                    tracking_id,
                    timestamp,
                )
                session.commit()

                return AttachmentDownloadResult(
                    file_bytes=file_bytes,
                    original_file_name=attachment.original_file_name,
                    content_type=attachment.content_type,
                    download_count=mapping.download_count,
                    first_download=mapping.first_download,
                    last_download=mapping.last_download,
                )
        except AttachmentNotFoundError:
            raise
        except Exception as exc:
            raise AttachmentLibraryError(
                f"Unable to track attachment download: {exc}"
            ) from exc

    def _update_email_tracking_download_summary(
        self,
        session: Session,
        tracking_id: str,
        timestamp: datetime,
    ) -> None:
        """Update existing email_tracking download summary fields when present."""
        if not inspect(session.get_bind()).has_table(EmailTracking.__tablename__):
            logger.warning(
                "Email tracking summary update skipped: tracking_id=%s "
                "event_type=download database_update_status=table_missing",
                tracking_id,
            )
            return

        record = session.scalar(
            select(EmailTracking)
            .where(EmailTracking.tracking_id == tracking_id)
            .with_for_update()
        )
        if record is None:
            logger.warning(
                "Email tracking summary update skipped: tracking_id=%s "
                "event_type=download database_update_status=not_found",
                tracking_id,
            )
            return

        record.download_count = (record.download_count or 0) + 1
        if record.first_download is None:
            record.first_download = timestamp
        record.last_download = timestamp
        record.updated_at = timestamp
        logger.info(
            "Email tracking summary update completed: tracking_id=%s "
            "event_type=download database_update_status=success",
            tracking_id,
        )

    def register_mappings(
        self,
        tracking_id: str,
        attachment_ids: list[UUID],
        created_at: datetime | None = None,
    ) -> int:
        """Register active attachments for a tracking ID in one transaction."""
        session_factory = self._require_session_factory()
        timestamp = created_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)

        try:
            with session_factory() as session:
                active_ids = set(
                    session.scalars(
                        select(Attachment.attachment_id).where(
                            Attachment.attachment_id.in_(attachment_ids),
                            Attachment.is_active.is_(True),
                        )
                    )
                )
                if active_ids != set(attachment_ids):
                    raise AttachmentValidationError(
                        "Every attachment_id must exist and be active."
                    )

                rows = [
                    {
                        "id": uuid4(),
                        "tracking_id": tracking_id,
                        "attachment_id": attachment_id,
                        "download_count": 0,
                        "first_download": None,
                        "last_download": None,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                    for attachment_id in attachment_ids
                ]
                statement = (
                    postgresql_insert(TrackingAttachment)
                    .values(rows)
                    .on_conflict_do_nothing(
                        index_elements=[
                            TrackingAttachment.tracking_id,
                            TrackingAttachment.attachment_id,
                        ]
                    )
                    .returning(TrackingAttachment.id)
                )
                created = len(list(session.scalars(statement)))
                session.commit()
                return created
        except AttachmentValidationError:
            raise
        except Exception as exc:
            raise AttachmentLibraryError(
                f"Unable to register attachment mappings: {exc}"
            ) from exc

    def dispose(self) -> None:
        """Release attachment database connections during shutdown."""
        if self._engine is not None:
            self._engine.dispose()

    def _read_file_bytes(self, source: BinaryIO) -> tuple[bytes, int]:
        source.seek(0)
        buffer = BytesIO()
        file_size = 0
        while chunk := source.read(UPLOAD_CHUNK_SIZE):
            file_size += len(chunk)
            if file_size > MAX_ATTACHMENT_SIZE:
                raise AttachmentTooLargeError(
                    "Attachment exceeds the maximum upload size of 50 MB."
                )
            buffer.write(chunk)
        return buffer.getvalue(), file_size

    def _generate_stored_name(
        self, attachment_id: UUID, original_file_name: str
    ) -> str:
        suffix = self._safe_suffix(original_file_name)
        return f"{attachment_id.hex}-{uuid4().hex}{suffix}"

    def _write_unique_file(
        self,
        source: BinaryIO,
        attachment_id: UUID,
        original_file_name: str,
    ) -> tuple[Path, int]:
        self.attachment_folder.mkdir(parents=True, exist_ok=True)
        suffix = self._safe_suffix(original_file_name)

        while True:
            stored_name = f"{attachment_id.hex}-{uuid4().hex}{suffix}"
            stored_path = self.attachment_folder / stored_name
            try:
                destination = stored_path.open("xb")
                break
            except FileExistsError:
                continue

        file_size = 0
        try:
            with destination:
                source.seek(0)
                while chunk := source.read(UPLOAD_CHUNK_SIZE):
                    file_size += len(chunk)
                    if file_size > MAX_ATTACHMENT_SIZE:
                        raise AttachmentTooLargeError(
                            "Attachment exceeds the maximum upload size of 50 MB."
                        )
                    destination.write(chunk)
            return stored_path, file_size
        except Exception:
            stored_path.unlink(missing_ok=True)
            raise

    def _require_engine(self) -> Engine:
        if self._engine is None:
            raise AttachmentLibraryError(
                self._configuration_error or "DATABASE_URL is not configured."
            )
        return self._engine

    def _require_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise AttachmentLibraryError(
                self._configuration_error or "DATABASE_URL is not configured."
            )
        return self._session_factory

    def _resolve_stored_file(self, stored_file_name: str) -> Path:
        folder = self.attachment_folder.resolve()
        file_path = (folder / stored_file_name).resolve()
        if file_path.parent != folder:
            raise AttachmentNotFoundError("Attachment file not found.")
        return file_path

    @staticmethod
    def _normalize_original_name(file_name: str) -> str:
        # Browsers can submit a client path; only its final component is safe.
        normalized = file_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not normalized or normalized in {".", ".."}:
            raise AttachmentValidationError("A valid original filename is required.")
        if len(normalized) > 512:
            raise AttachmentValidationError(
                "The original filename must not exceed 512 characters."
            )
        return normalized

    @staticmethod
    def _safe_suffix(file_name: str) -> str:
        suffix = Path(file_name).suffix.lower()[:20]
        return suffix if re.fullmatch(r"\.[a-z0-9]+", suffix) else ""

    @staticmethod
    def _normalize_database_url(database_url: str) -> str:
        if database_url.startswith("postgres://"):
            return database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if database_url.startswith("postgresql://"):
            return database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        return database_url
