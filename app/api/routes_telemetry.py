"""FastAPI telemetry and system metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import ApiResponse
from app.database.connection import get_db
from app.database.repository import ITransferRepository, SqliteTransferRepository
from app.models.telemetry import TelemetrySnapshot, TransferObservation
from app.telemetry.collector import TelemetryCollector
from app.telemetry.history import SqliteTelemetryHistoryStore

router = APIRouter()

_collector_instance: TelemetryCollector | None = None


def get_repository(session: Session = Depends(get_db)) -> ITransferRepository:
    return SqliteTransferRepository(session=session)


def get_telemetry_collector(
    session: Session = Depends(get_db),
) -> TelemetryCollector:
    """Dependency providing singleton or session-bound TelemetryCollector."""
    global _collector_instance
    if _collector_instance is None:
        repo = SqliteTransferRepository(session=session)
        history = SqliteTelemetryHistoryStore(session=session)
        _collector_instance = TelemetryCollector(repository=repo, history_store=history)
    return _collector_instance


@router.get(
    "/system",
    response_model=ApiResponse[TelemetrySnapshot],
    summary="Get system telemetry snapshot",
    description="Fetches real-time consolidated system metrics including active transfers, queue depth, and link metrics.",
)
async def get_system_metrics(
    collector: TelemetryCollector = Depends(get_telemetry_collector),
) -> ApiResponse[TelemetrySnapshot]:
    """Return the current consolidated system snapshot."""
    snapshot = collector.get_snapshot()
    return ApiResponse(data=snapshot)


@router.get(
    "/observations",
    response_model=ApiResponse[list[TransferObservation]],
    summary="Get historical transfer observations",
    description="Queries historical transfer observation log from SQLite store.",
)
async def get_observations(
    limit: int = 100,
    offset: int = 0,
    session: Session = Depends(get_db),
) -> ApiResponse[list[TransferObservation]]:
    history = SqliteTelemetryHistoryStore(session=session)
    records = history.get_observations(limit=limit, offset=offset)
    return ApiResponse(data=records)


__all__ = ["get_telemetry_collector", "router"]
