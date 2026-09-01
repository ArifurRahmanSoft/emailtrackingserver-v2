"""HTTP routes for independent campaign management."""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from app.models.campaign import Campaign
from app.models.campaign_api import (
    CampaignClientInfoResponse,
    CampaignCodeListResponse,
    CampaignDashboardFilter,
    CampaignDashboardItem,
    CampaignDashboardResponse,
    CampaignDeleteResponse,
    CampaignMutationResponse,
    CampaignPayload,
    CampaignProjectSenderItem,
    CampaignProjectSendersResponse,
    CampaignResponse,
    ClientCampaignDashboardResponse,
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
            payload.client_code,
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
    "/codes",
    response_model=CampaignCodeListResponse,
    summary="List campaign codes",
)
async def list_campaign_codes(request: Request) -> CampaignCodeListResponse:
    """Return all usable campaign codes for EmailAutomation dropdowns."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    request_time = datetime.now(timezone.utc)

    try:
        campaign_codes = await run_in_threadpool(campaign_service.get_campaign_codes)
    except CampaignDatabaseUnavailableError as exc:
        logger.error(
            "Campaign code list failed: client_ip=%s user_agent=%s request_time=%s "
            "error=%s",
            client_ip,
            user_agent,
            request_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Campaign codes are temporarily unavailable.",
        ) from exc

    logger.info(
        "Campaign code list requested: client_ip=%s user_agent=%s returned_count=%d "
        "request_time=%s",
        client_ip,
        user_agent,
        len(campaign_codes),
        request_time.isoformat(),
    )
    return CampaignCodeListResponse(success=True, campaign_codes=campaign_codes)


@router.get(
    "/client-info",
    response_model=CampaignClientInfoResponse,
    summary="Get client information by client code",
)
async def get_client_info(
    request: Request,
    client_code: str | None = None,
) -> CampaignClientInfoResponse:
    """Return unique client information from existing campaign records."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    request_time = datetime.now(timezone.utc)
    clean_client_code = client_code.strip() if client_code else None
    if clean_client_code is None:
        logger.warning(
            "Campaign client info rejected: client_ip=%s user_agent=%s "
            "request_time=%s reason=missing_client_code",
            client_ip,
            user_agent,
            request_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="client_code is required.",
        )

    try:
        result = await run_in_threadpool(
            campaign_service.get_client_info,
            clean_client_code,
        )
    except CampaignValidationError as exc:
        logger.warning(
            "Campaign client info rejected: client_code=%s client_ip=%s "
            "user_agent=%s request_time=%s reason=%s",
            clean_client_code,
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
            "Campaign client info failed: client_code=%s client_ip=%s "
            "user_agent=%s request_time=%s error=%s",
            clean_client_code,
            client_ip,
            user_agent,
            request_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Client information is temporarily unavailable.",
        ) from exc

    if result is None:
        logger.warning(
            "Campaign client info rejected: client_code=%s client_ip=%s "
            "user_agent=%s request_time=%s reason=not_found",
            clean_client_code,
            client_ip,
            user_agent,
            request_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client code not found.",
        )

    logger.info(
        "Campaign client info requested: client_code=%s client_name=%s "
        "client_ip=%s user_agent=%s request_time=%s",
        result.client_code,
        result.client_name,
        client_ip,
        user_agent,
        request_time.isoformat(),
    )
    return CampaignClientInfoResponse(
        success=True,
        client_code=result.client_code,
        client_name=result.client_name,
    )


