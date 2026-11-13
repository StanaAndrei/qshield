"""Unit tests for telemetry metrics, history store, and real-time collector (TASK-4.1)."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import (
    DataCategory,
    PriorityLevel,
    QKDLinkStatus,
    SecurityProfile,
    TransferStatus,
)
from app.database.schema import Base
from app.main import app
from app.models.scheduling import TransferResult
from app.models.telemetry import TransferObservation
from app.models.transfer_request import TransferRequest
from app.telemetry.collector import TelemetryCollector
from app.telemetry.history import (
    InMemoryTelemetryHistoryStore,
    SqliteTelemetryHistoryStore,
)
from app.telemetry.metrics import (
    LinkMetricsAggregator,
    QKDMetricsAggregator,
    RollingWindowStats,
)
from tests.fixtures.mock_repository import InMemoryTransferRepository


@pytest.fixture
def test_db_session() -> Generator[Session, None, None]:
    """In-memory SQLite session with all schema tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


class TestRollingWindowStats:
    def test_rolling_stats_accumulation(self) -> None:
        stats = RollingWindowStats(window_seconds=60.0)
        assert stats.count == 0
        assert stats.mean == 0.0

        stats.add(100.0)
        stats.add(200.0)
        stats.add(300.0)

        assert stats.count == 3
        assert stats.mean == 200.0
        assert stats.min == 100.0
        assert stats.max == 300.0
        assert stats.std > 0.0

    def test_rolling_stats_pruning(self) -> None:
        stats = RollingWindowStats(window_seconds=10.0)
        now = datetime.now(UTC)

        # Add old sample
        old_time = now - timedelta(seconds=20.0)
        stats.add(50.0, timestamp=old_time)

        # Add recent sample
        stats.add(150.0, timestamp=now)

        values = stats.get_values()
        assert len(values) == 1
        assert values[0] == 150.0


class TestLinkMetricsAggregator:
    def test_record_transfer_and_telemetry(self) -> None:
        agg = LinkMetricsAggregator("univ-lab", "poly-clinic", baseline_throughput_mbps=100.0)
        agg.record_transfer(throughput_mbps=120.0, duration_seconds=5.0, success=True, rtt_ms=8.0)
        agg.record_transfer(throughput_mbps=80.0, duration_seconds=6.0, success=False, rtt_ms=12.0)

        telemetry = agg.to_network_telemetry()
        assert telemetry.source_id == "univ-lab"
        assert telemetry.destination_id == "poly-clinic"
        assert telemetry.observed_throughput_mbps == 100.0  # mean of 120 and 80
        assert telemetry.packet_loss_rate == 0.5  # 1 failure out of 2
        assert telemetry.round_trip_time_ms == 12.0


class TestQKDMetricsAggregator:
    def test_qkd_metrics_update(self) -> None:
        agg = QKDMetricsAggregator("univ-lab", "poly-clinic", initial_keys=1000)
        agg.update(
            link_status=QKDLinkStatus.CONNECTED,
            available_keys=850,
            generation_rate_bps=60000.0,
            query_latency_ms=4.2,
        )

        metrics = agg.to_qkd_metrics()
        assert metrics.key_buffer_available_keys == 850
        assert metrics.key_generation_rate_bps == 60000.0
        assert metrics.key_service_latency_ms == 4.2
        assert metrics.link_status == QKDLinkStatus.CONNECTED


