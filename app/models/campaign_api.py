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
    campaign_offer: str | None = None

    @field_validator("file_name", "client_name", "campaign_offer")
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