@router.get(
    "/project-senders",
    response_model=CampaignProjectSendersResponse,
    summary="List sender/project pairs by campaign codes",
)
async def list_campaign_project_senders(
    request: Request,
    campaign_codes: str | None = None,
) -> CampaignProjectSendersResponse:
    """Return unique sender/project pairs for valid campaign codes."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    request_time = datetime.now(timezone.utc)
    if campaign_codes is None or not campaign_codes.strip():
        logger.warning(
            "Campaign project senders rejected: client_ip=%s user_agent=%s "
            "request_time=%s reason=missing_campaign_codes",
            client_ip,
            user_agent,
            request_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="campaign_codes is required.",
        )

    try:
        result = await run_in_threadpool(
            campaign_service.get_project_senders,
            campaign_codes,
        )
    except CampaignValidationError as exc:
        logger.warning(
            "Campaign project senders rejected: campaign_codes=%s client_ip=%s "
            "user_agent=%s request_time=%s reason=%s",
            campaign_codes,
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
            "Campaign project senders failed: campaign_codes=%s client_ip=%s "
            "user_agent=%s request_time=%s error=%s",
            campaign_codes,
            client_ip,
            user_agent,
            request_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Campaign project senders are temporarily unavailable.",
        ) from exc

    logger.info(
        "Campaign project senders requested: campaign_codes=%s valid_campaigns=%d "
        "returned_projects=%d client_ip=%s user_agent=%s request_time=%s",
        campaign_codes,
        len(result.campaign_codes),
        len(result.projects),
        client_ip,
        user_agent,
        request_time.isoformat(),
    )
    return CampaignProjectSendersResponse(
        success=True,
        campaign_codes=result.campaign_codes,
        projects=[
            CampaignProjectSenderItem(
                sender_email=project["sender_email"],
                project_name=project["project_name"],
            )
            for project in result.projects
        ],
    )


@router.get(
    "/dashboard",
    response_model=CampaignDashboardResponse,
    summary="Campaign-wise dashboard metrics",
)
async def get_campaign_dashboard(
    request: Request,
    campaign_code: str | None = None,
) -> CampaignDashboardResponse:
    """Return read-only campaign-wise dashboard reporting metrics."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    request_time = datetime.now(timezone.utc)
    clean_campaign_code = campaign_code.strip() if campaign_code else None

    try:
        dashboard_rows = await run_in_threadpool(
            campaign_service.get_campaign_dashboard,
            clean_campaign_code,
            request_time,
        )
    except CampaignNotFoundError as exc:
        logger.warning(
            "Campaign dashboard rejected: campaign_code=%s client_ip=%s "
            "user_agent=%s request_time=%s reason=not_found",
            clean_campaign_code,
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
            "Campaign dashboard failed: campaign_code=%s client_ip=%s user_agent=%s "
            "request_time=%s error=%s",
            clean_campaign_code,
            client_ip,
            user_agent,
            request_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Campaign dashboard is temporarily unavailable.",
        ) from exc

    logger.info(
        "Campaign dashboard requested: campaign_code=%s client_ip=%s user_agent=%s "
        "returned_count=%d request_time=%s",
        clean_campaign_code,
        client_ip,
        user_agent,
        len(dashboard_rows),
        request_time.isoformat(),
    )
    return CampaignDashboardResponse(
        success=True,
        filter=CampaignDashboardFilter(campaign_code=clean_campaign_code),
        campaigns=_build_campaign_dashboard_items(
            dashboard_rows,
            include_total=clean_campaign_code is None,
        ),
    )


