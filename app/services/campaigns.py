"""Database operations for independent campaign management."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
from uuid import UUID

from sqlalchemy import Engine, case, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.auth import SystemUser
from app.models.campaign import Campaign, CampaignBase
from app.models.email_tracking import EmailTracking

logger = logging.getLogger(__name__)


class CampaignServiceError(RuntimeError):
    """Base error for campaign-management failures."""


class CampaignDatabaseUnavailableError(CampaignServiceError):
    """Raised when campaign storage is unavailable."""


class DuplicateCampaignCodeError(CampaignServiceError):
    """Raised when another campaign already uses the campaign_code."""


class CampaignNotFoundError(CampaignServiceError):
    """Raised when a requested campaign UUID does not exist."""


class CampaignValidationError(CampaignServiceError):
    """Raised when campaign data violates business validation rules."""


@dataclass(frozen=True, slots=True)
class CampaignDashboardResult:
    """Aggregated campaign-wise dashboard metrics."""

    campaign_code: str
    campaign_name: str
    client_name: str | None
    client_code: str | None
    start_date: date | None
    end_date: date | None
    total_mail_sent: int
    total_click: int
    total_reply: int
    total_bounce: int
    total_download: int
    success_rate: float
    failure_rate: float
    monthly_sent: int
    weekly_sent: int


@dataclass(frozen=True, slots=True)
class ClientCampaignDashboardResult:
    """Aggregated dashboard metrics for all campaigns owned by one client."""

    client_code: str
    campaign_count: int
    campaign_codes: list[str]
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
    total_unsubscribe: int
    last_unsubscribe_time: datetime | None
    last_updated: datetime


@dataclass(frozen=True, slots=True)
class CampaignClientInfoResult:
    """Unique client information resolved from campaign records."""

    client_code: str
    client_name: str | None


@dataclass(frozen=True, slots=True)
class CampaignProjectSenderResult:
    """Read-only sender/project options for selected campaigns."""

    campaign_codes: list[str]
    projects: list[dict[str, str | None]]


class CampaignService:
    """CRUD service for the independent campaigns table."""

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

    def initialize(self) -> None:
        """Create only the campaigns table when it is absent."""
        engine = self._require_engine()
        CampaignBase.metadata.create_all(engine)

    def dispose(self) -> None:
        """Dispose the campaign database connection pool."""
        if self._engine is not None:
            self._engine.dispose()

    def create_campaign(
        self,
        campaign_name: str,
        campaign_code: str,
        start_date: date | None = None,
        end_date: date | None = None,
        file_name: str | None = None,
        client_name: str | None = None,
        client_code: str | None = None,
        campaign_offer: str | None = None,
        created_at: datetime | None = None,
    ) -> Campaign:
        """Create one campaign, rejecting duplicate campaign codes."""
        clean_campaign_name = self._clean_required(campaign_name, "campaign_name")
        clean_campaign_code = self._clean_required(campaign_code, "campaign_code")
        clean_client_code = self._clean_optional(client_code)
        self._validate_date_range(start_date, end_date)
        timestamp = self._as_utc(created_at or datetime.now(timezone.utc))
        session_factory = self._require_session_factory()

        try:
            with session_factory() as session:
                self._validate_client_code(session, clean_client_code)
                existing = session.scalar(
                    select(Campaign.id).where(
                        Campaign.campaign_code == clean_campaign_code
                    )
                )
                if existing is not None:
                    raise DuplicateCampaignCodeError(
                        f"campaign_code '{clean_campaign_code}' already exists."
                    )

                campaign = Campaign(
                    campaign_name=clean_campaign_name,
                    campaign_code=clean_campaign_code,
                    start_date=start_date,
                    end_date=end_date,
                    file_name=file_name,
                    client_name=client_name,
                    client_code=clean_client_code,
                    campaign_offer=campaign_offer,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(campaign)
                session.commit()
                return campaign
        except DuplicateCampaignCodeError:
            raise
        except IntegrityError as exc:
            raise DuplicateCampaignCodeError(
                f"campaign_code '{clean_campaign_code}' already exists."
            ) from exc
        except CampaignValidationError:
            raise
        except Exception as exc:
            raise CampaignDatabaseUnavailableError(
                f"Unable to create campaign: {exc}"
            ) from exc

    def list_campaigns(self) -> list[Campaign]:
        """Return all campaigns newest-first."""
        session_factory = self._require_session_factory()
        try:
            with session_factory() as session:
                return list(
                    session.scalars(
                        select(Campaign).order_by(Campaign.created_at.desc())
                    )
                )
        except Exception as exc:
            raise CampaignDatabaseUnavailableError(
                f"Unable to list campaigns: {exc}"
            ) from exc

    def get_campaign_codes(self) -> list[str]:
        """Return non-empty campaign codes sorted ascending without modifying data."""
        session_factory = self._require_session_factory()
        try:
            with session_factory() as session:
                return list(
                    session.scalars(
                        select(Campaign.campaign_code)
                        .where(
                            Campaign.campaign_code.is_not(None),
                            func.trim(Campaign.campaign_code) != "",
                        )
                        .order_by(Campaign.campaign_code.asc())
                    )
                )
        except Exception as exc:
            raise CampaignDatabaseUnavailableError(
                f"Unable to list campaign codes: {exc}"
            ) from exc

    def get_client_info(self, client_code: str) -> CampaignClientInfoResult | None:
        """Return one client_code/client_name pair from existing campaign rows."""
        clean_client_code = self._clean_required(client_code, "client_code")
        session_factory = self._require_session_factory()
        trimmed_client_name = func.trim(Campaign.client_name)

        try:
            with session_factory() as session:
                campaign_exists = session.scalar(
                    select(Campaign.id)
                    .where(Campaign.client_code == clean_client_code)
                    .limit(1)
                )
                if campaign_exists is None:
                    return None

                client_name = session.scalar(
                    select(func.distinct(trimmed_client_name))
                    .where(
                        Campaign.client_code == clean_client_code,
                        Campaign.client_name.is_not(None),
                        trimmed_client_name != "",
                    )
                    .order_by(trimmed_client_name.asc())
                    .limit(1)
                )
                return CampaignClientInfoResult(
                    client_code=clean_client_code,
                    client_name=str(client_name) if client_name is not None else None,
                )
        except CampaignValidationError:
            raise
        except Exception as exc:
            raise CampaignDatabaseUnavailableError(
                f"Unable to get client info: {exc}"
            ) from exc

    def get_project_senders(
        self,
        campaign_codes: str,
    ) -> CampaignProjectSenderResult:
        """Return unique sender/project pairs for valid selected campaign codes."""
        requested_codes = self._parse_campaign_code_list(campaign_codes)
        session_factory = self._require_session_factory()
        trimmed_sender = func.trim(EmailTracking.sender_email)
        trimmed_project = func.trim(EmailTracking.project_name)

        try:
            with session_factory() as session:
                valid_codes = list(
                    session.scalars(
                        select(Campaign.campaign_code)
                        .where(Campaign.campaign_code.in_(requested_codes))
                        .order_by(Campaign.campaign_code.asc())
                    )
                )
                if not valid_codes:
                    return CampaignProjectSenderResult(
                        campaign_codes=[],
                        projects=[],
                    )

                rows = session.execute(
                    select(
                        trimmed_sender.label("sender_email"),
                        trimmed_project.label("project_name"),
                    )
                    .where(
                        EmailTracking.campaign_code.in_(valid_codes),
                        EmailTracking.sender_email.is_not(None),
                        EmailTracking.project_name.is_not(None),
                        trimmed_sender != "",
                        trimmed_project != "",
                    )
                    .distinct()
                    .order_by(trimmed_sender.asc(), trimmed_project.asc())
                ).mappings()
                return CampaignProjectSenderResult(
                    campaign_codes=valid_codes,
                    projects=[
                        {
                            "sender_email": row["sender_email"],
                            "project_name": row["project_name"],
                        }
                        for row in rows
                    ],
                )
        except CampaignValidationError:
            raise
        except Exception as exc:
            raise CampaignDatabaseUnavailableError(
                f"Unable to get campaign project senders: {exc}"
            ) from exc

    def get_campaign_dashboard(
        self,
        campaign_code: str | None = None,
        generated_at: datetime | None = None,
    ) -> list[CampaignDashboardResult]:
        """Return read-only campaign metadata with aggregated tracking metrics."""
        session_factory = self._require_session_factory()
        timestamp = self._as_utc(generated_at or datetime.now(timezone.utc))
        weekly_threshold = timestamp - timedelta(days=7)
        monthly_threshold = timestamp - timedelta(days=30)
        clean_campaign_code = (
            self._clean_optional(campaign_code) if campaign_code is not None else None
        )

        try:
            with session_factory() as session:
                if clean_campaign_code is not None:
                    exists = session.scalar(
                        select(Campaign.id).where(
                            Campaign.campaign_code == clean_campaign_code
                        )
                    )
                    if exists is None:
                        raise CampaignNotFoundError("Campaign not found.")

                statement = (
                    select(
                        Campaign.campaign_code.label("campaign_code"),
                        Campaign.campaign_name.label("campaign_name"),
                        Campaign.client_name.label("client_name"),
                        Campaign.client_code.label("client_code"),
                        Campaign.start_date.label("start_date"),
                        Campaign.end_date.label("end_date"),
                        func.count(EmailTracking.id).label("total_mail_sent"),
                        func.coalesce(func.sum(EmailTracking.click_count), 0).label(
                            "total_click"
                        ),
                        func.coalesce(func.sum(EmailTracking.reply_count), 0).label(
                            "total_reply"
                        ),
                        func.coalesce(
                            func.sum(
                                case((EmailTracking.is_bounce == 1, 1), else_=0)
                            ),
                            0,
                        ).label("total_bounce"),
                        func.coalesce(func.sum(EmailTracking.download_count), 0).label(
                            "total_download"
                        ),
                        func.coalesce(
                            func.sum(
                                case(
                                    (EmailTracking.created_at >= monthly_threshold, 1),
                                    else_=0,
                                )
                            ),
                            0,
                        ).label("monthly_sent"),
                        func.coalesce(
                            func.sum(
                                case(
                                    (EmailTracking.created_at >= weekly_threshold, 1),
                                    else_=0,
                                )
                            ),
                            0,
                        ).label("weekly_sent"),
                    )
                    .select_from(Campaign)
                    .outerjoin(
                        EmailTracking,
                        EmailTracking.campaign_code == Campaign.campaign_code,
                    )
                    .group_by(
                        Campaign.campaign_code,
                        Campaign.campaign_name,
                        Campaign.client_name,
                        Campaign.client_code,
                        Campaign.start_date,
                        Campaign.end_date,
                    )
                    .order_by(Campaign.campaign_code.asc())
                )
                if clean_campaign_code is not None:
                    statement = statement.where(
                        Campaign.campaign_code == clean_campaign_code
                    )

                rows = session.execute(statement).mappings().all()
                return [self._dashboard_result_from_row(row) for row in rows]
        except CampaignNotFoundError:
            raise
        except Exception as exc:
            raise CampaignDatabaseUnavailableError(
                f"Unable to build campaign dashboard: {exc}"
            ) from exc

    def get_client_dashboard(
        self,
        client_code: str,
        generated_at: datetime | None = None,
    ) -> ClientCampaignDashboardResult:
        """Return read-only dashboard totals for campaigns under one client code."""
        clean_client_code = self._clean_required(client_code, "client_code")
        session_factory = self._require_session_factory()
        timestamp = self._as_utc(generated_at or datetime.now(timezone.utc))
        weekly_threshold = timestamp - timedelta(days=7)
        monthly_threshold = timestamp - timedelta(days=30)

        try:
            with session_factory() as session:
                campaign_codes = list(
                    session.scalars(
                        select(Campaign.campaign_code)
                        .where(
                            Campaign.client_code == clean_client_code,
                            Campaign.campaign_code.is_not(None),
                            func.trim(Campaign.campaign_code) != "",
                        )
                        .order_by(Campaign.campaign_code.asc())
                    )
                )

                if not campaign_codes:
                    return self._empty_client_dashboard_result(
                        clean_client_code,
                        timestamp,
                    )

                statement = select(
                    func.count(EmailTracking.id).label("total_sent"),
                    func.coalesce(func.sum(EmailTracking.open_count), 0).label(
                        "total_open"
                    ),
                    func.coalesce(func.sum(EmailTracking.click_count), 0).label(
                        "total_click"
                    ),
                    func.coalesce(func.sum(EmailTracking.download_count), 0).label(
                        "total_download"
                    ),
                    func.coalesce(func.sum(EmailTracking.reply_count), 0).label(
                        "total_reply"
                    ),
                    func.coalesce(
                        func.sum(case((EmailTracking.is_bounce == 1, 1), else_=0)),
                        0,
                    ).label("total_bounce"),
                    func.coalesce(
                        func.sum(
                            case(
                                (func.coalesce(EmailTracking.open_count, 0) > 0, 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("total_open_by_mail"),
                    func.coalesce(
                        func.sum(
                            case(
                                (func.coalesce(EmailTracking.click_count, 0) > 0, 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("total_click_by_mail"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    func.coalesce(EmailTracking.download_count, 0) > 0,
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("total_download_by_mail"),
                    func.coalesce(
                        func.sum(
                            case(
                                (func.coalesce(EmailTracking.reply_count, 0) > 0, 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("total_reply_by_mail"),
                    func.coalesce(
                        func.sum(
                            case(
                                (EmailTracking.created_at >= weekly_threshold, 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("weekly_sent"),
                    func.coalesce(
                        func.sum(
                            case(
                                (EmailTracking.created_at >= monthly_threshold, 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("monthly_sent"),
                    func.coalesce(
                        func.sum(case((EmailTracking.unsubscribe == 1, 1), else_=0)),
                        0,
                    ).label("total_unsubscribe"),
                    func.max(
                        case(
                            (EmailTracking.unsubscribe == 1, EmailTracking.unsubscribe_time),
                            else_=None,
                        )
                    ).label("last_unsubscribe_time"),
                ).where(EmailTracking.campaign_code.in_(campaign_codes))

                row = session.execute(statement).mappings().one()
                return self._client_dashboard_result_from_row(
                    clean_client_code,
                    campaign_codes,
                    row,
                    timestamp,
                )
        except CampaignValidationError:
            raise
        except Exception as exc:
            raise CampaignDatabaseUnavailableError(
                f"Unable to build client campaign dashboard: {exc}"
            ) from exc

    def get_campaign(self, campaign_id: UUID) -> Campaign:
        """Return one campaign by UUID."""
        session_factory = self._require_session_factory()
        try:
            with session_factory() as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    raise CampaignNotFoundError("Campaign not found.")
                return campaign
        except CampaignNotFoundError:
            raise
        except Exception as exc:
            raise CampaignDatabaseUnavailableError(
                f"Unable to get campaign: {exc}"
            ) from exc

    def update_campaign(
        self,
        campaign_id: UUID,
        campaign_name: str,
        campaign_code: str,
        start_date: date | None = None,
        end_date: date | None = None,
        file_name: str | None = None,
        client_name: str | None = None,
        client_code: str | None = None,
        campaign_offer: str | None = None,
        updated_at: datetime | None = None,
    ) -> Campaign:
        """Update one campaign while preserving created_at."""
        clean_campaign_name = self._clean_required(campaign_name, "campaign_name")
        clean_campaign_code = self._clean_required(campaign_code, "campaign_code")
        clean_client_code = self._clean_optional(client_code)
        self._validate_date_range(start_date, end_date)
        timestamp = self._as_utc(updated_at or datetime.now(timezone.utc))
        session_factory = self._require_session_factory()

        try:
            with session_factory() as session:
                self._validate_client_code(session, clean_client_code)
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    raise CampaignNotFoundError("Campaign not found.")

                duplicate = session.scalar(
                    select(Campaign.id).where(
                        Campaign.campaign_code == clean_campaign_code,
                        Campaign.id != campaign_id,
                    )
                )
                if duplicate is not None:
                    raise DuplicateCampaignCodeError(
                        f"campaign_code '{clean_campaign_code}' already exists."
                    )

                campaign.campaign_name = clean_campaign_name
                campaign.campaign_code = clean_campaign_code
                campaign.start_date = start_date
                campaign.end_date = end_date
                campaign.file_name = file_name
                campaign.client_name = client_name
                campaign.client_code = clean_client_code
                campaign.campaign_offer = campaign_offer
                campaign.updated_at = timestamp
                session.commit()
                return campaign
        except (CampaignNotFoundError, DuplicateCampaignCodeError):
            raise
        except IntegrityError as exc:
            raise DuplicateCampaignCodeError(
                f"campaign_code '{clean_campaign_code}' already exists."
            ) from exc
        except CampaignValidationError:
            raise
        except Exception as exc:
            raise CampaignDatabaseUnavailableError(
                f"Unable to update campaign: {exc}"
            ) from exc

    def delete_campaign(self, campaign_id: UUID) -> None:
        """Delete only the campaign record."""
        session_factory = self._require_session_factory()
        try:
            with session_factory() as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign is None:
                    raise CampaignNotFoundError("Campaign not found.")
                session.delete(campaign)
                session.commit()
        except CampaignNotFoundError:
            raise
        except Exception as exc:
            raise CampaignDatabaseUnavailableError(
                f"Unable to delete campaign: {exc}"
            ) from exc

    @staticmethod
    def _validate_date_range(
        start_date: date | None,
        end_date: date | None,
    ) -> None:
        """Ensure end_date is not earlier than start_date."""
        if start_date is not None and end_date is not None and end_date < start_date:
            raise CampaignValidationError("end_date must not be before start_date.")

    @staticmethod
    def _clean_required(value: str, field_name: str) -> str:
        """Trim and validate one required text value."""
        cleaned = value.strip()
        if not cleaned:
            raise CampaignValidationError(f"{field_name} is required.")
        return cleaned

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        """Trim optional text values and normalize blanks to None."""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _validate_client_code(session: Session, client_code: str | None) -> None:
        """Ensure a supplied client_code exists in system_users.user_id."""
        if client_code is None:
            return
        exists = session.scalar(
            select(SystemUser.id).where(SystemUser.user_id == client_code)
        )
        if exists is None:
            raise CampaignValidationError("client_code must exist in system_users.")

    @staticmethod
    def _parse_campaign_code_list(campaign_codes: str) -> list[str]:
        """Parse comma-separated campaign codes, rejecting empty requests."""
        parsed_codes = [
            code.strip()
            for code in campaign_codes.split(",")
            if code.strip()
        ]
        if not parsed_codes:
            raise CampaignValidationError("campaign_codes is required.")
        return list(dict.fromkeys(parsed_codes))

    @staticmethod
    def _dashboard_result_from_row(row) -> CampaignDashboardResult:
        """Convert one aggregate row to a dashboard result."""
        total_mail_sent = int(row["total_mail_sent"] or 0)
        total_bounce = int(row["total_bounce"] or 0)
        if total_mail_sent == 0:
            success_rate = 0.0
            failure_rate = 0.0
        else:
            success_rate = round(
                ((total_mail_sent - total_bounce) / total_mail_sent) * 100,
                2,
            )
            failure_rate = round((total_bounce / total_mail_sent) * 100, 2)

        return CampaignDashboardResult(
            campaign_code=row["campaign_code"],
            campaign_name=row["campaign_name"],
            client_name=row["client_name"],
            client_code=row["client_code"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            total_mail_sent=total_mail_sent,
            total_click=int(row["total_click"] or 0),
            total_reply=int(row["total_reply"] or 0),
            total_bounce=total_bounce,
            total_download=int(row["total_download"] or 0),
            success_rate=success_rate,
            failure_rate=failure_rate,
            monthly_sent=int(row["monthly_sent"] or 0),
            weekly_sent=int(row["weekly_sent"] or 0),
        )

    @classmethod
    def _empty_client_dashboard_result(
        cls,
        client_code: str,
        timestamp: datetime,
    ) -> ClientCampaignDashboardResult:
        """Return the successful zero-metric shape for clients without campaigns."""
        return ClientCampaignDashboardResult(
            client_code=client_code,
            campaign_count=0,
            campaign_codes=[],
            total_sent=0,
            total_open=0,
            total_click=0,
            total_download=0,
            total_reply=0,
            total_bounce=0,
            total_open_by_mail=0,
            total_click_by_mail=0,
            total_download_by_mail=0,
            total_reply_by_mail=0,
            weekly_sent=0,
            monthly_sent=0,
            success_rate=0.0,
            failure_rate=0.0,
            total_unsubscribe=0,
            last_unsubscribe_time=None,
            last_updated=timestamp,
        )

    @classmethod
    def _client_dashboard_result_from_row(
        cls,
        client_code: str,
        campaign_codes: list[str],
        row,
        timestamp: datetime,
    ) -> ClientCampaignDashboardResult:
        """Convert one aggregate row to the client dashboard response result."""
        total_sent = int(row["total_sent"] or 0)
        total_bounce = int(row["total_bounce"] or 0)
        if total_sent == 0:
            success_rate = 0.0
            failure_rate = 0.0
        else:
            success_rate = round(((total_sent - total_bounce) / total_sent) * 100, 2)
            failure_rate = round((total_bounce / total_sent) * 100, 2)

        return ClientCampaignDashboardResult(
            client_code=client_code,
            campaign_count=len(campaign_codes),
            campaign_codes=campaign_codes,
            total_sent=total_sent,
            total_open=int(row["total_open"] or 0),
            total_click=int(row["total_click"] or 0),
            total_download=int(row["total_download"] or 0),
            total_reply=int(row["total_reply"] or 0),
            total_bounce=total_bounce,
            total_open_by_mail=int(row["total_open_by_mail"] or 0),
            total_click_by_mail=int(row["total_click_by_mail"] or 0),
            total_download_by_mail=int(row["total_download_by_mail"] or 0),
            total_reply_by_mail=int(row["total_reply_by_mail"] or 0),
            weekly_sent=int(row["weekly_sent"] or 0),
            monthly_sent=int(row["monthly_sent"] or 0),
            success_rate=success_rate,
            failure_rate=failure_rate,
            total_unsubscribe=int(row["total_unsubscribe"] or 0),
            last_unsubscribe_time=row["last_unsubscribe_time"],
            last_updated=timestamp,
        )

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

    def _require_engine(self) -> Engine:
        if self._engine is None:
            raise CampaignDatabaseUnavailableError(
                self._configuration_error or "DATABASE_URL is not configured."
            )
        return self._engine

    def _require_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise CampaignDatabaseUnavailableError(
                self._configuration_error or "DATABASE_URL is not configured."
            )
        return self._session_factory