class TestTelemetryCollector:
    def test_real_time_active_transfers_dod(self) -> None:
        """TASK-4.1 DoD: Telemetry snapshot reflects active transfers in real time."""
        repo = InMemoryTransferRepository()
        history = InMemoryTelemetryHistoryStore()
        collector = TelemetryCollector(repository=repo, history_store=history)

        # 1. Initially 0 active transfers
        snap_0 = collector.get_snapshot()
        assert snap_0.active_transfers_count == 0

        # 2. Start two transfers in real time
        req_id_1 = uuid4()
        req_id_2 = uuid4()
        collector.notify_transfer_started(req_id_1, "univ-lab", "poly-clinic", 10_000_000)
        collector.notify_transfer_started(req_id_2, "univ-lab", "poly-clinic", 20_000_000)

        snap_active = collector.get_snapshot()
        assert snap_active.active_transfers_count == 2
        assert snap_active.system_load_factor > 0.0

        # 3. Complete first transfer
        res_1 = TransferResult(
            request_id=req_id_1,
            success=True,
            actual_duration_seconds=1.0,
            bytes_transferred=10_000_000,
            effective_throughput_mbps=80.0,
            payload_checksum_sha256="abc123sha",
            verification_success=True,
        )
        collector.record_transfer_completed(res_1)

        snap_one_left = collector.get_snapshot()
        assert snap_one_left.active_transfers_count == 1

        # 4. Complete second transfer
        res_2 = TransferResult(
            request_id=req_id_2,
            success=True,
            actual_duration_seconds=2.0,
            bytes_transferred=20_000_000,
            effective_throughput_mbps=80.0,
            payload_checksum_sha256="def456sha",
            verification_success=True,
        )
        collector.record_transfer_completed(res_2)

        snap_done = collector.get_snapshot()
        assert snap_done.active_transfers_count == 0

        # Observations should have been persisted
        observations = history.get_observations()
        assert len(observations) == 2

    def test_collector_with_repository_queue_depth(self) -> None:
        repo = InMemoryTransferRepository()
        collector = TelemetryCollector(repository=repo)

        # Create 3 queued requests in repo
        for _ in range(3):
            req = TransferRequest(
                request_id=uuid4(),
                source_id="univ-lab",
                destination_id="poly-clinic",
                data_size_bytes=5_000_000,
                priority=PriorityLevel.NORMAL,
                security_profile=SecurityProfile.QKD_AES_256_GCM,
                data_category=DataCategory.DIAGNOSTIC_REPORT,
                status=TransferStatus.QUEUED,
            )
            repo.create_request(req)

        snap = collector.get_snapshot()
        assert snap.queue_length == 3


class TestSqliteTelemetryHistoryStore:
    def test_record_and_query_observations(self, test_db_session: Session) -> None:
        store = SqliteTelemetryHistoryStore(session=test_db_session)

        obs = TransferObservation(
            observation_id=uuid4(),
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=10_000_000,
            priority_level=30,
            data_category="MEDICAL_IMAGE",
            actual_duration_seconds=1.25,
            effective_throughput_mbps=64.0,
            success=True,
            verification_success=True,
            payload_checksum_sha256="hash123",
            queue_wait_seconds=0.5,
        )

        store.record_observation(obs)

        results = store.get_observations(source_id="univ-lab")
        assert len(results) == 1
        assert results[0].observation_id == obs.observation_id
        assert results[0].data_size_bytes == 10_000_000
        assert results[0].effective_throughput_mbps == 64.0

        # Mean throughput
        mean_tp = store.get_recent_throughput_mean("univ-lab", "poly-clinic")
        assert abs(mean_tp - 64.0) < 0.01

        # Historical dataset
        dataset = store.get_historical_dataset()
        assert len(dataset) == 1
        assert dataset[0]["data_size_bytes"] == 10_000_000


class TestTelemetryAPI:
    def test_get_system_telemetry_endpoint(self, test_db_session: Session) -> None:
        from app.database.connection import get_db

        app.dependency_overrides[get_db] = lambda: test_db_session
        try:
            with TestClient(app) as client:
                response = client.get("/api/v1/metrics/system")
                assert response.status_code == 200

                body = response.json()
                assert body["success"] is True
                assert "data" in body
                data = body["data"]
                assert "snapshot_id" in data
                assert "active_transfers_count" in data
                assert "queue_length" in data
                assert "system_load_factor" in data
        finally:
            app.dependency_overrides.clear()

    def test_get_observations_endpoint(self, test_db_session: Session) -> None:
        from app.database.connection import get_db

        app.dependency_overrides[get_db] = lambda: test_db_session
        try:
            with TestClient(app) as client:
                response = client.get("/api/v1/metrics/observations")
                assert response.status_code == 200

                body = response.json()
                assert body["success"] is True
                assert isinstance(body["data"], list)
        finally:
            app.dependency_overrides.clear()
