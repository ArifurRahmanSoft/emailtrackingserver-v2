"""API for registering tracking-to-attachment mappings."""

import logging
import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.attachment_routes import attachment_service
from app.services.attachment_library import (
    AttachmentLibraryError,
    AttachmentValidationError,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Attachment Mapping"])


class AttachmentMappingRequest(BaseModel):
    """Intentionally permissive input so validation failures return HTTP 400."""

    tracking_id: str | None = None
    attachment_ids: list[str] | None = None


class AttachmentMappingResponse(BaseModel):
    """Count of newly created, non-duplicate mappings."""

    success: bool
    created: int


@router.post(
    "/api/tracking/attachments",
    response_model=AttachmentMappingResponse,
    summary="Register attachments for a tracking ID",
    responses={400: {"description": "Invalid or inactive attachment mapping"}},
)
async def register_attachment_mappings(
    payload: AttachmentMappingRequest,
    request: Request,
) -> AttachmentMappingResponse:
    """Validate and idempotently create attachment mapping rows."""
    started_at = time.perf_counter()
    tracking_id = (payload.tracking_id or "").strip()
    supplied_ids = payload.attachment_ids or []
    client_ip = request.client.host if request.client else "unknown"

    if not tracking_id or len(tracking_id) > 128:
        execution_ms = (time.perf_counter() - started_at) * 1000
        logger.warning(
            "Attachment mapping validation failed: tracking_id=%s "
            "attachment_ids=%s created_rows=0 execution_time_ms=%.2f "
            "client=%s reason=invalid_tracking_id",
            tracking_id,
            supplied_ids,
            execution_ms,
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tracking_id must not be empty.",
        )

    if not supplied_ids:
        execution_ms = (time.perf_counter() - started_at) * 1000
        logger.warning(
            "Attachment mapping validation failed: tracking_id=%s "
            "attachment_ids=%s created_rows=0 execution_time_ms=%.2f "
            "client=%s reason=empty_attachment_ids",
            tracking_id,
            supplied_ids,
            execution_ms,
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="attachment_ids must not be empty.",
        )

    try:
        parsed_ids = list(dict.fromkeys(UUID(value) for value in supplied_ids))
    except (TypeError, ValueError, AttributeError) as exc:
        execution_ms = (time.perf_counter() - started_at) * 1000
        logger.warning(
            "Attachment mapping validation failed: tracking_id=%s "
            "attachment_ids=%s created_rows=0 execution_time_ms=%.2f "
            "client=%s reason=invalid_attachment_id",
            tracking_id,
            supplied_ids,
            execution_ms,
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Every attachment_id must be a valid UUID.",
        ) from exc

    try:
        created = await run_in_threadpool(
            attachment_service.register_mappings,
            tracking_id,
            parsed_ids,
            datetime.now(timezone.utc),
        )
    except AttachmentValidationError as exc:
        execution_ms = (time.perf_counter() - started_at) * 1000
        logger.warning(
            "Attachment mapping validation failed: tracking_id=%s "
            "attachment_ids=%s created_rows=0 execution_time_ms=%.2f "
            "client=%s reason=%s",
            tracking_id,
            supplied_ids,
            execution_ms,
            client_ip,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except AttachmentLibraryError as exc:
        execution_ms = (time.perf_counter() - started_at) * 1000
        logger.error(
            "Attachment mapping failed: tracking_id=%s attachment_ids=%s "
            "created_rows=0 execution_time_ms=%.2f client=%s error=%s",
            tracking_id,
            supplied_ids,
            execution_ms,
            client_ip,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Attachment mapping is temporarily unavailable.",
        ) from exc

    execution_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "Attachment mapping completed: tracking_id=%s attachment_ids=%s "
        "created_rows=%d execution_time_ms=%.2f client=%s",
        tracking_id,
        supplied_ids,
        created,
        execution_ms,
        client_ip,
    )
    return AttachmentMappingResponse(success=True, created=created)
