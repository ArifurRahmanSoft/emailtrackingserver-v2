"""HTTP routes for client-based reports."""

import logging
import time
from io import BytesIO

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.models.client_report import ClientReportResponse
from app.services.client_report import (
    ClientReportDatabaseUnavailableError,
    ClientReportService,
    ClientReportValidationError,
)
from config.settings import load_settings


logger = logging.getLogger(__name__)
router = APIRouter(tags=["Client Reports"])
settings = load_settings()
client_report_service = ClientReportService(settings.database_url)


@router.get(
    "/api/client-report",
    response_model=ClientReportResponse,
    summary="Return paginated client-based tracking report rows",
)
async def get_client_report(
    client_code: str | None = Query(default=None, description="Required client code."),
    page: int = Query(default=1, description="Page number."),
    per_page: int = Query(default=20, description="Rows per page."),
    sender_mail: str | None = Query(default=None, description="Exact sender_mail."),
    campaign_code: str | None = Query(default=None, description="Exact campaign code."),
    project: str | None = Query(default=None, description="Exact project name."),
    is_bounce: str | None = Query(default=None, description="Bounce filter 1/0."),
    is_reply: str | None = Query(default=None, description="Reply filter true/false."),
    is_open: str | None = Query(default=None, description="Open filter true/false."),
    is_click: str | None = Query(default=None, description="Click filter true/false."),
    is_download: str | None = Query(
        default=None,
        description="Download filter true/false.",
    ),
    from_date: str | None = Query(default=None, description="Start date YYYY-MM-DD."),
    to_date: str | None = Query(default=None, description="End date YYYY-MM-DD."),
) -> ClientReportResponse:
    """Return email_tracking rows for only the requested client's campaigns."""
    started_at = time.perf_counter()
    try:
        result = await run_in_threadpool(
            client_report_service.get_report,
            client_code,
            page,
            per_page,
            sender_mail,
            campaign_code,
            project,
            is_bounce,
            is_reply,
            is_open,
            is_click,
            is_download,
            from_date,
            to_date,
        )
    except ClientReportValidationError as exc:
        logger.warning(
            "Client report rejected: client_code=%s page=%s per_page=%s "
            "campaign_code=%s error=%s",
            client_code,
            page,
            per_page,
            campaign_code,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ClientReportDatabaseUnavailableError as exc:
        logger.error(
            "Client report failed: client_code=%s page=%s per_page=%s "
            "campaign_code=%s error=%s",
            client_code,
            page,
            per_page,
            campaign_code,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Client report is temporarily unavailable.",
        ) from exc

    execution_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "Client report requested: client_code=%s page=%d per_page=%d "
        "total_records=%d returned_rows=%d execution_time=%.2fms",
        result.client_code,
        result.pagination.page,
        result.pagination.per_page,
        result.pagination.total_records,
        len(result.data),
        execution_ms,
    )
    return result


@router.get(
    "/api/client-report/export",
    summary="Export filtered client-based report rows to Excel",
    response_class=StreamingResponse,
)
async def export_client_report(
    client_code: str | None = Query(default=None, description="Required client code."),
    sender_mail: str | None = Query(default=None, description="Exact sender_mail."),
    campaign_code: str | None = Query(default=None, description="Exact campaign code."),
    project: str | None = Query(default=None, description="Exact project name."),
    is_bounce: str | None = Query(default=None, description="Bounce filter 1/0."),
    is_reply: str | None = Query(default=None, description="Reply filter true/false."),
    is_open: str | None = Query(default=None, description="Open filter true/false."),
    is_click: str | None = Query(default=None, description="Click filter true/false."),
    is_download: str | None = Query(
        default=None,
        description="Download filter true/false.",
    ),
    from_date: str | None = Query(default=None, description="Start date YYYY-MM-DD."),
    to_date: str | None = Query(default=None, description="End date YYYY-MM-DD."),
) -> StreamingResponse:
    """Export every matching client report row without pagination."""
    started_at = time.perf_counter()
    try:
        result = await run_in_threadpool(
            client_report_service.export_report,
            client_code,
            sender_mail,
            campaign_code,
            project,
            is_bounce,
            is_reply,
            is_open,
            is_click,
            is_download,
            from_date,
            to_date,
        )
    except ClientReportValidationError as exc:
        logger.warning(
            "Client report export rejected: client_code=%s campaign_code=%s error=%s",
            client_code,
            campaign_code,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ClientReportDatabaseUnavailableError as exc:
        logger.error(
            "Client report export failed: client_code=%s campaign_code=%s error=%s",
            client_code,
            campaign_code,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Client report export is temporarily unavailable.",
        ) from exc

    execution_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "Client report export completed: client_code=%s total_exported_rows=%d "
        "filename=%s execution_time=%.2fms",
        client_code,
        result.row_count,
        result.filename,
        execution_ms,
    )
    return StreamingResponse(
        BytesIO(result.content),
        media_type=result.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
        },
    )
