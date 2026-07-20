"""Paginated reporting service for tracking rows."""

from math import ceil

from app.models.report import ReportResponse
from app.services.database_tracking import DatabaseTrackingService


DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class ReportingService:
    """Build paginated report responses from PostgreSQL."""

    def __init__(self, database_service: DatabaseTrackingService) -> None:
        self._database_service = database_service

    def get_report(self, page: int = DEFAULT_PAGE, page_size: int = DEFAULT_PAGE_SIZE) -> ReportResponse:
        """Return one report page using server-side pagination."""
        normalized_page = page if page >= 1 else DEFAULT_PAGE
        if page_size <= 0:
            normalized_page_size = DEFAULT_PAGE_SIZE
        else:
            normalized_page_size = min(page_size, MAX_PAGE_SIZE)

        total_records = self._database_service.count_report_records()
        total_pages = ceil(total_records / normalized_page_size) if total_records else 0
        offset = (normalized_page - 1) * normalized_page_size
        rows = self._database_service.fetch_report_records(
            offset=offset,
            limit=normalized_page_size,
        )

        return ReportResponse(
            page=normalized_page,
            page_size=normalized_page_size,
            total_records=total_records,
            total_pages=total_pages,
            has_next_page=normalized_page < total_pages,
            has_previous_page=normalized_page > 1 and total_pages > 0,
            items=rows,
        )
