"""Dashboard statistics aggregation service."""

from datetime import datetime, timezone

from app.models.statistics import DashboardStatisticsResponse
from app.services.database_tracking import DatabaseTrackingService


class DashboardStatisticsService:
    """Build dashboard summaries from PostgreSQL aggregate totals."""

    def __init__(self, database_service: DatabaseTrackingService) -> None:
        self._database_service = database_service

    def get_statistics(
        self,
        generated_at: datetime | None = None,
    ) -> DashboardStatisticsResponse:
        """Return dashboard totals, rates, and generation timestamp."""
        last_updated = generated_at or datetime.now(timezone.utc)
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        else:
            last_updated = last_updated.astimezone(timezone.utc)

        totals = self._database_service.fetch_dashboard_statistics_totals(
            last_updated
        )
        total_sent = totals["total_sent"]
        total_bounce = totals["total_bounce"]

        if total_sent == 0:
            success_rate = 0.0
            failure_rate = 0.0
        else:
            success_rate = round(((total_sent - total_bounce) / total_sent) * 100, 2)
            failure_rate = round((total_bounce / total_sent) * 100, 2)

        return DashboardStatisticsResponse(
            total_sent=total_sent,
            total_open=totals["total_open"],
            total_click=totals["total_click"],
            total_download=totals["total_download"],
            total_reply=totals["total_reply"],
            total_bounce=total_bounce,
            total_open_by_mail=totals["total_open_by_mail"],
            total_click_by_mail=totals["total_click_by_mail"],
            total_download_by_mail=totals["total_download_by_mail"],
            total_reply_by_mail=totals["total_reply_by_mail"],
            weekly_sent=totals["weekly_sent"],
            monthly_sent=totals["monthly_sent"],
            success_rate=success_rate,
            failure_rate=failure_rate,
            last_updated=last_updated,
        )
