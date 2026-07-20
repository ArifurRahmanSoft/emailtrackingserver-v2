"""Public HTTP endpoints for the tracking server."""

import logging
import os
import platform
import re
import time
from datetime import datetime, timezone
from io import BytesIO

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.concurrency import run_in_threadpool

from app.models.statistics import DashboardStatisticsResponse, SampleStatistics
from app.models.sent_email_registration import (
    SentEmailRegistrationRequest,
    SentEmailRegistrationResponse,
)
from app.models.bounce_tracking import BounceTrackingRequest, BounceTrackingResponse
from app.models.reply_tracking import ReplyTrackingRequest, ReplyTrackingResponse
from app.models.report import ReportFilterOptionsResponse, ReportResponse
from app.models.tracking_sync import (
    MarkSynchronizedRequest,
    MarkSynchronizedResponse,
    TrackingSyncRecord,
)
from app.services.database_tracking import (
    DatabaseTrackingService,
    DatabaseUnavailableError,
    SentEmailRegistration,
)
from app.services.dashboard_statistics import DashboardStatisticsService
from app.services.excel_tracking import ExcelTrackingService
from app.services.reporting import ReportingService
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
dashboard_statistics_service = DashboardStatisticsService(database_service)
reporting_service = ReportingService(database_service)
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


@router.post(
    "/api/tracking/register-send",
    tags=["Tracking"],
    summary="Register sent-email tracking metadata",
    response_model=SentEmailRegistrationResponse,
)
async def register_sent_email(
    payload: SentEmailRegistrationRequest,
) -> SentEmailRegistrationResponse:
    """Register V2 email-send metadata without changing tracking counters."""
    logger.info(
        "Register-send request received: tracking_id=%s sender_mail=%s "
        "recipient_mail=%s message_id=%s",
        payload.tracking_id,
        payload.sender_mail,
        payload.recipient_mail,
        payload.message_id,
    )
    try:
        tracking_id = payload.tracking_id.strip()
        if not CLICK_TRACKING_ID_PATTERN.fullmatch(tracking_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid tracking_id.",
            )

        result = await run_in_threadpool(
            database_service.register_sent_email,
            SentEmailRegistration(
                tracking_id=tracking_id,
                sender_mail=payload.sender_mail,
                recipient_mail=payload.recipient_mail,
                mail_subject=payload.mail_subject,
                project_name=payload.project_name,
                excel_file_path=payload.excel_file_path,
                message_id=payload.message_id,
            ),
        )

        logger.info(
            "Sent email registered: tracking_id=%s sender_mail=%s recipient_mail=%s "
            "project_name=%s excel_file_name=%s message_id=%s status=success",
            tracking_id,
            payload.sender_mail,
            payload.recipient_mail,
            payload.project_name,
            result.excel_file_name,
            payload.message_id,
        )
        return SentEmailRegistrationResponse(
            success=True,
            tracking_id=result.tracking_id,
            excel_file_name=result.excel_file_name,
        )
    except DatabaseUnavailableError as exc:
        logger.exception(
            "Sent email registration failed: tracking_id=%s message_id=%s "
            "status=failure error=%s",
            payload.tracking_id,
            payload.message_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tracking registration is temporarily unavailable.",
        ) from exc
    except HTTPException:
        logger.exception(
            "Sent email registration HTTP exception: tracking_id=%s "
            "sender_mail=%s recipient_mail=%s message_id=%s status=failure",
            payload.tracking_id,
            payload.sender_mail,
            payload.recipient_mail,
            payload.message_id,
        )
        raise
    except Exception:
        logger.exception(
            "Sent email registration unhandled exception: tracking_id=%s "
            "sender_mail=%s recipient_mail=%s message_id=%s status=failure",
            payload.tracking_id,
            payload.sender_mail,
            payload.recipient_mail,
            payload.message_id,
        )
        raise


