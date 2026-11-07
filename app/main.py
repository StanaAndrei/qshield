"""Main entrypoint for Q-SHIELD FastAPI service."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.config import settings

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="AI-Driven Orchestration for Quantum-Secured Medical Data Exchange",
)


@app.get("/health", tags=["System"])
async def health_check() -> JSONResponse:
    """System health check endpoint."""
    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "environment": settings.APP_ENV,
            "version": settings.VERSION,
        },
    )
