"""Unit tests for PriorityScheduler (TASK-2.1 / TASK-2.5).

DoD: CRITICAL request submitted later takes precedence over an earlier NORMAL request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.enums import DataCategory, PriorityLevel, SecurityProfile, TransferStatus
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.scheduler.priority import PriorityScheduler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    priority: PriorityLevel = PriorityLevel.NORMAL,
    *,
    submitted_offset_seconds: float = 0.0,
    status: TransferStatus = TransferStatus.QUEUED,
    data_size_bytes: int = 5_242_880,
    deadline: datetime | None = None,
    source_id: str = "univ-lab",
    destination_id: str = "poly-clinic",
) -> TransferRequest:
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    return TransferRequest(
        request_id=uuid4(),
        source_id=source_id,
        destination_id=destination_id,
        data_size_bytes=data_size_bytes,
        priority=priority,
        submitted_at=base_time + timedelta(seconds=submitted_offset_seconds),
        deadline=deadline,
        security_profile=SecurityProfile.QKD_AES_256_GCM,
        data_category=DataCategory.DIAGNOSTIC_REPORT,
        status=status,
    )


def _empty_telemetry() -> TelemetrySnapshot:
    return TelemetrySnapshot(timestamp=datetime.now(UTC))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPrioritySchedulerBasics:
    def test_policy_name_is_priority(self) -> None:
        from app.core.enums import SchedulingPolicyName

        scheduler = PriorityScheduler()
        assert scheduler.policy_name == SchedulingPolicyName.PRIORITY

    def test_returns_none_for_empty_queue(self) -> None:
        scheduler = PriorityScheduler()
        result = scheduler.select_next([], _empty_telemetry())
        assert result is None

    def test_returns_none_when_all_non_queued(self) -> None:
        scheduler = PriorityScheduler()
        requests = [
            _make_request(status=TransferStatus.TRANSFERRING),
            _make_request(status=TransferStatus.COMPLETED),
        ]
        result = scheduler.select_next(requests, _empty_telemetry())
        assert result is None

    def test_single_queued_request_is_always_selected(self) -> None:
        scheduler = PriorityScheduler()
        req = _make_request(priority=PriorityLevel.LOW)
        result = scheduler.select_next([req], _empty_telemetry())
        assert result is not None
        assert result.selected_request_id == req.request_id


class TestPrioritySchedulerOrdering:
    def test_critical_beats_normal(self) -> None:
        """DoD: CRITICAL submitted later still beats NORMAL submitted earlier."""
        scheduler = PriorityScheduler()

        normal_req = _make_request(priority=PriorityLevel.NORMAL, submitted_offset_seconds=0)
        critical_req = _make_request(priority=PriorityLevel.CRITICAL, submitted_offset_seconds=10)

        result = scheduler.select_next([normal_req, critical_req], _empty_telemetry())
        assert result is not None
        assert result.selected_request_id == critical_req.request_id

    def test_critical_beats_high(self) -> None:
        scheduler = PriorityScheduler()
        high_req = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=0)
        critical_req = _make_request(priority=PriorityLevel.CRITICAL, submitted_offset_seconds=5)

        result = scheduler.select_next([high_req, critical_req], _empty_telemetry())
        assert result is not None
        assert result.selected_request_id == critical_req.request_id

    def test_high_beats_low(self) -> None:
        scheduler = PriorityScheduler()
        low_req = _make_request(priority=PriorityLevel.LOW, submitted_offset_seconds=0)
        high_req = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=100)

        result = scheduler.select_next([low_req, high_req], _empty_telemetry())
        assert result is not None
        assert result.selected_request_id == high_req.request_id

    def test_full_priority_ordering_critical_high_normal_low(self) -> None:
        scheduler = PriorityScheduler()
        requests = [
            _make_request(priority=PriorityLevel.LOW, submitted_offset_seconds=0),
            _make_request(priority=PriorityLevel.NORMAL, submitted_offset_seconds=1),
            _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=2),
            _make_request(priority=PriorityLevel.CRITICAL, submitted_offset_seconds=3),
        ]
        result = scheduler.select_next(requests, _empty_telemetry())
        assert result is not None
        # Highest priority (CRITICAL) must win regardless of submission order
        critical = next(r for r in requests if r.priority == PriorityLevel.CRITICAL)
        assert result.selected_request_id == critical.request_id


class TestPrioritySchedulerFIFOTiebreak:
    def test_equal_priority_uses_submission_time(self) -> None:
        """Among equal-priority requests the earliest submitted one must win."""
        scheduler = PriorityScheduler()

        early = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=0)
        late = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=60)

        result = scheduler.select_next([late, early], _empty_telemetry())
        assert result is not None
        assert result.selected_request_id == early.request_id

    def test_tiebreak_is_deterministic_across_calls(self) -> None:
        """Repeated calls on the same queue must return the same decision."""
        scheduler = PriorityScheduler()
        requests = [
            _make_request(priority=PriorityLevel.NORMAL, submitted_offset_seconds=0),
            _make_request(priority=PriorityLevel.NORMAL, submitted_offset_seconds=0),
        ]
        # Both have same submitted_at: UUID tiebreaker guarantees determinism
        result1 = scheduler.select_next(requests, _empty_telemetry())
        result2 = scheduler.select_next(requests, _empty_telemetry())
        assert result1 is not None
        assert result2 is not None
        assert result1.selected_request_id == result2.selected_request_id


class TestPrioritySchedulerDecisionFields:
    def test_decision_contains_correct_policy_name(self) -> None:
        from app.core.enums import SchedulingPolicyName

        scheduler = PriorityScheduler()
        req = _make_request()
        result = scheduler.select_next([req], _empty_telemetry())
        assert result is not None
        assert result.policy_name == SchedulingPolicyName.PRIORITY

    def test_decision_queue_length_is_eligible_count(self) -> None:
        scheduler = PriorityScheduler()
        queued = [_make_request() for _ in range(5)]
        non_queued = [_make_request(status=TransferStatus.COMPLETED)]
        result = scheduler.select_next(queued + non_queued, _empty_telemetry())
        assert result is not None
        assert result.queue_length_at_decision == 5

    def test_starvation_age_is_non_negative(self) -> None:
        scheduler = PriorityScheduler()
        req = _make_request(submitted_offset_seconds=-300)  # 5 minutes ago
        result = scheduler.select_next([req], _empty_telemetry())
        assert result is not None
        assert result.starvation_age_seconds >= 0.0

    def test_deadline_miss_probability_zero_for_fast_transfer(self) -> None:
        scheduler = PriorityScheduler()
        far_deadline = datetime.now(UTC) + timedelta(hours=24)
        req = _make_request(deadline=far_deadline, data_size_bytes=1_000)
        result = scheduler.select_next([req], _empty_telemetry())
        assert result is not None
        assert result.predicted_deadline_miss_probability == 0.0

    def test_deadline_miss_probability_one_for_expired_deadline(self) -> None:
        from app.models.telemetry import NetworkTelemetry

        scheduler = PriorityScheduler()
        # Tight deadline with huge payload → will miss
        past_deadline = datetime.now(UTC) + timedelta(seconds=1)
        huge_req = _make_request(deadline=past_deadline, data_size_bytes=100_000_000_000)
        telemetry = TelemetrySnapshot(
            timestamp=datetime.now(UTC),
            network_metrics={
                "univ-lab->poly-clinic": NetworkTelemetry(
                    source_id="univ-lab",
                    destination_id="poly-clinic",
                    observed_throughput_mbps=1.0,
                    packet_loss_rate=0.0,
                    round_trip_time_ms=0.0,
                )
            },
        )
        result = scheduler.select_next([huge_req], telemetry)
        assert result is not None
        assert result.predicted_deadline_miss_probability == 1.0

    def test_uses_telemetry_throughput_for_completion_estimate(self) -> None:
        from app.models.telemetry import NetworkTelemetry

        scheduler = PriorityScheduler()
        req = _make_request(data_size_bytes=800_000_000)  # 800 MB

        telemetry = TelemetrySnapshot(
            timestamp=datetime.now(UTC),
            network_metrics={
                "univ-lab->poly-clinic": NetworkTelemetry(
                    source_id="univ-lab",
                    destination_id="poly-clinic",
                    observed_throughput_mbps=800.0,
                    packet_loss_rate=0.0,
                    round_trip_time_ms=0.0,
                )
            },
        )
        result = scheduler.select_next([req], telemetry)
        assert result is not None
        expected_duration = (800_000_000 * 8.0) / (800.0 * 1_000_000.0)  # = 8.0 s
        actual_duration = (result.predicted_completion_time - result.estimated_start_time).total_seconds()
        assert abs(actual_duration - expected_duration) < 0.5


class TestPrioritySchedulerIgnoresNonQueued:
    def test_mixed_statuses_only_queued_eligible(self) -> None:
        scheduler = PriorityScheduler()
        statuses = [
            TransferStatus.SUBMITTED,
            TransferStatus.VALIDATING,
            TransferStatus.REJECTED,
            TransferStatus.SCHEDULED,
            TransferStatus.TRANSFERRING,
            TransferStatus.COMPLETED,
            TransferStatus.FAILED,
            TransferStatus.CANCELLED,
            TransferStatus.EXPIRED,
        ]
        requests = [_make_request(status=s) for s in statuses]
        result = scheduler.select_next(requests, _empty_telemetry())
        assert result is None

    def test_queued_among_mixed_statuses(self) -> None:
        scheduler = PriorityScheduler()
        queued_req = _make_request(priority=PriorityLevel.LOW, status=TransferStatus.QUEUED)
        non_queued = _make_request(priority=PriorityLevel.CRITICAL, status=TransferStatus.TRANSFERRING)
        result = scheduler.select_next([queued_req, non_queued], _empty_telemetry())
        assert result is not None
        assert result.selected_request_id == queued_req.request_id
