"""EmailTrackingServer application entry point."""

import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.api.attachment_routes import attachment_service, router as attachment_router
from app.api.attachment_download_routes import router as attachment_download_router
from app.api.attachment_mapping_routes import router as attachment_mapping_router
from app.api.routes import database_service, router, tracking_service
from app.utils.logging import configure_logging
from config.settings import Settings, load_settings

settings: Settings = load_settings()
configure_logging(
    settings.log_folder,
    settings.log_level,
    settings.application_name,
    settings.environment,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Prepare runtime folders and record application lifecycle events."""
    try:
        tracking_service.initialize()
        logger.info("Tracking workbook ready at %s", settings.tracking_file)
    except Exception as exc:
        # Keep serving the pixel if the drive is unavailable or the file is locked.
        logger.error(
            "Tracking workbook initialization failed: %s", exc, exc_info=True
        )
    try:
        database_service.initialize()
        logger.info(
            "PostgreSQL connection ready; V2 database '%s' tables verified",
            settings.expected_database_name,
        )
    except Exception as exc:
        # Database availability never prevents Excel tracking or application startup.
        logger.error("PostgreSQL initialization failed: %s", exc, exc_info=True)
    try:
        attachment_service.initialize()
        logger.info("Attachment Library storage and database table ready")
    except Exception as exc:
        logger.error("Attachment Library initialization failed: %s", exc, exc_info=True)
    logger.info(
        "%s started; environment=%s public_base_url=%s",
        settings.application_name,
        settings.environment,
        settings.public_base_url or "not-configured",
    )
    yield
    attachment_service.dispose()
    database_service.dispose()
    logger.info("%s stopped", settings.application_name)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Record every request with its method, path, status, and duration."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started_at = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = (time.perf_counter() - started_at) * 1000
            status_code = response.status_code if response is not None else 500
            client_host = request.client.host if request.client else "unknown"
            logger.info(
                "%s %s status=%d duration_ms=%.2f client=%s",
                request.method,
                request.url.path,
                status_code,
                duration_ms,
                client_host,
            )


class GlobalExceptionMiddleware(BaseHTTPMiddleware):
    """Convert unexpected application failures into safe JSON responses."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            logger.exception(
                "Unhandled error while processing %s", request.url.path, exc_info=exc
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": {
                        "type": "internal_server_error",
                        "message": "An unexpected error occurred.",
                    }
                },
            )


app = FastAPI(
    title=settings.application_name,
    description="Independent Version 2 email tracking service.",
    version="3.0.0-phase3",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)
app.add_middleware(GlobalExceptionMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.include_router(router)
app.include_router(attachment_router)
app.include_router(attachment_download_router)
app.include_router(attachment_mapping_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    _: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Return a consistent JSON response for HTTP errors."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"type": "http_error", "message": str(exc.detail)}},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return structured JSON when request validation fails."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "type": "validation_error",
                "message": "The request could not be validated.",
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected failures and avoid exposing internal details."""
    logger.exception("Unhandled error while processing %s", request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "type": "internal_server_error",
                "message": "An unexpected error occurred.",
            }
        },
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)
