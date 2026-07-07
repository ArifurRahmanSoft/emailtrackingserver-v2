"""SQLAlchemy model for the server-side attachment library."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    LargeBinary,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AttachmentBase(DeclarativeBase):
    """Isolated metadata for attachment-library tables."""


class Attachment(AttachmentBase):
    """Metadata for one uploaded attachment file."""

    __tablename__ = "attachments"

    attachment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    original_file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_file_name: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class TrackingAttachment(AttachmentBase):
    """Download counters for one tracking and attachment pair."""

    __tablename__ = "tracking_attachments"
    __table_args__ = (
        UniqueConstraint(
            "tracking_id",
            "attachment_id",
            name="uq_tracking_attachments_pair",
        ),
        Index("ix_tracking_attachments_tracking_id", "tracking_id"),
        Index("ix_tracking_attachments_attachment_id", "attachment_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    tracking_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attachment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    first_download: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_download: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
