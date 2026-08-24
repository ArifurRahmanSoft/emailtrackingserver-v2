"""Database operations for independent campaign management."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
from uuid import UUID

from sqlalchemy import Engine, case, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

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
        campaign_offer: str | None = None,
        created_at: datetime | None = None,
    ) -> Campaign:
        """Create one campaign, rejecting duplicate campaign codes."""
        clean_campaign_name = self._clean_required(campaign_name, "campaign_name")
        clean_campaign_code = self._clean_required(campaign_code, "campaign_code")
        self._validate_date_range(start_date, end_date)
        timestamp = self._as_utc(created_at or datetime.now(timezone.utc))
        session_factory = self._require_session_factory()

        try:
            with session_factory() as session:
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
        campaign_offer: str | None = None,
        updated_at: datetime | None = None,
    ) -> Campaign:
        """Update one campaign while preserving created_at."""
        clean_campaign_name = self._clean_required(campaign_name, "campaign_name")
        clean_campaign_code = self._clean_required(campaign_code, "campaign_code")
        self._validate_date_range(start_date, end_date)
        timestamp = self._as_utc(updated_at or datetime.now(timezone.utc))
        session_factory = self._require_session_factory()

        try:
            with session_factory() as session:
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
