"""PostgreSQL persistence for email tracking events."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from pathlib import PurePath, PureWindowsPath

from sqlalchemy import Engine, case, create_engine, func, inspect, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.models.attachment import TrackingAttachment
from app.models.email_tracking import Base, EmailTracking
from app.services.alembic_migrations import run_pending_migrations

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(RuntimeError):
    """Raised when PostgreSQL is not configured or cannot complete an operation."""


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    """Read-only database health details."""

    connected: bool
    table_exists: bool
    total_records: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ClickUpdateResult:
    """Result of a successful click update."""

    click_count: int
    first_click: datetime
    last_click: datetime


@dataclass(frozen=True, slots=True)
class ReplyUpdateResult:
    """Result of a successful reply update."""

    tracking_id: str
    reply_count: int
    first_reply: datetime
    last_reply: datetime
    database_primary_key: int | None = None
    reply_count_before_update: int | None = None


@dataclass(frozen=True, slots=True)
class BounceUpdateResult:
    """Result of a successful bounce update."""

    message_id: str
    tracking_id: str
    is_bounce: int
    bounce_time: datetime
    bounce_reason: str | None
    database_primary_key: int | None = None
    is_bounce_before_update: int | None = None


@dataclass(frozen=True, slots=True)
class SentEmailRegistration:
    """Metadata captured by Version 2 immediately after a successful send."""

    tracking_id: str
    sender_mail: str | None = None
    recipient_mail: str | None = None
    mail_subject: str | None = None
    project_name: str | None = None
    excel_file_path: str | None = None
    message_id: str | None = None


@dataclass(frozen=True, slots=True)
class SentEmailRegistrationResult:
    """Result of registering one sent email tracking row."""

    tracking_id: str
    excel_file_name: str | None


class DatabaseTrackingService:
    """Create the schema and mirror successful Excel tracking writes."""

    def __init__(self, database_url: str | None) -> None:
        self._database_url = database_url
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._configuration_error: str | None = None

        if database_url:
            try:
                sqlalchemy_url = self._normalize_database_url(database_url)
                self._engine = create_engine(
                    sqlalchemy_url,
                    pool_pre_ping=True,
                    pool_recycle=300,
                    connect_args={"connect_timeout": 10},
                )
                self._session_factory = sessionmaker(
                    bind=self._engine,
                    expire_on_commit=False,
                )
            except Exception as exc:
                # A malformed database setting must not prevent Excel tracking.
                self._configuration_error = str(exc)

    @property
    def configured(self) -> bool:
        """Return whether DATABASE_URL was provided."""
        return self._engine is not None

    def initialize(self) -> None:
        """Create base tables, run pending migrations, and verify the connection."""
        engine = self._require_engine()
        Base.metadata.create_all(engine)
        run_pending_migrations(self._database_url or str(engine.url))
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def record_open(
        self,
        tracking_id: str,
        open_count: int,
        client_ip: str,
        user_agent: str,
        occurred_at: datetime,
    ) -> bool:
        """Update an existing tracking row for one recipient open.

        Version 2 send registration creates the row. Open tracking must only
        update counters and timestamps for that existing ``tracking_id``.
        """
        session_factory = self._require_session_factory()
        timestamp = self._as_utc(occurred_at)

        try:
            with session_factory() as session:
                record = session.scalar(
                    select(EmailTracking)
                    .where(EmailTracking.tracking_id == tracking_id)
                    .with_for_update()
                )
                if record is None:
                    return False

                record.open_count = (record.open_count or 0) + 1
                if record.first_open is None:
                    record.first_open = timestamp
                record.last_open = timestamp
                record.updated_at = timestamp
                session.commit()
                return True
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to update PostgreSQL tracking record: {exc}"
            ) from exc

    def record_click(
        self,
        tracking_id: str,
        client_ip: str,
        user_agent: str,
        occurred_at: datetime,
    ) -> ClickUpdateResult | None:
        """Update an existing tracking row for one recipient click.

        The row is locked until commit so concurrent clicks cannot lose count
        increments or overwrite the original ``first_click`` timestamp.
        """
        session_factory = self._require_session_factory()
        timestamp = self._as_utc(occurred_at)

        try:
            with session_factory() as session:
                record = session.scalar(
                    select(EmailTracking)
                    .where(EmailTracking.tracking_id == tracking_id)
                    .with_for_update()
                )
                if record is None:
                    return None

                record.click_count = (record.click_count or 0) + 1
                if record.first_click is None:
                    record.first_click = timestamp
                record.last_click = timestamp
                record.last_ip = client_ip
                record.user_agent = user_agent
                record.updated_at = timestamp
                session.commit()

                return ClickUpdateResult(
                    click_count=record.click_count,
                    first_click=record.first_click,
                    last_click=record.last_click,
                )
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to update PostgreSQL click record: {exc}"
            ) from exc

    def record_reply(
        self,
        message_id: str,
        occurred_at: datetime | None = None,
    ) -> ReplyUpdateResult | None:
        """Update an existing tracking row for one recipient reply.

        The update is a single atomic statement so concurrent reply events
        cannot lose increments. No row is created when ``message_id`` is absent.
        """
        session_factory = self._require_session_factory()
        timestamp = self._as_utc(occurred_at or datetime.now(timezone.utc))
        message_id = message_id.strip()

        try:
            with session_factory() as session:
                lookup = session.execute(
                    select(
                        EmailTracking.id,
                        EmailTracking.tracking_id,
                        EmailTracking.reply_count,
                    ).where(EmailTracking.message_id == message_id)
                ).one_or_none()
                logger.info(
                    "register-reply database row lookup: found=%s message_id=%s "
                    "tracking_id=%s database_primary_key=%s",
                    lookup is not None,
                    message_id,
                    lookup.tracking_id if lookup is not None else None,
                    lookup.id if lookup is not None else None,
                )
                if lookup is None:
                    return None

                database_primary_key = lookup.id
                tracking_id = lookup.tracking_id
                reply_count_before_update = lookup.reply_count or 0
                result = session.execute(
                    update(EmailTracking)
                    .where(EmailTracking.message_id == message_id)
                    .values(
                        reply_count=func.coalesce(EmailTracking.reply_count, 0) + 1,
                        first_reply=func.coalesce(
                            EmailTracking.first_reply,
                            timestamp,
                        ),
                        last_reply=timestamp,
                        updated_at=timestamp,
                    )
                )
                if result.rowcount == 0:
                    session.rollback()
                    return None

                record = session.execute(
                    select(
                        EmailTracking.reply_count,
                        EmailTracking.first_reply,
                        EmailTracking.last_reply,
                    ).where(EmailTracking.message_id == message_id)
                ).one_or_none()
                session.commit()

                if record is None:
                    return None
                reply_count, first_reply, last_reply = record
                logger.info(
                    "register-reply database update committed: message_id=%s "
                    "tracking_id=%s database_primary_key=%s reply_count_before=%d "
                    "reply_count_after=%d commit_status=success",
                    message_id,
                    tracking_id,
                    database_primary_key,
                    reply_count_before_update,
                    reply_count or 0,
                )
                return ReplyUpdateResult(
                    tracking_id=tracking_id,
                    reply_count=reply_count or 0,
                    first_reply=first_reply,
                    last_reply=last_reply,
                    database_primary_key=database_primary_key,
                    reply_count_before_update=reply_count_before_update,
                )
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to update PostgreSQL reply record: {exc}"
            ) from exc

    def record_bounce(
        self,
        message_id: str,
        bounce_reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> BounceUpdateResult | None:
        """Mark an existing tracking row as bounced using SMTP Message-ID."""
        session_factory = self._require_session_factory()
        timestamp = self._as_utc(occurred_at or datetime.now(timezone.utc))
        message_id = message_id.strip()

        try:
            with session_factory() as session:
                lookup = session.execute(
                    select(
                        EmailTracking.id,
                        EmailTracking.tracking_id,
                        EmailTracking.is_bounce,
                    ).where(EmailTracking.message_id == message_id)
                ).one_or_none()
                logger.info(
                    "register-bounce database row lookup: found=%s message_id=%s "
                    "tracking_id=%s database_primary_key=%s",
                    lookup is not None,
                    message_id,
                    lookup.tracking_id if lookup is not None else None,
                    lookup.id if lookup is not None else None,
                )
                if lookup is None:
                    return None

                database_primary_key = lookup.id
                tracking_id = lookup.tracking_id
                is_bounce_before_update = lookup.is_bounce or 0
                result = session.execute(
                    update(EmailTracking)
                    .where(EmailTracking.message_id == message_id)
                    .values(
                        is_bounce=1,
                        bounce_time=func.coalesce(
                            EmailTracking.bounce_time,
                            timestamp,
                        ),
                        bounce_reason=bounce_reason,
                        updated_at=timestamp,
                    )
                )
                if result.rowcount == 0:
                    session.rollback()
                    return None

                record = session.execute(
                    select(
                        EmailTracking.is_bounce,
                        EmailTracking.bounce_time,
                        EmailTracking.bounce_reason,
                    ).where(EmailTracking.message_id == message_id)
                ).one_or_none()
                session.commit()

                if record is None:
                    return None
                is_bounce, bounce_time, stored_reason = record
                logger.info(
                    "register-bounce database update committed: message_id=%s "
                    "tracking_id=%s database_primary_key=%s is_bounce_before=%d "
                    "is_bounce_after=%d bounce_reason=%s commit_status=success",
                    message_id,
                    tracking_id,
                    database_primary_key,
                    is_bounce_before_update,
                    is_bounce or 0,
                    stored_reason,
                )
                return BounceUpdateResult(
                    message_id=message_id,
                    tracking_id=tracking_id,
                    is_bounce=is_bounce or 0,
                    bounce_time=bounce_time,
                    bounce_reason=stored_reason,
                    database_primary_key=database_primary_key,
                    is_bounce_before_update=is_bounce_before_update,
                )
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to update PostgreSQL bounce record: {exc}"
            ) from exc

    def register_sent_email(
        self,
        registration: SentEmailRegistration,
        registered_at: datetime | None = None,
    ) -> SentEmailRegistrationResult:
        """Insert or update the tracking row after a successful V2 email send.

        Existing open, click, and download counters are intentionally left
        unchanged. New V2 metadata remains nullable so older send clients and
        historical rows stay backward compatible.
        """
        tracking_id = registration.tracking_id.strip()
        if not tracking_id:
            raise ValueError("tracking_id must not be empty.")

        session_factory = self._require_session_factory()
        timestamp = self._as_utc(registered_at or datetime.now(timezone.utc))
        excel_file_path = self._clean_optional(registration.excel_file_path)
        excel_file_name = self._derive_excel_file_name(excel_file_path)
        message_id = self._clean_optional(registration.message_id)

        try:
            with session_factory() as session:
                record = session.scalar(
                    select(EmailTracking)
                    .where(EmailTracking.tracking_id == tracking_id)
                    .with_for_update()
                )
                if record is None:
                    record = EmailTracking(
                        tracking_id=tracking_id,
                        open_count=0,
                        click_count=0,
                        created_at=timestamp,
                    )
                    session.add(record)

                record.sender_email = self._clean_optional(
                    registration.sender_mail
                )
                record.sender_mail = self._clean_optional(
                    registration.sender_mail
                )
                record.recipient_email = self._clean_optional(
                    registration.recipient_mail
                )
                record.mail_subject = self._clean_optional(
                    registration.mail_subject
                )
                record.project_name = self._clean_optional(
                    registration.project_name
                )
                record.excel_file_path = excel_file_path
                record.excel_file_name = excel_file_name
                if message_id is not None and record.message_id is None:
                    record.message_id = message_id
                record.last_synchronize_time = None
                record.updated_at = timestamp
                session.commit()

                return SentEmailRegistrationResult(
                    tracking_id=tracking_id,
                    excel_file_name=excel_file_name,
                )
        except ValueError:
            raise
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to register sent email tracking row: {exc}"
            ) from exc

    def get_status(self) -> DatabaseStatus:
        """Return connection, table, and row-count diagnostics."""
        if self._engine is None or self._session_factory is None:
            return DatabaseStatus(
                connected=False,
                table_exists=False,
                total_records=0,
                error=(
                    self._configuration_error or "DATABASE_URL is not configured."
                ),
            )

        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            table_exists = inspect(self._engine).has_table(EmailTracking.__tablename__)
            total_records = 0
            if table_exists:
                with self._session_factory() as session:
                    total_records = int(
                        session.scalar(select(func.count()).select_from(EmailTracking))
                        or 0
                    )
            return DatabaseStatus(
                connected=True,
                table_exists=table_exists,
                total_records=total_records,
            )
        except Exception as exc:
            return DatabaseStatus(
                connected=False,
                table_exists=False,
                total_records=0,
                error=str(exc),
            )

    def fetch_sync_records(
        self,
        updated_after: datetime | None = None,
    ) -> list[dict[str, object]]:
        """Return only desktop synchronization fields ordered by update time."""
        session_factory = self._require_session_factory()
        statement = select(
            EmailTracking.excel_file_path,
            EmailTracking.tracking_id,
            EmailTracking.last_synchronize_time,
            EmailTracking.open_count,
            EmailTracking.click_count,
            func.coalesce(
                func.sum(TrackingAttachment.download_count), 0
            ).label("download_count"),
            EmailTracking.reply_count,
            EmailTracking.first_open,
            EmailTracking.last_open,
            EmailTracking.first_click,
            EmailTracking.last_click,
            func.min(TrackingAttachment.first_download).label("first_download"),
            func.max(TrackingAttachment.last_download).label("last_download"),
            EmailTracking.first_reply,
            EmailTracking.last_reply,
            EmailTracking.is_bounce,
            EmailTracking.bounce_time,
            EmailTracking.bounce_reason,
            EmailTracking.updated_at,
        ).outerjoin(
            TrackingAttachment,
            TrackingAttachment.tracking_id == EmailTracking.tracking_id,
        )
        if updated_after is not None:
            statement = statement.where(
                EmailTracking.updated_at > self._as_utc(updated_after)
            )
        statement = statement.group_by(
            EmailTracking.excel_file_path,
            EmailTracking.tracking_id,
            EmailTracking.last_synchronize_time,
            EmailTracking.open_count,
            EmailTracking.click_count,
            EmailTracking.reply_count,
            EmailTracking.first_open,
            EmailTracking.last_open,
            EmailTracking.first_click,
            EmailTracking.last_click,
            EmailTracking.first_reply,
            EmailTracking.last_reply,
            EmailTracking.is_bounce,
            EmailTracking.bounce_time,
            EmailTracking.bounce_reason,
            EmailTracking.updated_at,
        ).order_by(EmailTracking.updated_at.asc())

        try:
            with session_factory() as session:
                rows = session.execute(
                    statement.execution_options(stream_results=True, yield_per=1000)
                ).mappings()
                return [
                    {
                        **dict(row),
                        "is_bounce": "Yes" if row["is_bounce"] == 1 else "No",
                    }
                    for row in rows
                ]
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to fetch PostgreSQL synchronization records: {exc}"
            ) from exc

    def fetch_dashboard_statistics_totals(
        self,
        generated_at: datetime | None = None,
    ) -> dict[str, int]:
        """Return dashboard summary totals using SQL aggregate functions only."""
        session_factory = self._require_session_factory()
        timestamp = self._as_utc(generated_at or datetime.now(timezone.utc))
        weekly_threshold = timestamp - timedelta(days=7)
        monthly_threshold = timestamp - timedelta(days=30)

        statement = select(
            func.count(EmailTracking.id).label("total_sent"),
            func.coalesce(func.sum(EmailTracking.open_count), 0).label("total_open"),
            func.coalesce(func.sum(EmailTracking.click_count), 0).label("total_click"),
            func.coalesce(func.sum(EmailTracking.download_count), 0).label(
                "total_download"
            ),
            func.coalesce(func.sum(EmailTracking.reply_count), 0).label("total_reply"),
            func.count(
                case((EmailTracking.open_count > 0, 1))
            ).label("total_open_by_mail"),
            func.count(
                case((EmailTracking.click_count > 0, 1))
            ).label("total_click_by_mail"),
            func.count(
                case((EmailTracking.download_count > 0, 1))
            ).label("total_download_by_mail"),
            func.count(
                case((EmailTracking.reply_count > 0, 1))
            ).label("total_reply_by_mail"),
            func.coalesce(
                func.sum(case((EmailTracking.is_bounce == 1, 1), else_=0)),
                0,
            ).label("total_bounce"),
            func.coalesce(
                func.sum(
                    case((EmailTracking.created_at >= weekly_threshold, 1), else_=0)
                ),
                0,
            ).label("weekly_sent"),
            func.coalesce(
                func.sum(
                    case((EmailTracking.created_at >= monthly_threshold, 1), else_=0)
                ),
                0,
            ).label("monthly_sent"),
        )

        try:
            with session_factory() as session:
                row = session.execute(statement).mappings().one()
                return {
                    "total_sent": int(row["total_sent"] or 0),
                    "total_open": int(row["total_open"] or 0),
                    "total_click": int(row["total_click"] or 0),
                    "total_download": int(row["total_download"] or 0),
                    "total_reply": int(row["total_reply"] or 0),
                    "total_open_by_mail": int(row["total_open_by_mail"] or 0),
                    "total_click_by_mail": int(row["total_click_by_mail"] or 0),
                    "total_download_by_mail": int(
                        row["total_download_by_mail"] or 0
                    ),
                    "total_reply_by_mail": int(row["total_reply_by_mail"] or 0),
                    "total_bounce": int(row["total_bounce"] or 0),
                    "weekly_sent": int(row["weekly_sent"] or 0),
                    "monthly_sent": int(row["monthly_sent"] or 0),
                }
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to fetch dashboard statistics totals: {exc}"
            ) from exc

    def count_report_records(self) -> int:
        """Return the total number of email tracking rows for reporting."""
        session_factory = self._require_session_factory()
        try:
            with session_factory() as session:
                return int(
                    session.scalar(select(func.count(EmailTracking.id)))
                    or 0
                )
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to count report records: {exc}"
            ) from exc

    def fetch_report_records(
        self,
        offset: int,
        limit: int,
    ) -> list[dict[str, object]]:
        """Return one report page ordered by newest sent records first."""
        session_factory = self._require_session_factory()
        statement = (
            select(
                EmailTracking.tracking_id,
                EmailTracking.sender_email,
                EmailTracking.recipient_email.label("receiver_email"),
                EmailTracking.project_name,
                EmailTracking.created_at.label("send_date"),
                func.coalesce(EmailTracking.open_count, 0).label("open_count"),
                func.coalesce(EmailTracking.click_count, 0).label("click_count"),
                func.coalesce(EmailTracking.download_count, 0).label("download_count"),
                func.coalesce(EmailTracking.reply_count, 0).label("reply_count"),
                EmailTracking.is_bounce,
            )
            .order_by(EmailTracking.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        try:
            with session_factory() as session:
                rows = session.execute(statement).mappings()
                return [
                    {
                        **dict(row),
                        "is_bounce": bool(row["is_bounce"]),
                    }
                    for row in rows
                ]
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to fetch report records: {exc}"
            ) from exc

    def mark_synchronized(
        self,
        tracking_id: str,
        last_synchronize_time: datetime,
    ) -> bool:
        """Update only last_synchronize_time for one tracking row."""
        session_factory = self._require_session_factory()
        timestamp = self._as_utc(last_synchronize_time)

        try:
            with session_factory() as session:
                result = session.execute(
                    text(
                        "UPDATE email_tracking "
                        "SET last_synchronize_time = :last_synchronize_time "
                        "WHERE tracking_id = :tracking_id"
                    ),
                    {
                        "last_synchronize_time": timestamp,
                        "tracking_id": tracking_id,
                    },
                )
                session.commit()
                return bool(result.rowcount)
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to mark tracking row synchronized: {exc}"
            ) from exc

    def dispose(self) -> None:
        """Release pooled database connections during application shutdown."""
        if self._engine is not None:
            self._engine.dispose()

    def _require_engine(self) -> Engine:
        if self._engine is None:
            raise DatabaseUnavailableError(
                self._configuration_error or "DATABASE_URL is not configured."
            )
        return self._engine

    def _require_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise DatabaseUnavailableError(
                self._configuration_error or "DATABASE_URL is not configured."
            )
        return self._session_factory

    @staticmethod
    def _normalize_database_url(database_url: str) -> str:
        """Select psycopg 3 for standard PostgreSQL URL schemes."""
        if database_url.startswith("postgres://"):
            return database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if database_url.startswith("postgresql://"):
            return database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        return database_url

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Return a timezone-aware timestamp for PostgreSQL."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @classmethod
    def _derive_excel_file_name(cls, excel_file_path: str | None) -> str | None:
        """Return the final filename from Windows or POSIX-style paths."""
        cleaned_path = cls._clean_optional(excel_file_path)
        if cleaned_path is None:
            return None
        if "\\" in cleaned_path or ":" in cleaned_path:
            return PureWindowsPath(cleaned_path).name or None
        return PurePath(cleaned_path).name or None
