"""Isolated HTTP routes for the server-side Attachment Library."""

import logging
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.models.attachment_api import (
    AttachmentDeleteResponse,
    AttachmentListItem,
    AttachmentUploadResponse,
)
from app.services.attachment_library import (
    AttachmentLibraryError,
    AttachmentLibraryService,
    AttachmentNotFoundError,
    AttachmentTooLargeError,
    AttachmentValidationError,
    DuplicateAttachmentError,
)
from config.settings import PROJECT_ROOT, load_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/attachments", tags=["Attachment Library"])
settings = load_settings()
attachment_service = AttachmentLibraryService(
    database_url=settings.database_url,
    attachment_folder=PROJECT_ROOT / "attachments",
)


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    response_model=AttachmentUploadResponse,
    summary="Upload an attachment",
)
async def upload_attachment(
    request: Request,
    file: UploadFile = File(description="Attachment file, up to 50 MB."),
) -> AttachmentUploadResponse:
    """Store one uniquely named file and its original metadata."""
    client_ip = request.client.host if request.client else "unknown"
    original_name = file.filename or ""
    try:
        attachment = await run_in_threadpool(
            attachment_service.upload,
            file.file,
            original_name,
            file.content_type,
        )
    except AttachmentTooLargeError as exc:
        logger.warning(
            "Attachment validation failed: action=upload file_name=%s "
            "client=%s reason=too_large",
            original_name,
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except DuplicateAttachmentError as exc:
        logger.warning(
            "Attachment validation failed: action=upload file_name=%s "
            "client=%s reason=duplicate_active_file",
            original_name,
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except AttachmentValidationError as exc:
        logger.warning(
            "Attachment validation failed: action=upload file_name=%s "
            "client=%s reason=%s",
            original_name,
            client_ip,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except AttachmentLibraryError as exc:
        logger.error(
            "Attachment upload failed: file_name=%s client=%s error=%s",
            original_name,
            client_ip,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Attachment storage is temporarily unavailable.",
        ) from exc
    finally:
        await file.close()

    logger.info(
        "Attachment uploaded: attachment_id=%s original_file_name=%s "
        "file_size=%d content_type=%s client=%s",
        attachment.attachment_id,
        attachment.original_file_name,
        attachment.file_size,
        attachment.content_type,
        client_ip,
    )
    return AttachmentUploadResponse.model_validate(attachment)


@router.get(
    "/list",
    response_model=list[AttachmentListItem],
    summary="List active attachments",
)
async def list_attachments() -> list[AttachmentListItem]:
    """Return active attachments ordered from newest to oldest."""
    attachments = await run_in_threadpool(attachment_service.list_active)
    logger.info("Attachment list requested: returned_count=%d", len(attachments))
    return [AttachmentListItem.model_validate(item) for item in attachments]


@router.delete(
    "/{attachment_id}",
    response_model=AttachmentDeleteResponse,
    summary="Soft-delete an attachment",
)
async def delete_attachment(
    attachment_id: UUID,
    request: Request,
) -> AttachmentDeleteResponse:
    """Mark an attachment inactive without deleting its stored file."""
    client_ip = request.client.host if request.client else "unknown"
    try:
        await run_in_threadpool(attachment_service.deactivate, attachment_id)
    except AttachmentNotFoundError as exc:
        logger.warning(
            "Attachment delete validation failed: attachment_id=%s client=%s "
            "reason=not_found_or_inactive",
            attachment_id,
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except AttachmentLibraryError as exc:
        logger.error(
            "Attachment delete failed: attachment_id=%s client=%s error=%s",
            attachment_id,
            client_ip,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Attachment storage is temporarily unavailable.",
        ) from exc

    logger.info(
        "Attachment deleted: attachment_id=%s client=%s physical_file_retained=true",
        attachment_id,
        client_ip,
    )
    return AttachmentDeleteResponse(
        attachment_id=attachment_id,
        message="Attachment removed from the active library.",
    )
