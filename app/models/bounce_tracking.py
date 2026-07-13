"""Models for server-side bounce tracking registration."""

from datetime import datetime

from pydantic import BaseModel, Field


class BounceTrackingRequest(BaseModel):
    """Payload sent when EmailAutomation V2 detects a bounce."""

    message_id: str = Field(..., min_length=1, max_length=255)
    bounce_reason: str | None = None


class BounceTrackingResponse(BaseModel):
    """Bounce tracking update result."""

    success: bool
    message_id: str
    tracking_id: str
    is_bounce: int
    bounce_time: datetime
    bounce_reason: str | None