@router.post(
    "/api/tracking/register-reply",
    tags=["Tracking"],
    summary="Register a recipient reply",
    response_model=ReplyTrackingResponse,
)
async def register_reply(
    payload: ReplyTrackingRequest,
    request: Request,
) -> ReplyTrackingResponse:
    """Increment reply tracking counters for an existing tracking row."""
    try:
        request_body = (
            payload.model_dump(mode="json")
            if hasattr(payload, "model_dump")
            else payload.dict()
        )
        message_id = payload.message_id.strip() if payload.message_id else ""
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")

        logger.info(
            "register-reply request received before database query: "
            "message_id=%s client_ip=%s user_agent=%s request_body=%s",
            message_id,
            client_ip,
            user_agent,
            request_body,
        )

        if not message_id:
            logger.warning(
                "Reply rejected: from_email=%s message_id=%s "
                "reply_time=%s client_ip=%s user_agent=%s reason=missing_message_id",
                payload.from_email,
                message_id,
                payload.reply_time,
                client_ip,
                user_agent,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="message_id is required.",
            )

        reply_time = payload.reply_time or datetime.now(timezone.utc)
        result = await run_in_threadpool(
            database_service.record_reply,
            message_id,
            reply_time,
        )

        logger.info(
            "register-reply database query result: row_found=%s message_id=%s "
            "tracking_id=%s database_primary_key=%s",
            result is not None,
            message_id,
            result.tracking_id if result is not None else None,
            result.database_primary_key if result is not None else None,
        )

        if result is None:
            logger.warning(
                "Reply rejected: from_email=%s message_id=%s "
                "reply_time=%s client_ip=%s user_agent=%s reason=not_found",
                payload.from_email,
                message_id,
                reply_time.isoformat(),
                client_ip,
                user_agent,
            )
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "Message ID not found."},
            )

        logger.info(
            "register-reply counter update: message_id=%s tracking_id=%s "
            "database_primary_key=%s reply_count_before=%s reply_count_after=%d "
            "commit_status=success",
            message_id,
            result.tracking_id,
            result.database_primary_key,
            result.reply_count_before_update,
            result.reply_count,
        )
        logger.info(
            "Reply tracked: tracking_id=%s from_email=%s message_id=%s "
            "reply_count=%d reply_time=%s client_ip=%s user_agent=%s",
            result.tracking_id,
            payload.from_email,
            message_id,
            result.reply_count,
            reply_time.isoformat(),
            client_ip,
            user_agent,
        )
        return ReplyTrackingResponse(
            success=True,
            message_id=message_id,
            tracking_id=result.tracking_id,
            reply_count=result.reply_count,
            first_reply=result.first_reply,
            last_reply=result.last_reply,
        )
    except Exception:
        logger.exception("register-reply failed")
        raise


