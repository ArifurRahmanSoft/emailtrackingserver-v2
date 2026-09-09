"""Pydantic schemas for email-template APIs."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class EmailTemplatePayload(BaseModel):
    """Required and editable template fields."""

    template_name: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=500)
    body_html: str | None = None
    signature_html: str | None = None
    status: str = Field(default="active", max_length=50)
    created_by: str | None = Field(default=None, max_length=255)

    @field_validator("template_name", "subject", "body_html")
    @classmethod
    def required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field must not be empty.")
        return cleaned

    @field_validator("signature_html", "status", "created_by")
    @classmethod
    def optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class EmailTemplateListItem(BaseModel):
    id: UUID
    template_name: str
    subject: str
    status: str
    created_at: datetime


class EmailTemplateResponse(BaseModel):
    id: UUID
    template_name: str
    subject: str
    body_html: str
    signature_html: str | None
    status: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class EmailTemplateImageUploadResponse(BaseModel):
    success: bool
    file_name: str
    url: str
