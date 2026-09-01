"""Paginated reporting service for tracking rows."""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from io import BytesIO
from math import ceil
import re
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
REPORT_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REPORT_RESPONSE_TIMEZONE = ZoneInfo("Asia/Dhaka")
REPORT_EXPORT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
REPORT_EXPORT_BST_DATETIME_COLUMNS = {
    "last_synchronize_time",
    "first_reply",
    "last_reply",
    "bounce_time",
    "first_open",
    "last_open",
    "first_click",
    "last_click",
    "first_download",
    "last_download",
    "unsubscribe_time",
    "created_at",
    "updated_at",
}
REPORT_EXPORT_APPEND_COLUMNS = (
    ("Unsubscribe", "unsubscribe"),
    ("Unsubscribe Time", "unsubscribe_time"),
)


@dataclass(frozen=True, slots=True)
class ReportExportResult:
    """Generated Excel report export."""

    filename: str
    content: bytes
    row_count: int
    content_type: str = REPORT_EXPORT_CONTENT_TYPE


class ReportFilterValidationError(ValueError):
    """Raised when report query filters cannot be validated."""


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
        campaign_code: str | None = None,
        bounce: str | None = None,
        unsubscribe: str | None = None,
        is_reply: bool = False,
        is_bounce: bool = False,
        is_open: bool = False,
        is_click: bool = False,
        is_download: bool = False,
        from_date: str | None = None,
        to_date: str | None = None,
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
            campaign_code=campaign_code,
            bounce=bounce,
            unsubscribe=unsubscribe,
            is_reply=is_reply,
            is_bounce=is_bounce,
            is_open=is_open,
            is_click=is_click,
            is_download=is_download,
            from_date=from_date,
            to_date=to_date,
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
        campaign_code: str | None = None,
        bounce: str | None = None,
        unsubscribe: str | None = None,
        is_reply: bool = False,
        is_bounce: bool = False,
        is_open: bool = False,
        is_click: bool = False,
        is_download: bool = False,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> ReportFilters:
        """Normalize optional filter values while treating empty strings as absent."""
        clean_sender = sender_email.strip() if sender_email else None
        clean_project = project_name.strip() if project_name else None
        clean_campaign_code = campaign_code.strip() if campaign_code else None
        parsed_bounce = ReportingService._parse_optional_bool(bounce, "bounce")
        parsed_unsubscribe = ReportingService._parse_optional_bool(
            unsubscribe,
            "unsubscribe",
            invalid_message="Invalid unsubscribe filter value.",
        )
        created_at_from_utc, created_at_to_utc = (
            ReportingService._bangladesh_date_filter_to_utc_range(
                from_date=from_date,
                to_date=to_date,
            )
        )
        return ReportFilters(
            sender_email=clean_sender or None,
            project_name=clean_project or None,
            campaign_code=clean_campaign_code or None,
            bounce=parsed_bounce,
            unsubscribe=parsed_unsubscribe,
            is_reply=bool(is_reply),
            is_bounce=bool(is_bounce),
            is_open=bool(is_open),
            is_click=bool(is_click),
            is_download=bool(is_download),
            created_at_from_utc=created_at_from_utc,
            created_at_to_utc=created_at_to_utc,
        )

    @staticmethod
    def _parse_optional_bool(
        value: str | None,
        field_name: str,
        invalid_message: str | None = None,
    ) -> bool | None:
        """Parse optional strict true/false query values."""
        if value is None:
            return None

        cleaned = value.strip().lower()
        if not cleaned:
            return None
        if cleaned == "true":
            return True
        if cleaned == "false":
            return False
        raise ReportFilterValidationError(
            invalid_message or f"{field_name} must be true or false."
        )

    @staticmethod
    def _bangladesh_date_filter_to_utc_range(
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> tuple[datetime | None, datetime | None]:
        """Convert Bangladesh calendar date filters into UTC datetime bounds."""
        clean_from = from_date.strip() if from_date else None
        clean_to = to_date.strip() if to_date else None
        if not clean_from and not clean_to:
            return None, None

        start_date = (
            ReportingService._parse_report_date(clean_from, "from_date")
            if clean_from
            else None
        )
        end_date = (
            ReportingService._parse_report_date(clean_to, "to_date")
            if clean_to
            else None
        )
        if start_date is None:
            start_date = end_date
        if end_date is None:
            end_date = start_date
        if start_date is None or end_date is None:
            return None, None
        if end_date < start_date:
            raise ReportFilterValidationError(
                "to_date must be greater than or equal to from_date."
            )

        local_start = datetime.combine(
            start_date,
            time.min,
            tzinfo=REPORT_RESPONSE_TIMEZONE,
        )
        local_end = datetime.combine(
            end_date,
            time.max,
            tzinfo=REPORT_RESPONSE_TIMEZONE,
        )
        return (
            local_start.astimezone(timezone.utc),
            local_end.astimezone(timezone.utc),
        )

    @staticmethod
    def _parse_report_date(value: str | None, field_name: str) -> date:
        """Parse strict YYYY-MM-DD report dates."""
        if value is None or not REPORT_DATE_PATTERN.fullmatch(value):
            raise ReportFilterValidationError(
                f"{field_name} must use YYYY-MM-DD format."
            )
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ReportFilterValidationError(
                f"{field_name} must be a valid calendar date."
            ) from exc

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
        from_date: str | None = None,
        to_date: str | None = None,
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
            from_date=from_date,
            to_date=to_date,
        )

        workbook = Workbook(write_only=True)
        worksheet = workbook.create_sheet(title="Report")
        source_columns = self._database_service.report_export_columns()
        base_columns = [
            column
            for column in source_columns
            if column not in {"unsubscribe", "unsubscribe_time"}
        ]
        worksheet.append(
            base_columns + [label for label, _ in REPORT_EXPORT_APPEND_COLUMNS]
        )

        row_count = 0
        for row in self._database_service.iter_report_export_records(filters):
            worksheet.append(
                [self._excel_cell(column, row.get(column)) for column in base_columns]
                + [
                    self._excel_cell(source_column, row.get(source_column))
                    for _, source_column in REPORT_EXPORT_APPEND_COLUMNS
                ]
            )
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
    def _excel_cell(column: str, value: object) -> object:
        """Convert values into Excel-safe cell values."""
        if column in REPORT_EXPORT_BST_DATETIME_COLUMNS and isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(REPORT_RESPONSE_TIMEZONE).replace(tzinfo=None)
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