@router.post(
    "/api/tracking/register-bounce",
    tags=["Tracking"],
    summary="Register a bounced sent email",
    response_model=BounceTrackingResponse,
)
async def register_bounce(
    payload: BounceTrackingRequest,
    request: Request,
) -> BounceTrackingResponse:
    """Mark an existing sent-email tracking row as bounced by Message-ID."""
    try:
        request_body = (
            payload.model_dump(mode="json")
            if hasattr(payload, "model_dump")
            else payload.dict()
        )
        message_id = payload.message_id.strip() if payload.message_id else ""
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")

        logger.info(
            "register-bounce request received before database query: "
            "message_id=%s client_ip=%s user_agent=%s request_body=%s",
            message_id,
            client_ip,
            user_agent,
            request_body,
        )

        if not message_id:
            logger.warning(
                "Bounce rejected: message_id=%s bounce_reason=%s "
                "client_ip=%s user_agent=%s reason=missing_message_id",
                message_id,
                payload.bounce_reason,
                client_ip,
                user_agent,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="message_id is required.",
            )

        result = await run_in_threadpool(
            database_service.record_bounce,
            message_id,
            payload.bounce_reason,
            datetime.now(timezone.utc),
        )

        if result is None:
            logger.warning(
                "Bounce rejected: message_id=%s bounce_reason=%s "
                "client_ip=%s user_agent=%s reason=not_found",
                message_id,
                payload.bounce_reason,
                client_ip,
                user_agent,
            )
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "Message ID not found."},
            )

        logger.info(
            "Bounce tracked: message_id=%s tracking_id=%s database_primary_key=%s "
            "is_bounce_before=%s is_bounce_after=%d bounce_reason=%s "
            "commit_status=success client_ip=%s user_agent=%s",
            message_id,
            result.tracking_id,
            result.database_primary_key,
            result.is_bounce_before_update,
            result.is_bounce,
            result.bounce_reason,
            client_ip,
            user_agent,
        )
        return BounceTrackingResponse(
            success=True,
            message_id=result.message_id,
            tracking_id=result.tracking_id,
            is_bounce=result.is_bounce,
            bounce_time=result.bounce_time,
            bounce_reason=result.bounce_reason,
        )
    except Exception:
        logger.exception("register-bounce failed")
        raise


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
            database_updated = await run_in_threadpool(
                database_service.record_open,
                tracking_id,
                result.open_count,
                client_ip,
                user_agent,
                occurred_at,
            )
            if database_updated:
                logger.info(
                    "PostgreSQL tracking update completed: TrackingId=%s "
                    "EventType=open DatabaseUpdateStatus=success",
                    tracking_id,
                )
            else:
                logger.warning(
                    "PostgreSQL tracking update skipped: TrackingId=%s "
                    "EventType=open DatabaseUpdateStatus=not_found",
                    tracking_id,
                )
        except Exception as database_exc:
            # Excel remains authoritative when PostgreSQL is unavailable.
            logger.error(
                "PostgreSQL tracking update failed: TrackingId=%s EventType=open "
                "DatabaseUpdateStatus=failure Error=%s",
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
    "/api/dashboard/statistics",
    tags=["Dashboard"],
    summary="Return dashboard tracking statistics",
    response_model=DashboardStatisticsResponse,
)
async def get_dashboard_statistics() -> DashboardStatisticsResponse:
    """Return aggregate dashboard statistics from PostgreSQL only."""
    try:
        result = await run_in_threadpool(
            dashboard_statistics_service.get_statistics
        )
        logger.info(
            "Dashboard statistics generated: total_sent=%d total_bounce=%d",
            result.total_sent,
            result.total_bounce,
        )
        return result
    except Exception as exc:
        logger.error(
            "Dashboard statistics failed: Error=%s",
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard statistics are temporarily unavailable.",
        ) from exc


@router.get(
    "/api/report",
    tags=["Reports"],
    summary="Return paginated tracking report rows",
    response_model=ReportResponse,
)
async def get_report(
    page: int = Query(default=1, description="Report page number."),
    page_size: int = Query(default=20, description="Rows per page, maximum 100."),
    sender_email: str | None = Query(default=None, description="Exact sender email."),
    project_name: str | None = Query(default=None, description="Exact project name."),
    is_reply: bool = Query(default=False, description="Only rows with replies."),
    is_bounce: bool = Query(default=False, description="Only bounced rows."),
    is_open: bool = Query(default=False, description="Only opened rows."),
    is_click: bool = Query(default=False, description="Only clicked rows."),
    is_download: bool = Query(default=False, description="Only downloaded rows."),
) -> ReportResponse:
    """Return tracking rows using server-side pagination."""
    started_at = time.perf_counter()
    filters = ReportingService.build_filters(
        sender_email=sender_email,
        project_name=project_name,
        is_reply=is_reply,
        is_bounce=is_bounce,
        is_open=is_open,
        is_click=is_click,
        is_download=is_download,
    )
    try:
        result = await run_in_threadpool(
            reporting_service.get_report,
            page,
            page_size,
            sender_email,
            project_name,
            is_reply,
            is_bounce,
            is_open,
            is_click,
            is_download,
        )
    except Exception as exc:
        logger.error(
            "Report request failed: RequestedPage=%s RequestedPageSize=%s "
            "AppliedFilters=%s Error=%s",
            page,
            page_size,
            filters,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report data is temporarily unavailable.",
        ) from exc

    execution_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "Report requested: RequestedPage=%s RequestedPageSize=%s TotalRecords=%d "
        "ReturnedRows=%d AppliedFilters=%s ExecutionTime=%.2fms",
        page,
        page_size,
        result.total_records,
        len(result.items),
        filters,
        execution_ms,
    )
    return result


