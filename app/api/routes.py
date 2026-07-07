"""Public HTTP endpoints for the tracking server."""

import logging
import os
import platform
import re
import time
from datetime import datetime, timezone

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from app.models.statistics import SampleStatistics
from app.models.tracking_sync import TrackingSyncRecord
from app.services.database_tracking import DatabaseTrackingService
from app.services.excel_tracking import ExcelTrackingService
from app.services.tracking_debug import TrackingDebugService
from app.services.tracking_pixel import get_transparent_pixel
from app.utils.url_validation import is_valid_http_url
from app.utils.datetime_parsing import parse_iso8601_utc
from config.settings import PROJECT_ROOT, load_settings

router = APIRouter()
logger = logging.getLogger(__name__)
settings = load_settings()
tracking_service = ExcelTrackingService(settings.tracking_file)
database_service = DatabaseTrackingService(settings.database_url)
debug_service = TrackingDebugService(tracking_service.workbook_path)
DEBUG_TAG = "Development / Debug Only"
CLICK_TRACKING_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Tracking IDs remain URL-safe and must contain at least one character.
TrackingId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="A URL-safe tracking identifier.",
    ),
]


@router.get("/health", tags=["System"], summary="Check service health")
async def health_check() -> dict[str, str]:
    """Return a lightweight liveness response."""
    return {"status": "ok"}


