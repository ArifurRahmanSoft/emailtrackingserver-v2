"""API response models for the attachment library."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AttachmentUploadResponse(BaseModel):
    """Metadata returned after a successful upload."""

    model_config = ConfigDict(from_attributes=True)

    attachment_id: UUID
    original_file_name: str
    file_size: int
    uploaded_at: datetime


class AttachmentListItem(AttachmentUploadResponse):
    """Active attachment metadata returned by the list endpoint."""

    content_type: str


class AttachmentDeleteResponse(BaseModel):
    """Confirmation of a successful soft delete."""

    attachment_id: UUID
    message: str