@router.get(
    "/api/report/filter-options",
    tags=["Reports"],
    summary="Return available report filter dropdown values",
    response_model=ReportFilterOptionsResponse,
)
async def get_report_filter_options() -> ReportFilterOptionsResponse:
    """Return distinct sender email and project name filter options."""
    started_at = time.perf_counter()
    logger.info("Report filter options requested")
    try:
        result = await run_in_threadpool(reporting_service.get_filter_options)
    except Exception as exc:
        logger.error(
            "Report filter options failed: Error=%s",
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report filter options are temporarily unavailable.",
        ) from exc

    execution_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "Report filter options returned: SenderEmails=%d ProjectNames=%d "
        "ExecutionTime=%.2fms",
        len(result.sender_emails),
        len(result.project_names),
        execution_ms,
    )
    return result


@router.get(
    "/api/report/export",
    tags=["Reports"],
    summary="Export filtered report rows to Excel",
    response_class=StreamingResponse,
)
async def export_report(
    sender_email: str | None = Query(default=None, description="Exact sender email."),
    project_name: str | None = Query(default=None, description="Exact project name."),
    is_reply: bool = Query(default=False, description="Only rows with replies."),
    is_bounce: bool = Query(default=False, description="Only bounced rows."),
    is_open: bool = Query(default=False, description="Only opened rows."),
    is_click: bool = Query(default=False, description="Only clicked rows."),
    is_download: bool = Query(default=False, description="Only downloaded rows."),
) -> StreamingResponse:
    """Export every matching report row to an Excel workbook."""
    started_at = time.perf_counter()
    filters = ReportingService.build_filters(
        sender_email=sender_email,
        project_name=project_name,
        is_reply=is_reply,
        is_bounce=is_bounce,
        is_open=is_open,
        is_click=is_click,
        is_download=is_download,
    )
    logger.info("Report export started: AppliedFilters=%s", filters)
    try:
        result = await run_in_threadpool(
            reporting_service.export_report,
            sender_email,
            project_name,
            is_reply,
            is_bounce,
            is_open,
            is_click,
            is_download,
        )
    except Exception as exc:
        logger.error(
            "Report export failed: AppliedFilters=%s Error=%s",
            filters,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report export is temporarily unavailable.",
        ) from exc

    execution_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "Report export completed: AppliedFilters=%s TotalExportedRows=%d "
        "GeneratedFilename=%s ExecutionTime=%.2fms",
        filters,
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


@router.post(
    "/api/tracking/mark-synchronized",
    tags=["Synchronization"],
    summary="Mark one tracking row synchronized to Excel",
    response_model=MarkSynchronizedResponse,
)
async def mark_tracking_synchronized(
    payload: MarkSynchronizedRequest,
) -> MarkSynchronizedResponse:
    """Persist only last_synchronize_time after Excel update succeeds."""
    tracking_id = payload.tracking_id.strip()
    if not CLICK_TRACKING_ID_PATTERN.fullmatch(tracking_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid tracking_id.",
        )

    try:
        updated = await run_in_threadpool(
            database_service.mark_synchronized,
            tracking_id,
            payload.last_synchronize_time,
        )
    except Exception as exc:
        logger.error(
            "Mark synchronized failed: tracking_id=%s Error=%s",
            tracking_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tracking synchronization marker is temporarily unavailable.",
        ) from exc

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tracking ID not found.",
        )

    logger.info(
        "Tracking marked synchronized: tracking_id=%s last_synchronize_time=%s",
        tracking_id,
        payload.last_synchronize_time.isoformat(),
    )
    return MarkSynchronizedResponse(
        success=True,
        tracking_id=tracking_id,
        last_synchronize_time=payload.last_synchronize_time,
    )
