"""Response models for statistics endpoints."""

from pydantic import BaseModel


class SampleStatistics(BaseModel):
    """Placeholder statistics returned during Phase 1."""

    status: str
    total_opens: int
    total_clicks: int
    message: str
