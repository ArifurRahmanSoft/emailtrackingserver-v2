"""Models for server-side reply tracking registration."""

from datetime import datetime

from pydantic import BaseModel, Field


class ReplyTrackingRequest(BaseModel):
    """Payload sent when a recipient reply is detected."""

    message_id: str = Field(..., min_length=1, max_length=255)
    reply_time: datetime | None = None
    from_email: str | None = Field(default=None, max_length=320)


class ReplyTrackingResponse(BaseModel):
    """Reply tracking update result."""

    success: bool
    message_id: str
    tracking_id: str
    reply_count: int
    first_reply: datetime
    last_reply: datetime
