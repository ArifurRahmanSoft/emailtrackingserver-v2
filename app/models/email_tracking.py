"""SQLAlchemy ORM model for email tracking records."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for application database models."""


class EmailTracking(Base):
    """Persistent email open and click counters."""

    __tablename__ = "email_tracking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tracking_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_open: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_open: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_click: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_click: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(1024), nullable=True)
