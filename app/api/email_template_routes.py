"""HTTP routes for email-template management."""

from datetime import datetime, timezone
from uuid import UUID
import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.models.email_template_api import (
    EmailTemplateImageUploadResponse,
    EmailTemplateListItem,
    EmailTemplatePayload,
    EmailTemplateResponse,
)
from app.services.email_templates import (EmailTemplateDatabaseUnavailableError, EmailTemplateNotFoundError,
    EmailTemplateService, EmailTemplateValidationError)
from app.services.email_template_images import (
    EmailTemplateImageError,
    EmailTemplateImageService,
    EmailTemplateImageTooLargeError,
    EmailTemplateImageValidationError,
)
from config.settings import PROJECT_ROOT, load_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/email-templates", tags=["Email Templates"])
settings = load_settings()
template_service = EmailTemplateService(settings.database_url)
template_image_service = EmailTemplateImageService(
    PROJECT_ROOT / "uploads" / "email_templates"
)


def _response(template):
    return EmailTemplateResponse.model_validate(template, from_attributes=True)


def _public_image_url(request: Request, file_name: str) -> str:
    base_url = settings.public_base_url or str(request.base_url)
    return f"{base_url.rstrip('/')}/uploads/email_templates/{file_name}"


@router.post("", response_model=EmailTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(payload: EmailTemplatePayload, request: Request) -> EmailTemplateResponse:
    try:
        template = await run_in_threadpool(template_service.create_template, payload.template_name, payload.subject,
            payload.body_html, payload.signature_html, payload.status, payload.created_by, datetime.now(timezone.utc))
    except EmailTemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmailTemplateDatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Email template storage is temporarily unavailable.") from exc
    logger.info("Email template created: id=%s client_ip=%s", template.id, request.client.host if request.client else "unknown")
    return _response(template)


@router.get("", response_model=list[EmailTemplateListItem])
async def list_templates() -> list[EmailTemplateListItem]:
    try:
        templates = await run_in_threadpool(template_service.list_templates)
    except EmailTemplateDatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Email template storage is temporarily unavailable.") from exc
    return [EmailTemplateListItem.model_validate(item, from_attributes=True) for item in templates]


@router.post(
    "/upload-image",
    response_model=EmailTemplateImageUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an email-template image",
)
async def upload_template_image(
    request: Request,
    file: UploadFile = File(description="Template image file, up to 5 MB."),
) -> EmailTemplateImageUploadResponse:
    """Store one public image for template bodies or signatures."""
    client_ip = request.client.host if request.client else "unknown"
    original_name = file.filename or ""
    try:
        result = await run_in_threadpool(
            template_image_service.upload_image,
            file.file,
            original_name,
        )
    except EmailTemplateImageTooLargeError as exc:
        logger.warning(
            "Email template image validation failed: file_name=%s client=%s reason=too_large",
            original_name,
            client_ip,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except EmailTemplateImageValidationError as exc:
        logger.warning(
            "Email template image validation failed: file_name=%s client=%s reason=%s",
            original_name,
            client_ip,
            exc,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except EmailTemplateImageError as exc:
        logger.error(
            "Email template image upload failed: file_name=%s client=%s error=%s",
            original_name,
            client_ip,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email template image storage is temporarily unavailable.",
        ) from exc
    finally:
        await file.close()

    url = _public_image_url(request, result.file_name)
    logger.info(
        "Email template image uploaded: original_file_name=%s stored_file_name=%s "
        "file_size=%d url=%s client=%s",
        original_name,
        result.file_name,
        result.file_size,
        url,
        client_ip,
    )
    return EmailTemplateImageUploadResponse(
        success=True,
        file_name=result.file_name,
        url=url,
    )


@router.get("/{template_id}", response_model=EmailTemplateResponse)
async def get_template(template_id: UUID) -> EmailTemplateResponse:
    try:
        template = await run_in_threadpool(template_service.get_template, template_id)
    except EmailTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EmailTemplateDatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Email template storage is temporarily unavailable.") from exc
    return _response(template)


@router.put("/{template_id}", response_model=EmailTemplateResponse)
async def update_template(template_id: UUID, payload: EmailTemplatePayload) -> EmailTemplateResponse:
    try:
        template = await run_in_threadpool(template_service.update_template, template_id, payload.template_name,
            payload.subject, payload.body_html, payload.signature_html, payload.status)
    except EmailTemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmailTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EmailTemplateDatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Email template storage is temporarily unavailable.") from exc
    return _response(template)


@router.delete("/{template_id}")
async def delete_template(template_id: UUID) -> dict[str, object]:
    try:
        await run_in_threadpool(template_service.delete_template, template_id)
    except EmailTemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EmailTemplateDatabaseUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Email template storage is temporarily unavailable.") from exc
    return {"success": True, "id": str(template_id), "message": "Email template deleted."}
