"""Historical telemetry observation store for Q-SHIELD (TASK-4.1).

Records executed transfer telemetry and features into SQLite persistent storage
for model training, baseline calibration, and longitudinal observability.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database.connection import get_session_factory
from app.database.schema import TransferObservationORM
from app.models.telemetry import TransferObservation

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class ITelemetryHistoryStore(ABC):
    """Abstract interface for storing and querying completed transfer observations."""

    @abstractmethod
    def record_observation(self, observation: TransferObservation) -> None:
        """Persist a completed transfer observation."""
        pass

    @abstractmethod
    def get_observations(
        self,
        source_id: str | None = None,
        destination_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TransferObservation]:
        """Query historical observations with optional node filters and pagination."""
        pass

    @abstractmethod
    def get_recent_throughput_mean(
        self,
        source_id: str,
        destination_id: str,
        window_seconds: float = 300.0,
    ) -> float:
        """Calculate mean effective throughput (Mbps) for a route in the given recent time window."""
        pass

    @abstractmethod
    def get_historical_dataset(self, limit: int = 10000) -> list[dict[str, Any]]:
        """Return serialized records suitable for feature extraction and ML model training."""
        pass


class SqliteTelemetryHistoryStore(ITelemetryHistoryStore):
    """SQLite-backed persistent telemetry observation store using SQLAlchemy."""

    def __init__(self, session: Session | None = None) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        if self._session is not None:
            return self._session
        factory = get_session_factory()
        return factory()

    def record_observation(self, observation: TransferObservation) -> None:
        """Persist an observation to SQLite."""
        orm_record = TransferObservationORM.from_domain(observation)
        owns_session = self._session is None
        sess = self.session
        try:
            sess.add(orm_record)
            sess.commit()
            logger.debug(
                "Persisted transfer observation %s (%s->%s, %.2f MB/s)",
                observation.observation_id,
                observation.source_id,
                observation.destination_id,
                observation.effective_throughput_mbps,
            )
        except Exception:
            sess.rollback()
            logger.exception("Failed to persist transfer observation %s", observation.observation_id)
            raise
        finally:
            if owns_session:
                sess.close()

    def get_observations(
        self,
        source_id: str | None = None,
        destination_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TransferObservation]:
        """Query observations ordered by observation timestamp descending."""
        owns_session = self._session is None
        sess = self.session
        try:
            stmt = select(TransferObservationORM)
            if source_id is not None:
                stmt = stmt.where(TransferObservationORM.source_id == source_id)
            if destination_id is not None:
                stmt = stmt.where(TransferObservationORM.destination_id == destination_id)

            stmt = stmt.order_by(desc(TransferObservationORM.observed_at)).offset(offset).limit(limit)
            records = sess.scalars(stmt).all()
            return [r.to_domain() for r in records]
        finally:
            if owns_session:
                sess.close()

    def get_recent_throughput_mean(
        self,
        source_id: str,
        destination_id: str,
        window_seconds: float = 300.0,
    ) -> float:
        """Compute average throughput across transfers in the given window."""
        cutoff = utc_now() - timedelta(seconds=window_seconds)
        owns_session = self._session is None
        sess = self.session
        try:
            stmt = (
                select(TransferObservationORM.effective_throughput_mbps)
                .where(TransferObservationORM.source_id == source_id)
                .where(TransferObservationORM.destination_id == destination_id)
                .where(TransferObservationORM.observed_at >= cutoff)
                .where(TransferObservationORM.success.is_(True))
            )
            throughputs = sess.scalars(stmt).all()
            if not throughputs:
                return 0.0
            return float(sum(throughputs) / len(throughputs))
        finally:
            if owns_session:
                sess.close()

    def get_historical_dataset(self, limit: int = 10000) -> list[dict[str, Any]]:
        """Extract dataset formatted for model retraining."""
        obs_list = self.get_observations(limit=limit)
        dataset: list[dict[str, Any]] = []
        for o in obs_list:
            dataset.append({
                "request_id": str(o.request_id),
                "source_id": o.source_id,
                "destination_id": o.destination_id,
                "data_size_bytes": o.data_size_bytes,
                "priority": o.priority_level,
                "data_category": o.data_category,
                "transfer_duration_seconds": o.actual_duration_seconds,
                "total_duration_seconds": o.actual_duration_seconds + o.queue_wait_seconds,
                "actual_throughput_mbps": o.effective_throughput_mbps,
                "queue_time": o.queue_wait_seconds,
                "success": o.success,
                "observed_at": o.observed_at.isoformat(),
            })
        return dataset


class InMemoryTelemetryHistoryStore(ITelemetryHistoryStore):
    """Thread-safe in-memory store for hermetic testing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._observations: dict[UUID, TransferObservation] = {}

    def record_observation(self, observation: TransferObservation) -> None:
        with self._lock:
            self._observations[observation.observation_id] = observation.model_copy(deep=True)

    def get_observations(
        self,
        source_id: str | None = None,
        destination_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TransferObservation]:
        with self._lock:
            records = list(self._observations.values())
            if source_id is not None:
                records = [r for r in records if r.source_id == source_id]
            if destination_id is not None:
                records = [r for r in records if r.destination_id == destination_id]

            records.sort(key=lambda r: r.observed_at, reverse=True)
            return [r.model_copy(deep=True) for r in records[offset : offset + limit]]

    def get_recent_throughput_mean(
        self,
        source_id: str,
        destination_id: str,
        window_seconds: float = 300.0,
    ) -> float:
        cutoff = utc_now() - timedelta(seconds=window_seconds)
        with self._lock:
            valid = [
                r.effective_throughput_mbps
                for r in self._observations.values()
                if r.source_id == source_id
                and r.destination_id == destination_id
                and r.observed_at >= cutoff
                and r.success
            ]
            if not valid:
                return 0.0
            return float(sum(valid) / len(valid))

    def get_historical_dataset(self, limit: int = 10000) -> list[dict[str, Any]]:
        obs_list = self.get_observations(limit=limit)
        return [
            {
                "request_id": str(o.request_id),
                "source_id": o.source_id,
                "destination_id": o.destination_id,
                "data_size_bytes": o.data_size_bytes,
                "priority": o.priority_level,
                "data_category": o.data_category,
                "transfer_duration_seconds": o.actual_duration_seconds,
                "total_duration_seconds": o.actual_duration_seconds + o.queue_wait_seconds,
                "actual_throughput_mbps": o.effective_throughput_mbps,
                "queue_time": o.queue_wait_seconds,
                "success": o.success,
                "observed_at": o.observed_at.isoformat(),
            }
            for o in obs_list
        ]


__all__ = [
    "ITelemetryHistoryStore",
    "InMemoryTelemetryHistoryStore",
    "SqliteTelemetryHistoryStore",
]
