"""SQLAlchemy model for independent campaign management records."""

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, String, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class CampaignBase(DeclarativeBase):
    """Isolated metadata for campaign-management tables."""


class Campaign(CampaignBase):
    """Campaign-level metadata kept separate from email tracking rows."""

    __tablename__ = "campaigns"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    campaign_name: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    campaign_offer: Mapped[str | None] = mapped_column(Text, nullable=True)
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
