"""Shared Pytest test harness and fixtures for Q-SHIELD."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import (
    DataCategory,
    PriorityLevel,
    QKDLinkStatus,
    SecurityProfile,
    TransferStatus,
)
from app.main import app
from app.models.telemetry import NetworkTelemetry, QKDResourceMetrics, TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from tests.fixtures.mock_repository import InMemoryTransferRepository


@pytest.fixture
def sqlite_engine() -> Generator[Engine, None, None]:
    """Provides an isolated, in-memory SQLite database engine."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(sqlite_engine: Engine) -> Generator[Session, None, None]:
    """Provides a transactional SQLAlchemy session bound to in-memory SQLite."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=sqlite_engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def in_memory_repo() -> InMemoryTransferRepository:
    """Provides a clean, thread-safe in-memory transfer repository."""
    return InMemoryTransferRepository()


@pytest.fixture
def sample_transfer_request() -> TransferRequest:
    """Provides a valid canonical TransferRequest instance."""
    return TransferRequest(
        request_id=uuid4(),
        source_id="univ-lab",
        destination_id="poly-clinic",
        data_size_bytes=5_242_880,  # 5 MB diagnostic report
        priority=PriorityLevel.NORMAL,
        submitted_at=datetime.now(UTC),
        deadline=None,
        security_profile=SecurityProfile.QKD_AES_256_GCM,
        data_category=DataCategory.DIAGNOSTIC_REPORT,
        status=TransferStatus.SUBMITTED,
        metadata={"study_id": "test-study-001"},
    )


@pytest.fixture
def mock_telemetry_generator() -> Generator[Any, None, None]:
    """Generates synthetic TelemetrySnapshots for scheduling and prediction testing."""

    def _factory(
        source_id: str = "univ-lab",
        destination_id: str = "poly-clinic",
        throughput_mbps: float = 100.0,
        packet_loss: float = 0.001,
        rtt_ms: float = 12.5,
        available_keys: int = 1500,
        key_gen_rate_bps: float = 64_000.0,
        queue_len: int = 3,
        active_transfers: int = 1,
    ) -> TelemetrySnapshot:
        link_key = f"{source_id}->{destination_id}"
        return TelemetrySnapshot(
            snapshot_id=uuid4(),
            timestamp=datetime.now(UTC),
            active_transfers_count=active_transfers,
            queue_length=queue_len,
            network_metrics={
                link_key: NetworkTelemetry(
                    source_id=source_id,
                    destination_id=destination_id,
                    observed_throughput_mbps=throughput_mbps,
                    packet_loss_rate=packet_loss,
                    round_trip_time_ms=rtt_ms,
                    is_simulated=True,
                )
            },
            qkd_metrics={
                link_key: QKDResourceMetrics(
                    source_id=source_id,
                    destination_id=destination_id,
                    link_status=QKDLinkStatus.CONNECTED,
                    key_buffer_available_keys=available_keys,
                    key_generation_rate_bps=key_gen_rate_bps,
                    key_service_latency_ms=2.5,
                    is_simulated=True,
                )
            },
            system_load_factor=0.35,
        )

    yield _factory


@pytest.fixture
def test_client() -> Generator[TestClient, None, None]:
    """Provides a Starlette/FastAPI TestClient against the root application."""
    with TestClient(app) as client:
        yield client
