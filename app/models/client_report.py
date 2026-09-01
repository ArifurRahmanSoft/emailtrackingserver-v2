"""Request-independent models for client-based report APIs."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class ClientReportFilters:
    """Validated client-report filters applied before pagination/export."""

    client_code: str
    sender_mail: str | None = None
    campaign_code: str | None = None
    project: str | None = None
    is_bounce: bool | None = None
    is_reply: bool | None = None
    is_open: bool | None = None
    is_click: bool | None = None
    is_download: bool | None = None
    created_at_from_utc: datetime | None = None
    created_at_to_utc: datetime | None = None


class ClientReportPagination(BaseModel):
    """Pagination metadata for client-report list responses."""

    page: int
    per_page: int
    total_records: int
    total_pages: int


class ClientReportResponse(BaseModel):
    """Paginated client-based report rows."""

    success: bool
    client_code: str
    pagination: ClientReportPagination
    data: list[dict[str, Any]]
