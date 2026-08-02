"""Authentication routes for Angular Reporting users."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from app.models.auth_api import (
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthUserResponse,
)
from app.services.auth import (
    AuthDatabaseUnavailableError,
    AuthService,
    DuplicateSystemUserError,
    InvalidCredentialsError,
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
