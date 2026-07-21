"""Paginated reporting service for tracking rows."""

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from math import ceil
from zoneinfo import ZoneInfo

from openpyxl import Workbook

from app.models.report import (
    ReportFilterOptionsResponse,
    ReportFilters,
    ReportResponse,
)
from app.services.database_tracking import DatabaseTrackingService


DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
REPORT_RESPONSE_TIMEZONE = ZoneInfo("Asia/Dhaka")
REPORT_EXPORT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


@dataclass(frozen=True, slots=True)
class ReportExportResult:
    """Generated Excel report export."""

    filename: str
    content: bytes
    row_count: int
    content_type: str = REPORT_EXPORT_CONTENT_TYPE


class ReportingService:
    """Build paginated report responses from PostgreSQL."""

    def __init__(self, database_service: DatabaseTrackingService) -> None:
        self._database_service = database_service

    def get_report(
        self,
        page: int = DEFAULT_PAGE,
        page_size: int = DEFAULT_PAGE_SIZE,
        sender_email: str | None = None,
        project_name: str | None = None,
        is_reply: bool = False,
        is_bounce: bool = False,
        is_open: bool = False,
        is_click: bool = False,
        is_download: bool = False,
    ) -> ReportResponse:
        """Return one report page using server-side pagination."""
        normalized_page = page if page >= 1 else DEFAULT_PAGE
        if page_size <= 0:
            normalized_page_size = DEFAULT_PAGE_SIZE
        else:
            normalized_page_size = min(page_size, MAX_PAGE_SIZE)

        filters = self.build_filters(
            sender_email=sender_email,
            project_name=project_name,
            is_reply=is_reply,
            is_bounce=is_bounce,
            is_open=is_open,
            is_click=is_click,
            is_download=is_download,
        )

        total_records = self._database_service.count_report_records(filters)
        total_pages = ceil(total_records / normalized_page_size) if total_records else 0
        offset = (normalized_page - 1) * normalized_page_size
        rows = self._database_service.fetch_report_records(
            offset=offset,
            limit=normalized_page_size,
            filters=filters,
        )
        response_rows = [self._report_response_row(row) for row in rows]

        return ReportResponse(
            page=normalized_page,
            page_size=normalized_page_size,
            total_records=total_records,
            total_pages=total_pages,
            has_next_page=normalized_page < total_pages,
            has_previous_page=normalized_page > 1 and total_pages > 0,
            items=response_rows,
        )

    @staticmethod
    def build_filters(
        sender_email: str | None = None,
        project_name: str | None = None,
        is_reply: bool = False,
        is_bounce: bool = False,
        is_open: bool = False,
        is_click: bool = False,
        is_download: bool = False,
    ) -> ReportFilters:
        """Normalize optional filter values while treating empty strings as absent."""
        clean_sender = sender_email.strip() if sender_email else None
        clean_project = project_name.strip() if project_name else None
        return ReportFilters(
            sender_email=clean_sender or None,
            project_name=clean_project or None,
            is_reply=bool(is_reply),
            is_bounce=bool(is_bounce),
            is_open=bool(is_open),
            is_click=bool(is_click),
            is_download=bool(is_download),
        )

    def get_filter_options(self) -> ReportFilterOptionsResponse:
        """Return distinct report filter dropdown options."""
        options = self._database_service.fetch_report_filter_options()
        return ReportFilterOptionsResponse(
            sender_emails=options["sender_emails"],
            project_names=options["project_names"],
        )

    def export_report(
        self,
        sender_email: str | None = None,
        project_name: str | None = None,
        is_reply: bool = False,
        is_bounce: bool = False,
        is_open: bool = False,
        is_click: bool = False,
        is_download: bool = False,
        generated_at: datetime | None = None,
    ) -> ReportExportResult:
        """Generate an Excel export for every filtered report row."""
        timestamp = generated_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)

        filters = self.build_filters(
            sender_email=sender_email,
            project_name=project_name,
            is_reply=is_reply,
            is_bounce=is_bounce,
            is_open=is_open,
            is_click=is_click,
            is_download=is_download,
        )

        workbook = Workbook(write_only=True)
        worksheet = workbook.create_sheet(title="Report")
        columns = self._database_service.report_export_columns()
        worksheet.append(columns)

        row_count = 0
        for row in self._database_service.iter_report_export_records(filters):
            worksheet.append([self._excel_cell(row.get(column)) for column in columns])
            row_count += 1

        output = BytesIO()
        workbook.save(output)
        filename = f"Report_{timestamp.strftime('%Y%m%d_%H%M%S')}.xlsx"
        return ReportExportResult(
            filename=filename,
            content=output.getvalue(),
            row_count=row_count,
        )

    @staticmethod
    def _excel_cell(value: object) -> object:
        """Convert values into Excel-safe cell values."""
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            return value
        return value

    @classmethod
    def _report_response_row(cls, row: dict[str, object]) -> dict[str, object]:
        """Convert only report response send_date from stored UTC to Asia/Dhaka."""
        response_row = dict(row)
        send_date = response_row.get("send_date")
        if isinstance(send_date, datetime):
            if send_date.tzinfo is None:
                send_date = send_date.replace(tzinfo=timezone.utc)
            response_row["send_date"] = send_date.astimezone(REPORT_RESPONSE_TIMEZONE)
        return response_row
