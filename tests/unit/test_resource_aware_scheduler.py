"""Unit tests for ResourceAwareHeuristicScheduler (TASK-2.3 / TASK-2.5).

DoD: Starving LOW-priority request eventually out-scores a newly arrived
HIGH-priority request after configured aging threshold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.enums import DataCategory, PriorityLevel, SecurityProfile, TransferStatus
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.scheduler.resource_aware import ResourceAwareHeuristicScheduler

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


class TestResourceAwareHeuristicSchedulerBasics:
    def test_policy_name_is_resource_aware_heuristic(self) -> None:
        from app.core.enums import SchedulingPolicyName

        scheduler = ResourceAwareHeuristicScheduler()
        assert scheduler.policy_name == SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC

    def test_returns_none_for_empty_queue(self) -> None:
        scheduler = ResourceAwareHeuristicScheduler()
        result = scheduler.select_next([], _empty_telemetry())
        assert result is None

    def test_returns_none_when_all_non_queued(self) -> None:
        scheduler = ResourceAwareHeuristicScheduler()
        requests = [
            _make_request(status=TransferStatus.TRANSFERRING),
            _make_request(status=TransferStatus.COMPLETED),
        ]
        result = scheduler.select_next(requests, _empty_telemetry())
        assert result is None

    def test_single_request_always_selected(self) -> None:
        scheduler = ResourceAwareHeuristicScheduler()
        req = _make_request(priority=PriorityLevel.LOW)
        result = scheduler.select_next([req], _empty_telemetry())
        assert result is not None
        assert result.selected_request_id == req.request_id


class TestResourceAwareScoring:
    def test_critical_normally_beats_low_no_wait(self) -> None:
        """With zero wait time HIGH priority wins over LOW."""
        scheduler = ResourceAwareHeuristicScheduler(
            weight_priority=2.0,
            weight_slack=0.01,
            weight_aging=0.05,
            max_consecutive_high_priority=0,  # disable forced preemption
        )
        low_req = _make_request(priority=PriorityLevel.LOW, submitted_offset_seconds=0)
        high_req = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=1)
        result = scheduler.select_next([low_req, high_req], _empty_telemetry())
        assert result is not None
        assert result.selected_request_id == high_req.request_id

    def test_starvation_aging_causes_low_priority_to_win(self) -> None:
        """DoD: A starving LOW-priority request eventually out-scores a newly arrived HIGH.

        With large weight_aging and long wait time the LOW request's aging bonus
        should surpass the HIGH request's priority advantage.
        """
        # Score formula: w_p * priority - w_s * slack + w_a * wait
        #
        # Choose weights so that LOW + long wait beats HIGH + 0 wait:
        #   HIGH score  = 2.0 * 30 - 0 + 0.1 * 0        = 60.0
        #   LOW  score  = 2.0 * 10 - 0 + 0.1 * wait_sec = 20 + 0.1 * wait_sec
        #
        # LOW wins when 20 + 0.1 * wait > 60 → wait > 400 s
        scheduler = ResourceAwareHeuristicScheduler(
            weight_priority=2.0,
            weight_slack=0.0,     # ignore deadlines for this test
            weight_aging=0.1,
            max_consecutive_high_priority=0,  # disable forced preemption
            aging_threshold_seconds=0.0,
        )

        # LOW request has been waiting for 500 seconds → score = 20 + 0.1*500 = 70
        low_req = _make_request(priority=PriorityLevel.LOW, submitted_offset_seconds=-500)
        # HIGH request just arrived → score = 60 + 0 = 60
        high_req = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=0)

        result = scheduler.select_next([low_req, high_req], _empty_telemetry())
        assert result is not None
        assert result.selected_request_id == low_req.request_id, (
            "Starving LOW-priority request must out-score newly arrived HIGH after sufficient aging"
        )

    def test_tighter_deadline_increases_score(self) -> None:
        """Lower slack_seconds should raise the score (via -w_s * slack)."""
        scheduler = ResourceAwareHeuristicScheduler(
            weight_priority=1.0,
            weight_slack=1.0,
            weight_aging=0.0,
            max_consecutive_high_priority=0,
        )
        # Both NORMAL priority; req_a has tighter deadline (less slack)
        now = datetime.now(UTC)
        req_tight = _make_request(priority=PriorityLevel.NORMAL, deadline=now + timedelta(seconds=10))
        req_loose = _make_request(priority=PriorityLevel.NORMAL, deadline=now + timedelta(hours=10))

        result = scheduler.select_next([req_tight, req_loose], _empty_telemetry())
        assert result is not None
        assert result.selected_request_id == req_tight.request_id

    def test_no_deadline_uses_sentinel(self) -> None:
        """Requests with no deadline should use a large sentinel slack and not crash."""
        scheduler = ResourceAwareHeuristicScheduler(
            no_deadline_sentinel_seconds=86_400.0,
        )
        req = _make_request(deadline=None)
        result = scheduler.select_next([req], _empty_telemetry())
        assert result is not None


class TestStarvationForcedPreemption:
    def test_forced_preemption_after_max_consecutive_high(self) -> None:
        """After max_consecutive_high_priority cycles, the most-aged request must be picked."""
        scheduler = ResourceAwareHeuristicScheduler(
            weight_priority=100.0,   # strongly favour HIGH
            weight_slack=0.0,
            weight_aging=0.0,        # no organic aging bonus
            max_consecutive_high_priority=2,
        )
        # LOW request has been waiting a long time
        old_low = _make_request(priority=PriorityLevel.LOW, submitted_offset_seconds=-10_000)
        high1 = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=0)
        high2 = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=1)
        high3 = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=2)

        all_requests = [old_low, high1, high2, high3]

        # First two calls should pick HIGH requests
        result1 = scheduler.select_next(all_requests, _empty_telemetry())
        assert result1 is not None
        assert result1.selected_request_id != old_low.request_id

        result2 = scheduler.select_next(all_requests, _empty_telemetry())
        assert result2 is not None
        assert result2.selected_request_id != old_low.request_id

        # Third call must trigger forced age-based preemption → picks old_low
        result3 = scheduler.select_next(all_requests, _empty_telemetry())
        assert result3 is not None
        assert result3.selected_request_id == old_low.request_id, (
            "Forced preemption must select the most-aged request after max consecutive HIGH picks"
        )

    def test_counter_resets_after_non_high_selection(self) -> None:
        """Consecutive counter resets when a non-HIGH request is selected."""
        scheduler = ResourceAwareHeuristicScheduler(
            weight_priority=1.0,
            weight_slack=0.0,
            weight_aging=100.0,  # strong aging so LOW wins when it has waited
            max_consecutive_high_priority=5,
            aging_threshold_seconds=0,
        )
        old_low = _make_request(priority=PriorityLevel.LOW, submitted_offset_seconds=-10_000)
        high_req = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=0)

        # Trigger a non-high selection (old_low should win due to aging)
        result = scheduler.select_next([old_low, high_req], _empty_telemetry())
        assert result is not None
        # Verify counter was reset (counter is internal but we can observe no crash)
        assert scheduler._consecutive_high_priority_count == 0

    def test_no_preemption_when_cap_zero(self) -> None:
        """Setting max_consecutive_high_priority=0 must disable forced preemption."""
        scheduler = ResourceAwareHeuristicScheduler(
            weight_priority=100.0,
            weight_slack=0.0,
            weight_aging=0.0,
            max_consecutive_high_priority=0,  # disabled
        )
        old_low = _make_request(priority=PriorityLevel.LOW, submitted_offset_seconds=-100_000)
        high_req = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=0)

        # Even after many calls the HIGH request should still win (no forced preemption)
        for _ in range(20):
            result = scheduler.select_next([old_low, high_req], _empty_telemetry())
            assert result is not None
            assert result.selected_request_id == high_req.request_id


class TestResourceAwareDecisionFields:
    def test_decision_contains_correct_policy_name(self) -> None:
        from app.core.enums import SchedulingPolicyName

        scheduler = ResourceAwareHeuristicScheduler()
        req = _make_request()
        result = scheduler.select_next([req], _empty_telemetry())
        assert result is not None
        assert result.policy_name == SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC

    def test_queue_length_matches_eligible(self) -> None:
        scheduler = ResourceAwareHeuristicScheduler()
        queued = [_make_request() for _ in range(4)]
        non_queued = [_make_request(status=TransferStatus.COMPLETED)]
        result = scheduler.select_next(queued + non_queued, _empty_telemetry())
        assert result is not None
        assert result.queue_length_at_decision == 4

    def test_starvation_age_reflects_wait_time(self) -> None:
        scheduler = ResourceAwareHeuristicScheduler()
        # Request submitted 300 seconds ago
        req = _make_request(submitted_offset_seconds=-300)
        result = scheduler.select_next([req], _empty_telemetry())
        assert result is not None
        # Starvation age should be ~300s (allow 10s clock tolerance)
        assert result.starvation_age_seconds >= 290.0

    def test_uses_telemetry_throughput(self) -> None:
        from app.models.telemetry import NetworkTelemetry

        scheduler = ResourceAwareHeuristicScheduler()
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
        expected_duration = (800_000_000 * 8.0) / (800.0 * 1_000_000.0)
        actual = (result.predicted_completion_time - result.estimated_start_time).total_seconds()
        assert abs(actual - expected_duration) < 1.0


class TestResourceAwareConfigLoading:
    def test_weights_set_via_constructor_take_precedence(self) -> None:
        """Constructor kwargs must override YAML config values."""
        scheduler = ResourceAwareHeuristicScheduler(
            weight_priority=99.0,
            weight_slack=0.0,
            weight_aging=0.0,
        )
        assert scheduler._w_priority == 99.0
        assert scheduler._w_slack == 0.0
        assert scheduler._w_aging == 0.0

    def test_default_construction_does_not_raise(self) -> None:
        """Default construction (reading YAML or using defaults) must not raise."""
        scheduler = ResourceAwareHeuristicScheduler()
        assert scheduler._w_priority > 0
        assert scheduler._w_aging >= 0
        assert scheduler._w_slack >= 0
