"""Unit tests for FIFOScheduler implementation (TASK-1.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.enums import (
    DataCategory,
    PriorityLevel,
    SchedulingPolicyName,
    SecurityProfile,
    TransferStatus,
)
from app.models.telemetry import NetworkTelemetry, TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.scheduler.fifo import FIFOScheduler


@pytest.fixture
def fifo_scheduler() -> FIFOScheduler:
    return FIFOScheduler()


@pytest.fixture
def mock_telemetry() -> TelemetrySnapshot:
    return TelemetrySnapshot(
        network_metrics={
            "univ-lab->poly-clinic": NetworkTelemetry(
                source_id="univ-lab",
                destination_id="poly-clinic",
                observed_throughput_mbps=100.0,
                packet_loss_rate=0.0001,
                round_trip_time_ms=10.0,
            )
        }
    )


def _create_queued_request(
    submitted_at: datetime,
    source_id: str = "univ-lab",
    destination_id: str = "poly-clinic",
    priority: PriorityLevel = PriorityLevel.NORMAL,
    data_size_bytes: int = 1_048_576,
    status: TransferStatus = TransferStatus.QUEUED,
) -> TransferRequest:
    return TransferRequest(
        request_id=uuid4(),
        source_id=source_id,
        destination_id=destination_id,
        data_size_bytes=data_size_bytes,
        priority=priority,
        submitted_at=submitted_at,
        security_profile=SecurityProfile.QKD_AES_256_GCM,
        data_category=DataCategory.DIAGNOSTIC_REPORT,
        status=status,
    )


def test_fifo_ordering_dod_t1_t2_t3(
    fifo_scheduler: FIFOScheduler,
    mock_telemetry: TelemetrySnapshot,
) -> None:
    """Verifies TASK-1.5 DoD: Candidate requests submitted at t1, t2, t3 are always selected in submission order."""
    base_time = datetime(2026, 11, 7, 10, 0, 0, tzinfo=UTC)
    t1 = base_time
    t2 = base_time + timedelta(seconds=10)
    t3 = base_time + timedelta(seconds=20)

    req1 = _create_queued_request(t1, priority=PriorityLevel.LOW)       # Older, but LOW priority
    req2 = _create_queued_request(t2, priority=PriorityLevel.HIGH)      # Newer, HIGH priority
    req3 = _create_queued_request(t3, priority=PriorityLevel.CRITICAL)  # Newest, CRITICAL priority

    # Pass in shuffled order [req3, req1, req2]
    candidates = [req3, req1, req2]

    # Pick 1 -> MUST be req1 (t1)
    d1 = fifo_scheduler.select_next(candidates, mock_telemetry)
    assert d1 is not None
    assert d1.selected_request_id == req1.request_id
    assert d1.policy_name == SchedulingPolicyName.FIFO

    # Remove req1, pick 2 -> MUST be req2 (t2)
    candidates.remove(req1)
    d2 = fifo_scheduler.select_next(candidates, mock_telemetry)
    assert d2 is not None
    assert d2.selected_request_id == req2.request_id

    # Remove req2, pick 3 -> MUST be req3 (t3)
    candidates.remove(req2)
    d3 = fifo_scheduler.select_next(candidates, mock_telemetry)
    assert d3 is not None
    assert d3.selected_request_id == req3.request_id

    # Empty queue returns None
    candidates.remove(req3)
    d_empty = fifo_scheduler.select_next(candidates, mock_telemetry)
    assert d_empty is None


def test_fifo_ignores_non_queued_requests(
    fifo_scheduler: FIFOScheduler,
    mock_telemetry: TelemetrySnapshot,
) -> None:
    """Verifies that requests in states other than QUEUED (e.g. SUBMITTED, COMPLETED) are not selected."""
    now = datetime.now(UTC)
    r_sub = _create_queued_request(now - timedelta(seconds=30), status=TransferStatus.SUBMITTED)
    r_comp = _create_queued_request(now - timedelta(seconds=20), status=TransferStatus.COMPLETED)
    r_queued = _create_queued_request(now - timedelta(seconds=10), status=TransferStatus.QUEUED)

    candidates = [r_sub, r_comp, r_queued]
    decision = fifo_scheduler.select_next(candidates, mock_telemetry)

    assert decision is not None
    assert decision.selected_request_id == r_queued.request_id
    assert decision.queue_length_at_decision == 1


def test_fifo_deterministic_uuid_tie_breaking(
    fifo_scheduler: FIFOScheduler,
    mock_telemetry: TelemetrySnapshot,
) -> None:
    """Verifies deterministic tie-breaking by UUID when submitted_at timestamps are identical."""
    same_time = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)
    reqs = [_create_queued_request(same_time) for _ in range(5)]

    # Expected winner is the one with min(str(request_id))
    expected_winner = min(reqs, key=lambda r: str(r.request_id))

    decision = fifo_scheduler.select_next(reqs, mock_telemetry)
    assert decision is not None
    assert decision.selected_request_id == expected_winner.request_id
