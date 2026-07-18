"""Response models for statistics endpoints."""

from pydantic import BaseModel
from datetime import datetime


class SampleStatistics(BaseModel):
    """Placeholder statistics returned during Phase 1."""

    status: str
    total_opens: int
    total_clicks: int
    message: str


class DashboardStatisticsResponse(BaseModel):
    """Aggregate dashboard totals calculated from PostgreSQL."""

    total_sent: int
    total_open: int
    total_click: int
    total_download: int
    total_reply: int
    total_bounce: int
    total_open_by_mail: int
    total_click_by_mail: int
    total_download_by_mail: int
    total_reply_by_mail: int
    weekly_sent: int
    monthly_sent: int
    success_rate: float
    failure_rate: float
    last_updated: datetime
