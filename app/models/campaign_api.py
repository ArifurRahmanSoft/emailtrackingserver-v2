"""Request and response models for campaign management APIs."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CampaignPayload(BaseModel):
    """Editable fields for one campaign."""

    campaign_name: str = Field(..., max_length=255)
    campaign_code: str = Field(..., max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    file_name: str | None = Field(default=None, max_length=500)
    client_name: str | None = Field(default=None, max_length=255)
    client_code: str | None = Field(default=None, max_length=100)
    campaign_offer: str | None = None

    @field_validator("file_name", "client_name", "client_code", "campaign_offer")
    @classmethod
    def optional_text_to_clean_or_none(cls, value: str | None) -> str | None:
        """Store optional whitespace-only strings as NULL-equivalent values."""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class CampaignResponse(BaseModel):
    """One campaign returned by the API."""

    id: UUID
    campaign_name: str
    campaign_code: str
    start_date: date | None
    end_date: date | None
    file_name: str | None
    client_name: str | None
    client_code: str | None
    campaign_offer: str | None
    created_at: datetime
    updated_at: datetime


class CampaignMutationResponse(BaseModel):
    """Create/update response wrapper."""

    success: bool
    campaign: CampaignResponse


class CampaignDeleteResponse(BaseModel):
    """Successful campaign deletion response."""

    success: bool
    id: UUID
    message: str


class CampaignCodeListResponse(BaseModel):
    """Read-only list of available campaign codes."""

    success: bool
    campaign_codes: list[str]


class CampaignClientInfoResponse(BaseModel):
    """Read-only client information resolved from campaigns."""

    success: bool
    client_code: str
    client_name: str | None


class CampaignProjectSenderItem(BaseModel):
    """One unique sender/project combination for campaign reports."""

    sender_email: str | None
    project_name: str | None


class CampaignProjectSendersResponse(BaseModel):
    """Read-only sender/project options resolved from campaign tracking rows."""

    success: bool
    campaign_codes: list[str]
    projects: list[CampaignProjectSenderItem]


class CampaignDashboardFilter(BaseModel):
    """Applied campaign dashboard filter values."""

    campaign_code: str | None


class CampaignDashboardItem(BaseModel):
    """One campaign-wise dashboard row."""

    campaign_code: str
    campaign_name: str
    clint_name: str | None
    start_date: date | str | None
    end_date: date | str | None
    total_mail_sent: int
    total_click: int
    total_reply: int
    total_bounce: int
    total_download: int
    success_rate: float
    failure_rate: float
    monthly_sent: int
    weekly_sent: int
    is_total: bool = False


class CampaignDashboardResponse(BaseModel):
    """Campaign-wise dashboard response."""

    success: bool
    filter: CampaignDashboardFilter
    campaigns: list[CampaignDashboardItem]


class ClientCampaignDashboardResponse(BaseModel):
    """Aggregated campaign dashboard metrics for one client code."""

    success: bool
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
