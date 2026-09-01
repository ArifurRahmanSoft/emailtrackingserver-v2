"""Client-based report list and Excel export services."""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from io import BytesIO
from math import ceil
import re
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.campaign import Campaign
from app.models.client_report import ClientReportFilters, ClientReportResponse
from app.models.email_tracking import EmailTracking


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 20
CLIENT_REPORT_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BANGLADESH_TIMEZONE = ZoneInfo("Asia/Dhaka")
CLIENT_REPORT_EXPORT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class ClientReportError(RuntimeError):
    """Base error for client-report API failures."""


class ClientReportDatabaseUnavailableError(ClientReportError):
    """Raised when client-report storage is unavailable."""


class ClientReportValidationError(ClientReportError):
    """Raised when client-report query filters are invalid."""


@dataclass(frozen=True, slots=True)
class ClientReportExportResult:
    """Generated client-report Excel export."""

    filename: str
    content: bytes
    row_count: int
    content_type: str = CLIENT_REPORT_EXPORT_CONTENT_TYPE


class ClientReportService:
    """Read-only reporting service scoped by campaign client_code."""

    def __init__(self, database_url: str | None) -> None:
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._configuration_error: str | None = None

        if database_url:
            try:
                self._engine = create_engine(
                    self._normalize_database_url(database_url),
                    pool_pre_ping=True,
                    pool_recycle=300,
                    connect_args={"connect_timeout": 10},
                )
                self._session_factory = sessionmaker(
                    bind=self._engine,
                    expire_on_commit=False,
                )
            except Exception as exc:
                self._configuration_error = str(exc)

    def dispose(self) -> None:
        """Dispose the client-report database connection pool."""
        if self._engine is not None:
            self._engine.dispose()

    def get_report(
        self,
        client_code: str,
        page: int = DEFAULT_PAGE,
        per_page: int = DEFAULT_PER_PAGE,
        sender_mail: str | None = None,
        campaign_code: str | None = None,
        project: str | None = None,
        is_bounce: str | None = None,
        is_reply: str | None = None,
        is_open: str | None = None,
        is_click: str | None = None,
        is_download: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> ClientReportResponse:
        """Return one page of email_tracking rows for a single client's campaigns."""
        normalized_page = page if page >= 1 else DEFAULT_PAGE
        normalized_per_page = per_page if per_page > 0 else DEFAULT_PER_PAGE
        filters = self.build_filters(
            client_code=client_code,
            sender_mail=sender_mail,
            campaign_code=campaign_code,
            project=project,
            is_bounce=is_bounce,
            is_reply=is_reply,
            is_open=is_open,
            is_click=is_click,
            is_download=is_download,
            from_date=from_date,
            to_date=to_date,
        )

        session_factory = self._require_session_factory()
        try:
            with session_factory() as session:
                campaign_codes = self._campaign_codes_for_client(
                    session,
                    filters.client_code,
                )
                if filters.campaign_code is not None:
                    campaign_codes = [
                        code for code in campaign_codes if code == filters.campaign_code
                    ]
                if not campaign_codes:
                    return ClientReportResponse(
                        success=True,
                        client_code=filters.client_code,
                        pagination={
                            "page": normalized_page,
                            "per_page": normalized_per_page,
                            "total_records": 0,
                            "total_pages": 0,
                        },
                        data=[],
                    )

                conditions = self._tracking_conditions(filters, campaign_codes)
                total_records = self._count_records(session, conditions)
                total_pages = (
                    ceil(total_records / normalized_per_page) if total_records else 0
                )
                offset = (normalized_page - 1) * normalized_per_page
                rows = self._fetch_records(
                    session,
                    conditions,
                    offset=offset,
                    limit=normalized_per_page,
                )

                return ClientReportResponse(
                    success=True,
                    client_code=filters.client_code,
                    pagination={
                        "page": normalized_page,
                        "per_page": normalized_per_page,
                        "total_records": total_records,
                        "total_pages": total_pages,
                    },
                    data=rows,
                )
        except ClientReportValidationError:
            raise
        except Exception as exc:
            raise ClientReportDatabaseUnavailableError(
                f"Unable to build client report: {exc}"
            ) from exc

    def export_report(
        self,
        client_code: str,
        sender_mail: str | None = None,
        campaign_code: str | None = None,
        project: str | None = None,
        is_bounce: str | None = None,
        is_reply: str | None = None,
        is_open: str | None = None,
        is_click: str | None = None,
        is_download: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        generated_at: datetime | None = None,
    ) -> ClientReportExportResult:
        """Export every filtered client-report row without pagination."""
        filters = self.build_filters(
            client_code=client_code,
            sender_mail=sender_mail,
            campaign_code=campaign_code,
            project=project,
            is_bounce=is_bounce,
            is_reply=is_reply,
            is_open=is_open,
            is_click=is_click,
            is_download=is_download,
            from_date=from_date,
            to_date=to_date,
        )
        timestamp = self._as_utc(generated_at or datetime.now(timezone.utc))
        workbook = Workbook(write_only=True)
        worksheet = workbook.create_sheet(title="Client Report")
        columns = self.report_columns()
        worksheet.append(columns)

        row_count = 0
        session_factory = self._require_session_factory()
        try:
            with session_factory() as session:
                campaign_codes = self._campaign_codes_for_client(
                    session,
                    filters.client_code,
                )
                if filters.campaign_code is not None:
                    campaign_codes = [
                        code for code in campaign_codes if code == filters.campaign_code
                    ]

                if campaign_codes:
                    conditions = self._tracking_conditions(filters, campaign_codes)
                    statement = (
                        select(*list(EmailTracking.__table__.columns))
                        .where(*conditions)
                        .order_by(EmailTracking.created_at.desc())
                    )
                    rows = session.execute(
                        statement.execution_options(
                            stream_results=True,
                            yield_per=1000,
                        )
                    ).mappings()
                    for row in rows:
                        worksheet.append(
                            [self._excel_cell(row[column]) for column in columns]
                        )
                        row_count += 1
        except ClientReportValidationError:
            raise
        except Exception as exc:
            raise ClientReportDatabaseUnavailableError(
                f"Unable to export client report: {exc}"
            ) from exc

        output = BytesIO()
        workbook.save(output)
        filename = f"Client_Report_{timestamp.strftime('%Y%m%d_%H%M%S')}.xlsx"
        return ClientReportExportResult(
            filename=filename,
            content=output.getvalue(),
            row_count=row_count,
        )

    @staticmethod
    def build_filters(
        client_code: str,
        sender_mail: str | None = None,
        campaign_code: str | None = None,
        project: str | None = None,
        is_bounce: str | None = None,
        is_reply: str | None = None,
        is_open: str | None = None,
        is_click: str | None = None,
        is_download: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> ClientReportFilters:
        """Normalize and validate client-report query filters."""
        clean_client_code = client_code.strip() if client_code else None
        if not clean_client_code:
            raise ClientReportValidationError("client_code is required.")

        created_at_from_utc, created_at_to_utc = (
            ClientReportService._bangladesh_date_filter_to_utc_range(
                from_date=from_date,
                to_date=to_date,
            )
        )
        return ClientReportFilters(
            client_code=clean_client_code,
            sender_mail=ClientReportService._clean_optional(sender_mail),
            campaign_code=ClientReportService._clean_optional(campaign_code),
            project=ClientReportService._clean_optional(project),
            is_bounce=ClientReportService._parse_optional_bool(
                is_bounce,
                "is_bounce",
            ),
            is_reply=ClientReportService._parse_optional_bool(is_reply, "is_reply"),
            is_open=ClientReportService._parse_optional_bool(is_open, "is_open"),
            is_click=ClientReportService._parse_optional_bool(is_click, "is_click"),
            is_download=ClientReportService._parse_optional_bool(
                is_download,
                "is_download",
            ),
            created_at_from_utc=created_at_from_utc,
            created_at_to_utc=created_at_to_utc,
        )

    @staticmethod
    def report_columns() -> list[str]:
        """Return all email_tracking database column names for client report output."""
        return [column.name for column in EmailTracking.__table__.columns]

    @staticmethod
    def _campaign_codes_for_client(session: Session, client_code: str) -> list[str]:
        """Return non-empty campaign codes owned by the requested client."""
        return list(
            session.scalars(
                select(Campaign.campaign_code)
                .where(
                    Campaign.client_code == client_code,
                    Campaign.campaign_code.is_not(None),
                    func.trim(Campaign.campaign_code) != "",
                )
                .order_by(Campaign.campaign_code.asc())
            )
        )

    @classmethod
    def _tracking_conditions(
        cls,
        filters: ClientReportFilters,
        campaign_codes: list[str],
    ) -> list[object]:
        """Build SQLAlchemy conditions for client-report filtering."""
        conditions: list[object] = [EmailTracking.campaign_code.in_(campaign_codes)]
        if filters.sender_mail:
            conditions.append(EmailTracking.sender_mail == filters.sender_mail)
        if filters.campaign_code:
            conditions.append(EmailTracking.campaign_code == filters.campaign_code)
        if filters.project:
            conditions.append(EmailTracking.project_name == filters.project)
        if filters.is_bounce is not None:
            conditions.append(EmailTracking.is_bounce == (1 if filters.is_bounce else 0))
        if filters.is_reply is True:
            conditions.append(func.coalesce(EmailTracking.reply_count, 0) > 0)
        elif filters.is_reply is False:
            conditions.append(func.coalesce(EmailTracking.reply_count, 0) == 0)
        if filters.is_open is True:
            conditions.append(func.coalesce(EmailTracking.open_count, 0) > 0)
        elif filters.is_open is False:
            conditions.append(func.coalesce(EmailTracking.open_count, 0) == 0)
        if filters.is_click is True:
            conditions.append(func.coalesce(EmailTracking.click_count, 0) > 0)
        elif filters.is_click is False:
            conditions.append(func.coalesce(EmailTracking.click_count, 0) == 0)
        if filters.is_download is True:
            conditions.append(func.coalesce(EmailTracking.download_count, 0) > 0)
        elif filters.is_download is False:
            conditions.append(func.coalesce(EmailTracking.download_count, 0) == 0)
        if filters.created_at_from_utc is not None:
            conditions.append(EmailTracking.created_at >= filters.created_at_from_utc)
        if filters.created_at_to_utc is not None:
            conditions.append(EmailTracking.created_at <= filters.created_at_to_utc)
        return conditions

    @staticmethod
    def _count_records(session: Session, conditions: list[object]) -> int:
        """Count filtered records in the database."""
        return int(
            session.scalar(select(func.count(EmailTracking.id)).where(*conditions))
            or 0
        )

    @staticmethod
    def _fetch_records(
        session: Session,
        conditions: list[object],
        offset: int,
        limit: int,
    ) -> list[dict[str, object]]:
        """Fetch one filtered page without loading unrelated rows."""
        statement = (
            select(*list(EmailTracking.__table__.columns))
            .where(*conditions)
            .order_by(EmailTracking.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return [dict(row) for row in session.execute(statement).mappings()]

    @staticmethod
    def _parse_optional_bool(value: str | None, field_name: str) -> bool | None:
        """Parse optional boolean query strings, accepting true/false and 1/0."""
        if value is None:
            return None
        cleaned = value.strip().lower()
        if not cleaned:
            return None
        if cleaned in {"true", "1"}:
            return True
        if cleaned in {"false", "0"}:
            return False
        raise ClientReportValidationError(f"{field_name} must be true/false or 1/0.")

    @staticmethod
    def _excel_cell(value: object) -> object:
        """Convert values into Excel-safe cell values."""
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        """Trim optional strings and treat blanks as absent filters."""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _bangladesh_date_filter_to_utc_range(
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> tuple[datetime | None, datetime | None]:
        """Convert Bangladesh date filters into UTC datetime bounds."""
        clean_from = from_date.strip() if from_date else None
        clean_to = to_date.strip() if to_date else None
        if not clean_from and not clean_to:
            return None, None

        start_date = (
            ClientReportService._parse_report_date(clean_from, "from_date")
            if clean_from
            else None
        )
        end_date = (
            ClientReportService._parse_report_date(clean_to, "to_date")
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
            raise ClientReportValidationError(
                "to_date must be greater than or equal to from_date."
            )

        local_start = datetime.combine(start_date, time.min, tzinfo=BANGLADESH_TIMEZONE)
        local_end = datetime.combine(end_date, time.max, tzinfo=BANGLADESH_TIMEZONE)
        return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)

    @staticmethod
    def _parse_report_date(value: str | None, field_name: str) -> date:
        """Parse strict YYYY-MM-DD date filters."""
        if value is None or not CLIENT_REPORT_DATE_PATTERN.fullmatch(value):
            raise ClientReportValidationError(
                f"{field_name} must use YYYY-MM-DD format."
            )
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ClientReportValidationError(
                f"{field_name} must be a valid calendar date."
            ) from exc

    @staticmethod
    def _normalize_database_url(database_url: str) -> str:
        """Use psycopg 3 for normal PostgreSQL URLs."""
        if database_url.startswith("postgres://"):
            return database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if database_url.startswith("postgresql://"):
            return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return database_url

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Return a timezone-aware UTC datetime."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _require_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise ClientReportDatabaseUnavailableError(
                self._configuration_error or "DATABASE_URL is not configured."
            )
        return self._session_factory
