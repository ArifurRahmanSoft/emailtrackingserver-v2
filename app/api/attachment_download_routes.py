"""Tracked Attachment Library download endpoint."""

import logging
from io import BytesIO
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.attachment_routes import attachment_service
from app.services.attachment_library import (
    AttachmentLibraryError,
    AttachmentNotFoundError,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Attachment Downloads"])


@router.get(
    "/download/{tracking_id}/{attachment_id}",
    response_class=StreamingResponse,
    summary="Track and download an active attachment",
    responses={404: {"description": "Tracking ID or attachment not found"}},
)
async def download_attachment(
    tracking_id: str,
    attachment_id: str,
    request: Request,
) -> StreamingResponse:
    """Increment download counters, then return the stored file."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    download_time = datetime.now(timezone.utc)

    try:
        parsed_attachment_id = UUID(attachment_id)
    except ValueError as exc:
        logger.warning(
            "Attachment download rejected: tracking_id=%s attachment_id=%s "
            "client_ip=%s reason=invalid_attachment_id",
            tracking_id,
            attachment_id,
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found.",
        ) from exc

    try:
        result = await run_in_threadpool(
            attachment_service.track_download,
            tracking_id,
            parsed_attachment_id,
            download_time,
        )
    except AttachmentNotFoundError as exc:
        logger.warning(
            "Attachment download rejected: tracking_id=%s attachment_id=%s "
            "client_ip=%s reason=%s",
            tracking_id,
            parsed_attachment_id,
            client_ip,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tracking ID or active attachment not found.",
        ) from exc
    except AttachmentLibraryError as exc:
        logger.error(
            "Attachment download failed: tracking_id=%s attachment_id=%s "
            "client_ip=%s error=%s",
            tracking_id,
            parsed_attachment_id,
            client_ip,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Attachment download is temporarily unavailable.",
        ) from exc

    logger.info(
        "Attachment downloaded: tracking_id=%s attachment_id=%s "
        "original_file_name=%s client_ip=%s user_agent=%s download_time=%s "
        "download_count=%d",
        tracking_id,
        parsed_attachment_id,
        result.original_file_name,
        client_ip,
        user_agent,
        download_time.isoformat(),
        result.download_count,
    )
    encoded_name = quote(result.original_file_name)
    content_disposition = (
        f"attachment; filename*=utf-8''{encoded_name}"
        if encoded_name != result.original_file_name
        else f'attachment; filename="{result.original_file_name}"'
    )
    return StreamingResponse(
        BytesIO(result.file_bytes),
        media_type=result.content_type,
        headers={"Content-Disposition": content_disposition},
    )