@router.get(
    "/client-dashboard",
    response_model=ClientCampaignDashboardResponse,
    summary="Client-wise campaign dashboard metrics",
)
async def get_client_campaign_dashboard(
    request: Request,
    client_code: str | None = None,
) -> ClientCampaignDashboardResponse:
    """Return aggregate tracking metrics for campaigns owned by one client code."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    request_time = datetime.now(timezone.utc)
    clean_client_code = client_code.strip() if client_code else None
    if clean_client_code is None:
        logger.warning(
            "Client campaign dashboard rejected: client_ip=%s user_agent=%s "
            "request_time=%s reason=missing_client_code",
            client_ip,
            user_agent,
            request_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="client_code is required.",
        )

    try:
        result = await run_in_threadpool(
            campaign_service.get_client_dashboard,
            clean_client_code,
            request_time,
        )
    except CampaignValidationError as exc:
        logger.warning(
            "Client campaign dashboard rejected: client_code=%s client_ip=%s "
            "user_agent=%s request_time=%s reason=%s",
            clean_client_code,
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
            "Client campaign dashboard failed: client_code=%s client_ip=%s "
            "user_agent=%s request_time=%s error=%s",
            clean_client_code,
            client_ip,
            user_agent,
            request_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Client campaign dashboard is temporarily unavailable.",
        ) from exc

    logger.info(
        "Client campaign dashboard requested: client_code=%s client_ip=%s "
        "user_agent=%s campaign_count=%d total_sent=%d request_time=%s",
        result.client_code,
        client_ip,
        user_agent,
        result.campaign_count,
        result.total_sent,
        request_time.isoformat(),
    )
    return ClientCampaignDashboardResponse(
        success=True,
        client_code=result.client_code,
        campaign_count=result.campaign_count,
        campaign_codes=result.campaign_codes,
        total_sent=result.total_sent,
        total_open=result.total_open,
        total_click=result.total_click,
        total_download=result.total_download,
        total_reply=result.total_reply,
        total_bounce=result.total_bounce,
        total_open_by_mail=result.total_open_by_mail,
        total_click_by_mail=result.total_click_by_mail,
        total_download_by_mail=result.total_download_by_mail,
        total_reply_by_mail=result.total_reply_by_mail,
        weekly_sent=result.weekly_sent,
        monthly_sent=result.monthly_sent,
        success_rate=result.success_rate,
        failure_rate=result.failure_rate,
        total_unsubscribe=result.total_unsubscribe,
        last_unsubscribe_time=result.last_unsubscribe_time,
        last_updated=result.last_updated,
    )


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
            payload.client_code,
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
        client_code=campaign.client_code,
        campaign_offer=campaign.campaign_offer,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


def _build_campaign_dashboard_items(
    dashboard_rows: list,
    include_total: bool,
) -> list[CampaignDashboardItem]:
    """Convert campaign dashboard rows and optionally append a total summary row."""
    items = [
        CampaignDashboardItem(
            campaign_code=row.campaign_code,
            campaign_name=row.campaign_name,
            clint_name=row.client_name,
            start_date=row.start_date,
            end_date=row.end_date,
            total_mail_sent=row.total_mail_sent,
            total_click=row.total_click,
            total_reply=row.total_reply,
            total_bounce=row.total_bounce,
            total_download=row.total_download,
            success_rate=row.success_rate,
            failure_rate=row.failure_rate,
            monthly_sent=row.monthly_sent,
            weekly_sent=row.weekly_sent,
            is_total=False,
        )
        for row in dashboard_rows
    ]

    if include_total:
        items.append(_build_campaign_dashboard_total_item(items))

    return items


def _build_campaign_dashboard_total_item(
    items: list[CampaignDashboardItem],
) -> CampaignDashboardItem:
    """Build the all-campaign summary row from already aggregated campaign totals."""
    total_mail_sent = sum(item.total_mail_sent for item in items)
    total_bounce = sum(item.total_bounce for item in items)

    if total_mail_sent == 0:
        success_rate = 0.0
        failure_rate = 0.0
    else:
        success_rate = round(
            ((total_mail_sent - total_bounce) / total_mail_sent) * 100,
            2,
        )
        failure_rate = round((total_bounce / total_mail_sent) * 100, 2)

    return CampaignDashboardItem(
        campaign_code="",
        campaign_name="",
        clint_name="",
        start_date="",
        end_date="",
        total_mail_sent=total_mail_sent,
        total_click=sum(item.total_click for item in items),
        total_reply=sum(item.total_reply for item in items),
        total_bounce=total_bounce,
        total_download=sum(item.total_download for item in items),
        success_rate=success_rate,
        failure_rate=failure_rate,
        monthly_sent=sum(item.monthly_sent for item in items),
        weekly_sent=sum(item.weekly_sent for item in items),
        is_total=True,
    )
