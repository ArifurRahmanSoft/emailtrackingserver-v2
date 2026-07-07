"""Response model for desktop tracking synchronization."""

from datetime import datetime

from pydantic import BaseModel


class TrackingSyncRecord(BaseModel):
    """The minimal tracking fields required by the desktop application."""

    tracking_id: str
    open_count: int
    click_count: int
    download_count: int
    first_open: datetime | None
    last_open: datetime | None
    first_click: datetime | None
    last_click: datetime | None
    first_download: datetime | None
    last_download: datetime | None
    updated_at: datetime
