"""Main entrypoint for Q-SHIELD FastAPI service."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes_transfers import router as transfers_router
from app.api.schemas import ApiErrorDetail, ApiErrorEnvelope, ApiResponse
from app.core.config import settings
from app.core.exceptions import (
    InvalidStateTransitionError,
    PolicyViolationError,
    QShieldError,
    TransferNotFoundError,
)
from app.database.connection import init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager initializing database tables on startup."""
    try:
        init_db()
    except Exception as err:
        logger.warning("Could not auto-initialize database on startup: %s", err)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="AI-Driven Orchestration for Quantum-Secured Medical Data Exchange",
    lifespan=lifespan,
)

# Mount transfer routes
app.include_router(transfers_router, prefix="/api/v1/transfers", tags=["Transfers"])


# -----------------------------------------------------------------------------
# Global Exception Handlers
# -----------------------------------------------------------------------------
@app.exception_handler(TransferNotFoundError)
async def transfer_not_found_handler(request: Request, exc: TransferNotFoundError) -> JSONResponse:
    envelope = ApiErrorEnvelope(
        success=False,
        errors=[ApiErrorDetail(code=exc.code, message=exc.message)],
    )
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=envelope.model_dump(mode="json"))


@app.exception_handler(InvalidStateTransitionError)
async def invalid_transition_handler(request: Request, exc: InvalidStateTransitionError) -> JSONResponse:
    envelope = ApiErrorEnvelope(
        success=False,
        errors=[ApiErrorDetail(code=exc.code, message=exc.message)],
    )
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=envelope.model_dump(mode="json"))


@app.exception_handler(PolicyViolationError)
async def policy_violation_handler(request: Request, exc: PolicyViolationError) -> JSONResponse:
    envelope = ApiErrorEnvelope(
        success=False,
        errors=[ApiErrorDetail(code=exc.code, message=exc.message)],
    )
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=envelope.model_dump(mode="json"))


@app.exception_handler(QShieldError)
async def qshield_error_handler(request: Request, exc: QShieldError) -> JSONResponse:
    envelope = ApiErrorEnvelope(
        success=False,
        errors=[ApiErrorDetail(code=exc.code, message=exc.message)],
    )
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=envelope.model_dump(mode="json"))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details: list[ApiErrorDetail] = []
    for error in exc.errors():
        field_path = ".".join(str(loc) for loc in error.get("loc", []))
        details.append(
            ApiErrorDetail(
                code="VALIDATION_ERROR",
                message=error.get("msg", "Invalid value"),
                field=field_path if field_path else None,
            )
        )
    envelope = ApiErrorEnvelope(success=False, errors=details)
    return JSONResponse(status_code=422, content=envelope.model_dump(mode="json"))


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    envelope = ApiErrorEnvelope(
        success=False,
        errors=[ApiErrorDetail(code="HTTP_ERROR", message=str(exc.detail))],
    )
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(mode="json"))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server exception: %s", exc)
    envelope = ApiErrorEnvelope(
        success=False,
        errors=[ApiErrorDetail(code="INTERNAL_SERVER_ERROR", message="An unexpected error occurred")],
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=envelope.model_dump(mode="json"),
    )


# -----------------------------------------------------------------------------
# System Health Endpoints
# -----------------------------------------------------------------------------
@app.get("/health", tags=["System"])
async def health_check() -> JSONResponse:
    """Root health check endpoint."""
    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "environment": settings.APP_ENV,
            "version": settings.VERSION,
        },
    )


@app.get("/api/v1/health", response_model=ApiResponse[dict[str, str]], tags=["System"])
async def api_health_check() -> ApiResponse[dict[str, str]]:
    """Versioned API health probe returning standard ApiResponse envelope."""
    return ApiResponse(
        data={
            "status": "healthy",
            "environment": settings.APP_ENV,
            "version": settings.VERSION,
        }
    )


__all__ = ["app"]
