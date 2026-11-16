"""Integration tests for Q-SHIELD Dashboard data retrieval and components (TASK-6.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import (
    DataCategory,
    PriorityLevel,
    SecurityProfile,
    TransferStatus,
)
from app.database.repository import SqliteTransferRepository
from app.database.schema import Base
from app.models.policy_decision import PolicyDecision
from app.models.telemetry import TransferObservation
from app.models.transfer_request import TransferRequest
from app.telemetry.history import SqliteTelemetryHistoryStore
from dashboard.components.metrics_cards import render_kpi_cards, render_secondary_kpis
from dashboard.data import (
    format_bytes,
    format_duration,
    format_throughput,
    format_timestamp,
    get_active_transfers,
    get_all_transfers,
    get_completed_transfers,
    get_history_store,
    get_kpi_summary,
    get_qkd_links_metrics,
    get_queue_transfers,
    get_repository,
    get_request_policy_decisions,
    get_request_transitions,
    get_transfer_observations,
)
from dashboard.pages.operations import render_operations_page


@pytest.fixture
def test_db_session() -> Session:
    """Creates an in-memory SQLite database session with full schema initialized."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    return session


def _create_sample_request(
    status: TransferStatus = TransferStatus.SUBMITTED,
    priority: PriorityLevel = PriorityLevel.NORMAL,
    submitted_at: datetime | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> TransferRequest:
    """Helper to create valid TransferRequest instances for testing."""
    now = datetime.now(UTC)
    return TransferRequest(
        request_id=uuid4(),
        source_id="univ-lab",
        destination_id="poly-clinic",
        data_size_bytes=10_485_760,  # 10 MB
        priority=priority,
        submitted_at=submitted_at or now,
        deadline=now + timedelta(minutes=10),
        security_profile=SecurityProfile.QKD_AES_256_GCM,
        data_category=DataCategory.DIAGNOSTIC_REPORT,
        status=status,
        transfer_started_at=started_at,
        transfer_completed_at=completed_at,
    )


# -----------------------------------------------------------------------------
# Formatting Tests
# -----------------------------------------------------------------------------


def test_format_bytes() -> None:
    """Verifies byte size formatting across scales."""
    assert format_bytes(None) == "N/A"
    assert format_bytes(500) == "500 B"
    assert format_bytes(2048) == "2.00 KB"
    assert format_bytes(5 * 1024 * 1024) == "5.00 MB"
    assert format_bytes(2 * 1024 * 1024 * 1024) == "2.00 GB"


def test_format_duration() -> None:
    """Verifies duration formatting for seconds and milliseconds."""
    assert format_duration(None) == "N/A"
    assert format_duration(0.045) == "45.0 ms"
    assert format_duration(12.345) == "12.35 s"


def test_format_throughput() -> None:
    """Verifies network throughput formatting in Mbps."""
    assert format_throughput(None) == "N/A"
    assert format_throughput(125.75) == "125.75 Mbps"


def test_format_timestamp() -> None:
    """Verifies timestamp formatting with UTC normalization."""
    assert format_timestamp(None) == "N/A"
    dt_utc = datetime(2026, 11, 16, 20, 0, 0, tzinfo=UTC)
    assert format_timestamp(dt_utc) == "2026-11-16 20:00:00 UTC"
    dt_naive = datetime(2026, 11, 16, 20, 0, 0)
    assert format_timestamp(dt_naive) == "2026-11-16 20:00:00 UTC"


# -----------------------------------------------------------------------------
# QKD Metrics Tests
# -----------------------------------------------------------------------------


def test_get_qkd_links_metrics_defaults() -> None:
    """Verifies default QKD link buffer metrics adhere to spec §16.3 provenance."""
    metrics = get_qkd_links_metrics()
    assert len(metrics) > 0
    for link in metrics:
        assert "link_key" in link
        assert "source_id" in link
        assert "destination_id" in link
        assert link["status"] in ("CONNECTED", "DEGRADED", "UNAVAILABLE")
        assert link["available_keys"] >= 0
        assert 0.0 <= link["buffer_percent"] <= 100.0
        assert link["is_simulated"] is True


def test_get_qkd_links_metrics_custom() -> None:
    """Verifies custom link topology queries."""
    custom = [("alpha", "beta"), ("gamma", "delta")]
    metrics = get_qkd_links_metrics(custom_links=custom)
    assert len(metrics) == 2
    assert metrics[0]["source_id"] == "alpha"
    assert metrics[0]["destination_id"] == "beta"
    assert metrics[1]["source_id"] == "gamma"
    assert metrics[1]["destination_id"] == "delta"


# -----------------------------------------------------------------------------
# Database Queries & Aggregation Tests
# -----------------------------------------------------------------------------


def test_empty_database_queries(test_db_session: Session) -> None:
    """Verifies queries handle empty database gracefully without raising exceptions."""
    repo = get_repository(session=test_db_session)
    assert isinstance(repo, SqliteTransferRepository)

    history = get_history_store(session=test_db_session)
    assert isinstance(history, SqliteTelemetryHistoryStore)

    assert get_active_transfers(session=test_db_session) == []
    assert get_queue_transfers(session=test_db_session) == []
    assert get_completed_transfers(session=test_db_session) == []
    assert get_all_transfers(session=test_db_session) == []
    assert get_transfer_observations(session=test_db_session) == []

    kpi = get_kpi_summary(session=test_db_session)
    assert kpi["active_transfers_count"] == 0
    assert kpi["queue_length"] == 0
    assert kpi["completed_count"] == 0
    assert kpi["failed_count"] == 0
    assert kpi["total_requests"] == 0
    assert kpi["is_simulated"] is True


def test_populated_database_queries(test_db_session: Session) -> None:
    """Verifies data queries accurately categorize requests by status and priority."""
    repo = SqliteTransferRepository(session=test_db_session)
    now = datetime.now(UTC)

    # 1. Active requests
    req_active_1 = _create_sample_request(status=TransferStatus.TRANSFERRING, started_at=now)
    req_active_2 = _create_sample_request(status=TransferStatus.VERIFYING, started_at=now)
    repo.create_request(req_active_1)
    repo.create_request(req_active_2)

    # 2. Queued requests with different priorities
    req_queued_normal = _create_sample_request(
        status=TransferStatus.QUEUED,
        priority=PriorityLevel.NORMAL,
        submitted_at=now - timedelta(minutes=5),
    )
    req_queued_critical = _create_sample_request(
        status=TransferStatus.SCHEDULED,
        priority=PriorityLevel.CRITICAL,
        submitted_at=now - timedelta(minutes=1),
    )
    repo.create_request(req_queued_normal)
    repo.create_request(req_queued_critical)

    # 3. Terminal requests
    req_completed = _create_sample_request(
        status=TransferStatus.COMPLETED,
        started_at=now - timedelta(seconds=10),
        completed_at=now - timedelta(seconds=2),
    )
    req_failed = _create_sample_request(status=TransferStatus.FAILED)
    repo.create_request(req_completed)
    repo.create_request(req_failed)

    # Verify active transfers
    active = get_active_transfers(session=test_db_session)
    assert len(active) == 2
    active_ids = {r.request_id for r in active}
    assert req_active_1.request_id in active_ids
    assert req_active_2.request_id in active_ids

    # Verify queue transfers order: CRITICAL should precede NORMAL
    queue = get_queue_transfers(session=test_db_session)
    assert len(queue) == 2
    assert queue[0].priority == PriorityLevel.CRITICAL
    assert queue[1].priority == PriorityLevel.NORMAL

    # Verify completed transfers
    completed = get_completed_transfers(session=test_db_session)
    assert len(completed) == 2
    terminal_statuses = {r.status for r in completed}
    assert TransferStatus.COMPLETED in terminal_statuses
    assert TransferStatus.FAILED in terminal_statuses

    # Verify all transfers
    all_reqs = get_all_transfers(session=test_db_session)
    assert len(all_reqs) == 6

    # Verify KPI summary
    kpi = get_kpi_summary(session=test_db_session)
    assert kpi["active_transfers_count"] == 2
    assert kpi["queue_length"] == 2
    assert kpi["completed_count"] == 1
    assert kpi["failed_count"] == 1
    assert kpi["total_requests"] == 6
    assert kpi["system_load_factor"] > 0.0


def test_transitions_and_policy_decisions_retrieval(test_db_session: Session) -> None:
    """Verifies retrieval of state transitions and policy decisions for a request."""
    repo = SqliteTransferRepository(session=test_db_session)
    req = _create_sample_request(status=TransferStatus.SUBMITTED)
    repo.create_request(req)

    # Record state transition via update_status
    repo.update_status(
        request_id=req.request_id,
        new_status=TransferStatus.VALIDATING,
        actor="TestHarness",
        reason="Initial policy evaluation",
    )

    # Record policy decision
    decision = PolicyDecision(
        request_id=req.request_id,
        allowed=True,
        reason_code="AUTH_SUCCESS",
        explanation="Authorized transfer route",
        required_security_profile=SecurityProfile.QKD_AES_256_GCM,
        evaluated_at=datetime.now(UTC),
    )
    repo.record_policy_decision(decision)

    transitions = get_request_transitions(req.request_id, session=test_db_session)
    assert len(transitions) == 1
    assert transitions[0].to_status == TransferStatus.VALIDATING

    decisions = get_request_policy_decisions(req.request_id, session=test_db_session)
    assert len(decisions) == 1
    assert decisions[0].reason_code == "AUTH_SUCCESS"
    assert decisions[0].allowed is True


def test_history_observations_retrieval(test_db_session: Session) -> None:
    """Verifies historical observations storage and query."""
    history = SqliteTelemetryHistoryStore(session=test_db_session)
    obs = TransferObservation(
        observation_id=uuid4(),
        request_id=uuid4(),
        source_id="univ-lab",
        destination_id="poly-clinic",
        data_size_bytes=5_242_880,
        priority_level=20,
        data_category="DIAGNOSTIC_REPORT",
        actual_duration_seconds=2.45,
        effective_throughput_mbps=17.1,
        success=True,
        observed_at=datetime.now(UTC),
    )
    history.record_observation(obs)

    observations = get_transfer_observations(session=test_db_session)
    assert len(observations) == 1
    assert observations[0].observation_id == obs.observation_id
    assert observations[0].effective_throughput_mbps == 17.1


# -----------------------------------------------------------------------------
# UI Component Headless Smoke Tests
# -----------------------------------------------------------------------------


def test_ui_components_smoke(test_db_session: Session) -> None:
    """Verifies UI cards and operations page render headlessly without raising exceptions."""
    kpi = get_kpi_summary(session=test_db_session)
    render_kpi_cards(kpi)
    render_secondary_kpis(kpi)
    render_operations_page()