@router.get(
    "/email/open/{tracking_id}",
    tags=["Tracking"],
    summary="Return the email open tracking pixel",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def track_email_open(tracking_id: TrackingId, request: Request) -> Response:
    """Validate the ID and return a transparent 1×1 PNG.

    The PNG is returned even when the tracking workbook cannot be updated.
    """
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    occurred_at = datetime.now()

    try:
        result = await run_in_threadpool(
            tracking_service.record_open,
            tracking_id,
            client_ip,
            user_agent,
            occurred_at,
        )
        logger.info(
            "DateTime=%s TrackingId=%s ClientIP=%s OpenCount=%d Status=%s Error=None",
            occurred_at.isoformat(),
            tracking_id,
            client_ip,
            result.open_count,
            result.status,
        )
        try:
            await run_in_threadpool(
                database_service.record_open,
                tracking_id,
                result.open_count,
                client_ip,
                user_agent,
                occurred_at,
            )
            logger.info(
                "PostgreSQL tracking update completed: TrackingId=%s OpenCount=%d",
                tracking_id,
                result.open_count,
            )
        except Exception as database_exc:
            # Excel remains authoritative when PostgreSQL is unavailable.
            logger.error(
                "PostgreSQL tracking update failed: TrackingId=%s Error=%s",
                tracking_id,
                database_exc,
                exc_info=True,
            )
    except Exception as exc:
        # Storage failures must never prevent an email client loading the pixel.
        logger.error(
            "DateTime=%s TrackingId=%s ClientIP=%s OpenCount=unknown "
            "Status=error Error=%s",
            occurred_at.isoformat(),
            tracking_id,
            client_ip,
            exc,
            exc_info=True,
        )

    return Response(
        content=get_transparent_pixel(),
        media_type="image/png",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get(
    "/email/click/{tracking_id}",
    tags=["Tracking"],
    summary="Track a recipient click and redirect",
    status_code=status.HTTP_302_FOUND,
    responses={
        302: {"description": "Click recorded; redirect to the original URL"},
        400: {"description": "Invalid tracking ID or destination URL"},
        404: {"description": "Tracking ID not found"},
    },
)
async def track_email_click(
    tracking_id: str,
    request: Request,
    url: str | None = Query(
        default=None,
        description="URL-encoded original HTTP or HTTPS destination.",
    ),
) -> RedirectResponse:
    """Record a database click and redirect to the validated original URL."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    click_time = datetime.now(timezone.utc)

    if not CLICK_TRACKING_ID_PATTERN.fullmatch(tracking_id):
        logger.warning(
            "Click rejected: TrackingId=%s OriginalURL=%s ClickTime=%s "
            "ClientIP=%s UserAgent=%s DatabaseUpdateStatus=not_attempted "
            "RedirectStatus=not_redirected Reason=invalid_tracking_id",
            tracking_id,
            url,
            click_time.isoformat(),
            client_ip,
            user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid tracking_id.",
        )

    if url is None or not is_valid_http_url(url):
        logger.warning(
            "Click rejected: TrackingId=%s OriginalURL=%s ClickTime=%s "
            "ClientIP=%s UserAgent=%s DatabaseUpdateStatus=not_attempted "
            "RedirectStatus=not_redirected Reason=invalid_url",
            tracking_id,
            url,
            click_time.isoformat(),
            client_ip,
            user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid HTTP or HTTPS url query parameter is required.",
        )

    try:
        result = await run_in_threadpool(
            database_service.record_click,
            tracking_id,
            client_ip,
            user_agent,
            click_time,
        )
    except Exception as exc:
        logger.error(
            "Click failed: TrackingId=%s OriginalURL=%s ClickTime=%s "
            "ClientIP=%s UserAgent=%s DatabaseUpdateStatus=failed "
            "RedirectStatus=not_redirected Error=%s",
            tracking_id,
            url,
            click_time.isoformat(),
            client_ip,
            user_agent,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Click tracking is temporarily unavailable.",
        ) from exc

    if result is None:
        logger.warning(
            "Click rejected: TrackingId=%s OriginalURL=%s ClickTime=%s "
            "ClientIP=%s UserAgent=%s DatabaseUpdateStatus=not_found "
            "RedirectStatus=not_redirected",
            tracking_id,
            url,
            click_time.isoformat(),
            client_ip,
            user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tracking ID not found.",
        )

    logger.info(
        "Click tracked: TrackingId=%s OriginalURL=%s ClickTime=%s "
        "ClientIP=%s UserAgent=%s ClickCount=%d DatabaseUpdateStatus=updated "
        "RedirectStatus=302",
        tracking_id,
        url,
        click_time.isoformat(),
        client_ip,
        user_agent,
        result.click_count,
    )
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get(
    "/api/statistics",
    tags=["Statistics"],
    summary="Return placeholder tracking statistics",
    response_model=SampleStatistics,
)
async def get_statistics() -> SampleStatistics:
    """Return sample data; no statistics are calculated in Phase 1."""
    return SampleStatistics(
        status="sample",
        total_opens=0,
        total_clicks=0,
        message="Statistics tracking is not implemented yet.",
    )


@router.get(
    "/api/tracking",
    tags=[DEBUG_TAG],
    summary="Development only: list tracking records",
    description="Development / Debug Only. Reads all records without modifying Excel.",
    response_model=None,
)
async def get_tracking_records() -> list[dict[str, object]] | JSONResponse:
    """Return all workbook records or a message when no workbook exists."""
    logger.info("Debug endpoint requested: GET /api/tracking")
    if not debug_service.workbook_path.is_file():
        logger.info("Debug tracking list completed: workbook not found")
        return JSONResponse(content={"message": "No tracking records found."})

    records = await run_in_threadpool(debug_service.read_records)
    logger.info("Debug tracking list completed: total_records=%d", len(records))
    return records


@router.get(
    "/api/download-excel",
    tags=[DEBUG_TAG],
    summary="Development only: download the tracking workbook",
    description="Development / Debug Only. Downloads Excel without modifying it.",
    response_model=None,
    responses={404: {"description": "Tracking workbook not found"}},
)
async def download_tracking_excel() -> FileResponse | JSONResponse:
    """Download the current workbook or return the required 404 response."""
    logger.info("Debug endpoint requested: GET /api/download-excel")
    if not debug_service.workbook_path.is_file():
        logger.info("Debug Excel download failed: workbook not found")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"message": "EmailTracking.xlsx not found."},
        )

    logger.info("Debug Excel download started: path=%s", debug_service.workbook_path)
    return FileResponse(
        path=debug_service.workbook_path,
        filename="EmailTracking.xlsx",
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )


@router.get(
    "/api/debug",
    tags=[DEBUG_TAG],
    summary="Development only: inspect server diagnostics",
    description="Development / Debug Only. Returns runtime and workbook diagnostics.",
)
async def get_debug_information() -> dict[str, object]:
    """Return application, runtime, and read-only workbook diagnostics."""
    logger.info("Debug endpoint requested: GET /api/debug")
    excel_exists = debug_service.workbook_path.is_file()
    total_records = (
        await run_in_threadpool(debug_service.count_records) if excel_exists else 0
    )
    response: dict[str, object] = {
        "application": "EmailTrackingServer",
        "working_directory": os.getcwd(),
        "base_directory": str(PROJECT_ROOT),
        "excel_path": str(debug_service.workbook_path),
        "excel_exists": excel_exists,
        "total_records": total_records,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "python_version": platform.python_version(),
        "operating_system": f"{platform.system()} {platform.release()}",
    }
    logger.info(
        "Debug diagnostics completed: excel_exists=%s total_records=%d",
        excel_exists,
        total_records,
    )
    return response


@router.get(
    "/api/database/status",
    tags=[DEBUG_TAG],
    summary="Development only: inspect PostgreSQL status",
    description=(
        "Development / Debug Only. Reports PostgreSQL connectivity, table presence, "
        "and the current email_tracking row count."
    ),
)
async def get_database_status() -> dict[str, object]:
    """Return read-only PostgreSQL connection and table diagnostics."""
    logger.info("Debug endpoint requested: GET /api/database/status")
    database_status = await run_in_threadpool(database_service.get_status)
    if database_status.error:
        logger.error("Database status check failed: %s", database_status.error)
    else:
        logger.info(
            "Database status completed: connected=%s table_exists=%s "
            "total_records=%d",
            database_status.connected,
            database_status.table_exists,
            database_status.total_records,
        )
    return {
        "database_connected": database_status.connected,
        "table_exists": database_status.table_exists,
        "total_records": database_status.total_records,
    }


@router.get(
    "/api/tracking/sync",
    tags=["Synchronization"],
    summary="Synchronize tracking updates with the desktop application",
    response_model=list[TrackingSyncRecord],
    responses={400: {"description": "Invalid updated_after timestamp"}},
)
async def synchronize_tracking_records(
    request: Request,
    updated_after: str | None = Query(
        default=None,
        description=(
            "Optional ISO-8601 cursor. Only records updated after this time "
            "are returned."
        ),
    ),
) -> list[TrackingSyncRecord]:
    """Return all or incrementally updated tracking records in ascending order."""
    started_at = time.perf_counter()
    request_time = datetime.now(timezone.utc)
    client_ip = request.client.host if request.client else "unknown"

    parsed_updated_after = None
    if updated_after is not None:
        try:
            parsed_updated_after = parse_iso8601_utc(updated_after)
        except ValueError as exc:
            execution_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "Sync Request Time=%s ClientIP=%s updated_after=%s "
                "Returned Record Count=0 Execution Time=%.2fms Status=invalid",
                request_time.isoformat(),
                client_ip,
                updated_after,
                execution_ms,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="updated_after must be a valid ISO-8601 datetime.",
            ) from exc

    try:
        records = await run_in_threadpool(
            database_service.fetch_sync_records,
            parsed_updated_after,
        )
    except Exception as exc:
        execution_ms = (time.perf_counter() - started_at) * 1000
        logger.error(
            "Sync Request Time=%s ClientIP=%s updated_after=%s "
            "Returned Record Count=0 Execution Time=%.2fms Status=failed Error=%s",
            request_time.isoformat(),
            client_ip,
            updated_after,
            execution_ms,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tracking synchronization is temporarily unavailable.",
        ) from exc

    execution_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "Sync Request Time=%s ClientIP=%s updated_after=%s "
        "Returned Record Count=%d Execution Time=%.2fms Status=success",
        request_time.isoformat(),
        client_ip,
        updated_after,
        len(records),
        execution_ms,
    )
    return [TrackingSyncRecord.model_validate(record) for record in records]
