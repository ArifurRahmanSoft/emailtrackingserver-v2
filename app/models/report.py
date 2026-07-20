"""Response models for paginated tracking reports."""

from datetime import datetime

from pydantic import BaseModel


class ReportItem(BaseModel):
    """One tracking row returned by the reporting API."""

    tracking_id: str
    sender_email: str | None
    receiver_email: str | None
    project_name: str | None
    send_date: datetime
    open_count: int
    click_count: int
    download_count: int
    reply_count: int
    is_bounce: bool


class ReportResponse(BaseModel):
    """Paginated report response."""

    page: int
    page_size: int
    total_records: int
    total_pages: int
    has_next_page: bool
    has_previous_page: bool
    items: list[ReportItem]
