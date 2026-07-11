"""Request and response models for Version 2 sent-email registration."""

from pydantic import BaseModel, Field


class SentEmailRegistrationRequest(BaseModel):
    """Metadata sent by EmailAutomation V2 after one email is sent."""

    tracking_id: str = Field(..., min_length=1, max_length=128)
    sender_mail: str | None = Field(default=None, max_length=320)
    recipient_mail: str | None = Field(default=None, max_length=320)
    mail_subject: str | None = Field(default=None, max_length=998)
    project_name: str | None = Field(default=None, max_length=255)
    excel_file_path: str | None = None
    message_id: str | None = Field(default=None, max_length=255)


class SentEmailRegistrationResponse(BaseModel):
    """Confirmation returned after the tracking row is registered."""

    success: bool
    tracking_id: str
    excel_file_name: str | None
