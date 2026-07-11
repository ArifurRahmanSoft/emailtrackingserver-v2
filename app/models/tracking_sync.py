"""Response model for desktop tracking synchronization."""

from datetime import datetime

from pydantic import BaseModel


class TrackingSyncRecord(BaseModel):
    """The minimal tracking fields required by the desktop application."""

    excel_file_path: str | None
    tracking_id: str
    last_synchronize_time: datetime | None
    open_count: int
    click_count: int
    download_count: int
    reply_count: int | None
    first_open: datetime | None
    last_open: datetime | None
    first_click: datetime | None
    last_click: datetime | None
    first_download: datetime | None
    last_download: datetime | None
    first_reply: datetime | None
    last_reply: datetime | None
    updated_at: datetime


class MarkSynchronizedRequest(BaseModel):
    """Request to mark one tracking row synchronized back to Excel."""

    tracking_id: str
    last_synchronize_time: datetime


class MarkSynchronizedResponse(BaseModel):
    """Confirmation returned after synchronization state is stored."""

    success: bool
    tracking_id: str
    last_synchronize_time: datetime
