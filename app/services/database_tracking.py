"""PostgreSQL persistence for email tracking events."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePath, PureWindowsPath

from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session, sessionmaker

from app.models.attachment import TrackingAttachment
from app.models.email_tracking import Base, EmailTracking


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
class SentEmailRegistration:
    """Metadata captured by Version 2 immediately after a successful send."""

    tracking_id: str
    sender_mail: str | None = None
    recipient_mail: str | None = None
    mail_subject: str | None = None
    project_name: str | None = None
    excel_file_path: str | None = None


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
        """Create all ORM tables and verify the database connection."""
        engine = self._require_engine()
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def record_open(
        self,
        tracking_id: str,
        open_count: int,
        client_ip: str,
        user_agent: str,
        occurred_at: datetime,
    ) -> None:
        """Insert or update one open event using an atomic PostgreSQL upsert."""
        session_factory = self._require_session_factory()
        timestamp = self._as_utc(occurred_at)

        statement = postgresql_insert(EmailTracking).values(
            tracking_id=tracking_id,
            open_count=open_count,
            click_count=0,
            first_open=timestamp,
            last_open=timestamp,
            last_ip=client_ip,
            user_agent=user_agent,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[EmailTracking.tracking_id],
            set_={
                "open_count": open_count,
                "last_open": timestamp,
                "updated_at": func.now(),
                "last_ip": client_ip,
                "user_agent": user_agent,
            },
        )

        try:
            with session_factory() as session:
                session.execute(statement)
                session.commit()
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
            EmailTracking.tracking_id,
            EmailTracking.open_count,
            EmailTracking.click_count,
            func.coalesce(
                func.sum(TrackingAttachment.download_count), 0
            ).label("download_count"),
            EmailTracking.first_open,
            EmailTracking.last_open,
            EmailTracking.first_click,
            EmailTracking.last_click,
            func.min(TrackingAttachment.first_download).label("first_download"),
            func.max(TrackingAttachment.last_download).label("last_download"),
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
            EmailTracking.tracking_id,
            EmailTracking.open_count,
            EmailTracking.click_count,
            EmailTracking.first_open,
            EmailTracking.last_open,
            EmailTracking.first_click,
            EmailTracking.last_click,
            EmailTracking.updated_at,
        ).order_by(EmailTracking.updated_at.asc())

        try:
            with session_factory() as session:
                rows = session.execute(
                    statement.execution_options(stream_results=True, yield_per=1000)
                ).mappings()
                return [dict(row) for row in rows]
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Unable to fetch PostgreSQL synchronization records: {exc}"
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
