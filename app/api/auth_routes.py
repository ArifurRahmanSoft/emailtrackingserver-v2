"""Authentication routes for Angular Reporting users."""

import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from app.models.auth_api import (
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthUpdateUserRequest,
    AuthUpdateUserResponse,
    AuthUserListItem,
    AuthUserResponse,
)
from app.services.auth import (
    AuthDatabaseUnavailableError,
    AuthService,
    DuplicateSystemUserError,
    InvalidCredentialsError,
    SystemUserNotFoundError,
)
from config.settings import load_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["Authentication"])
settings = load_settings()
auth_service = AuthService(settings.database_url)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthUserResponse,
    summary="Register a system user",
)
async def register_user(
    payload: AuthRegisterRequest,
    request: Request,
) -> AuthUserResponse:
    """Create one authentication user without returning password data."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    register_time = datetime.now(timezone.utc)

    try:
        result = await run_in_threadpool(
            auth_service.register_user,
            payload.user_id,
            payload.password,
            payload.role,
            register_time,
        )
    except DuplicateSystemUserError as exc:
        logger.warning(
            "System user registration rejected: user_id=%s role=%s client_ip=%s "
            "user_agent=%s register_time=%s reason=duplicate_user_id",
            payload.user_id,
            payload.role,
            client_ip,
            user_agent,
            register_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="user_id already exists.",
        ) from exc
    except AuthDatabaseUnavailableError as exc:
        logger.error(
            "System user registration failed: user_id=%s role=%s client_ip=%s "
            "user_agent=%s register_time=%s error=%s",
            payload.user_id,
            payload.role,
            client_ip,
            user_agent,
            register_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        ) from exc

    logger.info(
        "System user registered: user_id=%s role=%s client_ip=%s user_agent=%s "
        "register_time=%s",
        result.user_id,
        result.role,
        client_ip,
        user_agent,
        result.register_date.isoformat(),
    )
    return AuthUserResponse(
        success=True,
        user_id=result.user_id,
        role=result.role,
        register_date=result.register_date,
    )


@router.get(
    "/users",
    response_model=list[AuthUserListItem],
    summary="List registered system users",
)
async def list_users(request: Request) -> list[AuthUserListItem]:
    """Return all registered users without password data."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    request_time = datetime.now(timezone.utc)

    try:
        users = await run_in_threadpool(auth_service.list_users)
    except AuthDatabaseUnavailableError as exc:
        logger.error(
            "System user list failed: client_ip=%s user_agent=%s request_time=%s "
            "error=%s",
            client_ip,
            user_agent,
            request_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        ) from exc

    logger.info(
        "System user list requested: client_ip=%s user_agent=%s "
        "returned_record_count=%d request_time=%s",
        client_ip,
        user_agent,
        len(users),
        request_time.isoformat(),
    )
    return [
        AuthUserListItem(
            id=user.id,
            user_id=user.user_id,
            role=user.role,
            register_date=user.register_date,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
        for user in users
    ]


@router.put(
    "/users/{id}",
    response_model=AuthUpdateUserResponse,
    summary="Update a registered system user",
)
async def update_user(
    id: UUID,
    payload: AuthUpdateUserRequest,
    request: Request,
) -> AuthUpdateUserResponse:
    """Update one registered user without returning password data."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    update_time = datetime.now(timezone.utc)

    try:
        result = await run_in_threadpool(
            auth_service.update_user,
            id,
            payload.user_id,
            payload.password,
            payload.role,
            update_time,
        )
    except SystemUserNotFoundError as exc:
        logger.warning(
            "System user update rejected: database_primary_key=%s client_ip=%s "
            "user_agent=%s update_time=%s reason=not_found",
            id,
            client_ip,
            user_agent,
            update_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        ) from exc
    except DuplicateSystemUserError as exc:
        logger.warning(
            "System user update rejected: database_primary_key=%s user_id_after=%s "
            "role_after=%s client_ip=%s user_agent=%s update_time=%s "
            "reason=duplicate_user_id",
            id,
            payload.user_id,
            payload.role,
            client_ip,
            user_agent,
            update_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="user_id already exists.",
        ) from exc
    except AuthDatabaseUnavailableError as exc:
        logger.error(
            "System user update failed: database_primary_key=%s client_ip=%s "
            "user_agent=%s update_time=%s error=%s",
            id,
            client_ip,
            user_agent,
            update_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        ) from exc

    logger.info(
        "System user updated: database_primary_key=%s user_id_before=%s "
        "user_id_after=%s role_before=%s role_after=%s client_ip=%s "
        "user_agent=%s update_time=%s",
        result.id,
        result.user_id_before_update,
        result.user_id,
        result.role_before_update,
        result.role,
        client_ip,
        user_agent,
        result.updated_at.isoformat(),
    )
    return AuthUpdateUserResponse(
        success=True,
        id=result.id,
        user_id=result.user_id,
        role=result.role,
        register_date=result.register_date,
        updated_at=result.updated_at,
    )


@router.post(
    "/login",
    response_model=AuthUserResponse,
    summary="Authenticate a system user",
)
async def login_user(
    payload: AuthLoginRequest,
    request: Request,
) -> AuthUserResponse:
    """Authenticate user_id/password and return non-sensitive user metadata."""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    login_time = datetime.now(timezone.utc)

    try:
        result = await run_in_threadpool(
            auth_service.login_user,
            payload.user_id,
            payload.password,
        )
    except InvalidCredentialsError as exc:
        logger.warning(
            "System user login failure: user_id=%s client_ip=%s user_agent=%s "
            "login_time=%s",
            payload.user_id,
            client_ip,
            user_agent,
            login_time.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user_id or password.",
        ) from exc
    except AuthDatabaseUnavailableError as exc:
        logger.error(
            "System user login failed: user_id=%s client_ip=%s user_agent=%s "
            "login_time=%s error=%s",
            payload.user_id,
            client_ip,
            user_agent,
            login_time.isoformat(),
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        ) from exc

    logger.info(
        "System user login success: user_id=%s role=%s client_ip=%s user_agent=%s "
        "login_time=%s",
        result.user_id,
        result.role,
        client_ip,
        user_agent,
        login_time.isoformat(),
    )
    return AuthUserResponse(
        success=True,
        user_id=result.user_id,
        role=result.role,
        register_date=result.register_date,
    )
