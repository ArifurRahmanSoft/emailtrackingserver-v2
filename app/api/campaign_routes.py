"""HTTP routes for independent campaign management."""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from app.models.campaign import Campaign
from app.models.campaign_api import (
    CampaignDeleteResponse,
    CampaignMutationResponse,
    CampaignPayload,
    CampaignResponse,
)
from app.services.campaigns import (
    CampaignDatabaseUnavailableError,
    CampaignNotFoundError,
    CampaignService,
    CampaignValidationError,
    DuplicateCampaignCodeError,
)
from config.settings import load_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/campaigns", tags=["Campaign Management"])
settings = load_settings()
campaign_service = CampaignService(settings.database_url)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CampaignMutationResponse,
    summary="Create a campaign",
)
async def create_campaign(
    payload: CampaignPayload,
    request: Request,
) -> CampaignMutationResponse:
    """Create one campaign-level record without touching tracking tables."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    request_time = datetime.now(timezone.utc)

    try:
        campaign = await run_in_threadpool(
            campaign_service.create_campaign,
            payload.campaign_name,
            payload.campaign_code,
            payload.start_date,
            payload.end_date,
            payload.file_name,
            payload.client_name,
            payload.campaign_offer,
            request_time,
        )
    except DuplicateCampaignCodeError as exc:
        logger.warning(
            "Campaign create rejected: campaign_code=%s client_ip=%s "
            "user_agent=%s request_time=%s reason=duplicate_campaign_code",
            payload.campaign_code,
            client_ip,
            user_agent,
            request_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="campaign_code already exists.",
        ) from exc
    except CampaignValidationError as exc:
        logger.warning(
            "Campaign create validation failed: campaign_code=%s client_ip=%s "
            "user_agent=%s request_time=%s reason=%s",
            payload.campaign_code,
            client_ip,
            user_agent,
            request_time.isoformat(),
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except CampaignDatabaseUnavailableError as exc:
        logger.error(
            "Campaign create failed: campaign_code=%s client_ip=%s user_agent=%s "
            "request_time=%s error=%s",
            payload.campaign_code,
            client_ip,
            user_agent,
            request_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Campaign storage is temporarily unavailable.",
        ) from exc

    logger.info(
        "Campaign created: campaign_id=%s campaign_code=%s client_ip=%s "
        "user_agent=%s request_time=%s",
        campaign.id,
        campaign.campaign_code,
        client_ip,
        user_agent,
        request_time.isoformat(),
    )
    return CampaignMutationResponse(success=True, campaign=_to_response(campaign))


@router.get(
    "",
    response_model=list[CampaignResponse],
    summary="List campaigns",
)
async def list_campaigns(request: Request) -> list[CampaignResponse]:
    """Return all campaigns newest-first."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    request_time = datetime.now(timezone.utc)

    try:
        campaigns = await run_in_threadpool(campaign_service.list_campaigns)
    except CampaignDatabaseUnavailableError as exc:
        logger.error(
            "Campaign list failed: client_ip=%s user_agent=%s request_time=%s "
            "error=%s",
            client_ip,
            user_agent,
            request_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Campaign storage is temporarily unavailable.",
        ) from exc

    logger.info(
        "Campaign list requested: client_ip=%s user_agent=%s returned_count=%d "
        "request_time=%s",
        client_ip,
        user_agent,
        len(campaigns),
        request_time.isoformat(),
    )
    return [_to_response(campaign) for campaign in campaigns]


@router.get(
    "/{id}",
    response_model=CampaignResponse,
    summary="Get one campaign",
)
async def get_campaign(id: UUID, request: Request) -> CampaignResponse:
    """Return one campaign by UUID."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")

    try:
        campaign = await run_in_threadpool(campaign_service.get_campaign, id)
    except CampaignNotFoundError as exc:
        logger.warning(
            "Campaign get rejected: campaign_id=%s client_ip=%s user_agent=%s "
            "reason=not_found",
            id,
            client_ip,
            user_agent,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found.",
        ) from exc
    except CampaignDatabaseUnavailableError as exc:
        logger.error(
            "Campaign get failed: campaign_id=%s client_ip=%s user_agent=%s error=%s",
            id,
            client_ip,
            user_agent,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Campaign storage is temporarily unavailable.",
        ) from exc

    return _to_response(campaign)


@router.put(
    "/{id}",
    response_model=CampaignMutationResponse,
    summary="Update a campaign",
)
async def update_campaign(
    id: UUID,
    payload: CampaignPayload,
    request: Request,
) -> CampaignMutationResponse:
    """Update one campaign without changing created_at or tracking data."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    update_time = datetime.now(timezone.utc)

    try:
        campaign = await run_in_threadpool(
            campaign_service.update_campaign,
            id,
            payload.campaign_name,
            payload.campaign_code,
            payload.start_date,
            payload.end_date,
            payload.file_name,
            payload.client_name,
            payload.campaign_offer,
            update_time,
        )
    except CampaignNotFoundError as exc:
        logger.warning(
            "Campaign update rejected: campaign_id=%s campaign_code=%s "
            "client_ip=%s user_agent=%s update_time=%s reason=not_found",
            id,
            payload.campaign_code,
            client_ip,
            user_agent,
            update_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found.",
        ) from exc
    except DuplicateCampaignCodeError as exc:
        logger.warning(
            "Campaign update rejected: campaign_id=%s campaign_code=%s "
            "client_ip=%s user_agent=%s update_time=%s "
            "reason=duplicate_campaign_code",
            id,
            payload.campaign_code,
            client_ip,
            user_agent,
            update_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="campaign_code already exists.",
        ) from exc
    except CampaignValidationError as exc:
        logger.warning(
            "Campaign update validation failed: campaign_id=%s campaign_code=%s "
            "client_ip=%s user_agent=%s update_time=%s reason=%s",
            id,
            payload.campaign_code,
            client_ip,
            user_agent,
            update_time.isoformat(),
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except CampaignDatabaseUnavailableError as exc:
        logger.error(
            "Campaign update failed: campaign_id=%s campaign_code=%s client_ip=%s "
            "user_agent=%s update_time=%s error=%s",
            id,
            payload.campaign_code,
            client_ip,
            user_agent,
            update_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Campaign storage is temporarily unavailable.",
        ) from exc

    logger.info(
        "Campaign updated: campaign_id=%s campaign_code=%s client_ip=%s "
        "user_agent=%s update_time=%s",
        campaign.id,
        campaign.campaign_code,
        client_ip,
        user_agent,
        campaign.updated_at.isoformat(),
    )
    return CampaignMutationResponse(success=True, campaign=_to_response(campaign))


@router.delete(
    "/{id}",
    response_model=CampaignDeleteResponse,
    summary="Delete a campaign",
)
async def delete_campaign(id: UUID, request: Request) -> CampaignDeleteResponse:
    """Delete only the campaign row; tracking data is not touched."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    request_time = datetime.now(timezone.utc)

    try:
        await run_in_threadpool(campaign_service.delete_campaign, id)
    except CampaignNotFoundError as exc:
        logger.warning(
            "Campaign delete rejected: campaign_id=%s client_ip=%s user_agent=%s "
            "request_time=%s reason=not_found",
            id,
            client_ip,
            user_agent,
            request_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found.",
        ) from exc
    except CampaignDatabaseUnavailableError as exc:
        logger.error(
            "Campaign delete failed: campaign_id=%s client_ip=%s user_agent=%s "
            "request_time=%s error=%s",
            id,
            client_ip,
            user_agent,
            request_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Campaign storage is temporarily unavailable.",
        ) from exc

    logger.info(
        "Campaign deleted: campaign_id=%s client_ip=%s user_agent=%s "
        "request_time=%s tracking_data_untouched=true",
        id,
        client_ip,
        user_agent,
        request_time.isoformat(),
    )
    return CampaignDeleteResponse(
        success=True,
        id=id,
        message="Campaign deleted successfully.",
    )


def _to_response(campaign: Campaign) -> CampaignResponse:
    """Convert an ORM campaign row to an API response model."""
    return CampaignResponse(
        id=campaign.id,
        campaign_name=campaign.campaign_name,
        campaign_code=campaign.campaign_code,
        start_date=campaign.start_date,
        end_date=campaign.end_date,
        file_name=campaign.file_name,
        client_name=campaign.client_name,
        campaign_offer=campaign.campaign_offer,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )
