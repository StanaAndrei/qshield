"""Integration tests for AI-assisted scheduling policy and simulation (TASK-4.6 / TASK-4.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.enums import (
    DataCategory,
    PriorityLevel,
    SchedulingPolicyName,
    SecurityProfile,
    TransferStatus,
)
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.predictor.baseline import AnalyticalPredictor
from app.scheduler.ai_assisted import AIAssistedScheduler
from simulation.engine import EngineConfig, SimulationEngine
from simulation.key_resource_model import KeyResourceConfig
from simulation.network_model import NetworkConfig
from simulation.workload_generator import WorkloadConfig


def _make_request(
    size_bytes: int,
    priority: PriorityLevel,
    slack_seconds: float | None = None,
    status: TransferStatus = TransferStatus.QUEUED,
    submitted_offset_s: float = 0.0,
) -> TransferRequest:
    now = datetime.now(UTC)
    deadline = now + timedelta(seconds=slack_seconds) if slack_seconds is not None else None
    return TransferRequest(
        request_id=uuid4(),
        source_id="univ-lab",
        destination_id="poly-clinic",
        data_size_bytes=size_bytes,
        priority=priority,
        submitted_at=now - timedelta(seconds=submitted_offset_s),
        deadline=deadline,
        security_profile=SecurityProfile.QKD_AES_256_GCM,
        data_category=DataCategory.DIAGNOSTIC_REPORT,
        status=status,
    )


class TestAIAssistedScheduling:
    def test_filter_eligible_strictly_enforced(self) -> None:
        """DoD: Deterministic security policy is strictly preserved."""
        scheduler = AIAssistedScheduler()
        telemetry = TelemetrySnapshot()

        queued_req = _make_request(10_000, PriorityLevel.NORMAL, status=TransferStatus.QUEUED)
        submitted_req = _make_request(10_000, PriorityLevel.CRITICAL, status=TransferStatus.SUBMITTED)
        completed_req = _make_request(10_000, PriorityLevel.CRITICAL, status=TransferStatus.COMPLETED)

        decision = scheduler.select_next([queued_req, submitted_req, completed_req], telemetry)

        assert decision is not None
        assert decision.selected_request_id == queued_req.request_id

    def test_priority_and_deadline_risk_ranking(self) -> None:
        """Scheduler favors candidate with high deadline satisfaction probability."""
        scheduler = AIAssistedScheduler(predictor=AnalyticalPredictor())
        telemetry = TelemetrySnapshot()

        # Request A: Huge 500 MB transfer (takes ~40s), deadline in 10s -> P(miss) = 1.0
        req_a = _make_request(500_000_000, PriorityLevel.NORMAL, slack_seconds=10.0)

        # Request B: Small 5 MB transfer (takes ~0.4s), deadline in 10s -> P(miss) ~ 0.0
        req_b = _make_request(5_000_000, PriorityLevel.NORMAL, slack_seconds=10.0)

        decision = scheduler.select_next([req_a, req_b], telemetry)

        assert decision is not None
        # Candidate B must be preferred due to high likelihood of meeting its deadline
        assert decision.selected_request_id == req_b.request_id
        assert decision.policy_name == SchedulingPolicyName.AI_ASSISTED

    def test_starvation_aging_progression(self) -> None:
        """Aged request gains utility and overtakes newly arrived candidate."""
        scheduler = AIAssistedScheduler(
            alpha_priority=1.0,
            delta_age=1.0,  # 1 unit per second of waiting
        )
        telemetry = TelemetrySnapshot()

        # Newly arrived HIGH request
        req_high_fresh = _make_request(10_000, PriorityLevel.HIGH, submitted_offset_s=0.0)

        # Older LOW request that has been starving for 60 seconds
        req_low_aged = _make_request(10_000, PriorityLevel.LOW, submitted_offset_s=60.0)

        decision = scheduler.select_next([req_high_fresh, req_low_aged], telemetry)

        assert decision is not None
        # LOW (10) + 60*1.0 = 70 > HIGH (30) + 0 = 30
        assert decision.selected_request_id == req_low_aged.request_id

    def test_simulation_engine_integration_ai_assisted(self) -> None:
        """Verify SimulationEngine runs cleanly with AI_ASSISTED policy."""
        wl_cfg = WorkloadConfig(
            total_requests=25,
            seed=42,
        )
        net_cfg = NetworkConfig(
            source_id="univ-lab",
            destination_id="poly-clinic",
            baseline_throughput_mbps=100.0,
            seed=42,
        )
        key_cfg = KeyResourceConfig(
            source_id="univ-lab",
            destination_id="poly-clinic",
            initial_keys=500,
            buffer_ceiling=1000,
        )

        eng_cfg = EngineConfig(
            policy_name="AI_ASSISTED",
            scenario_name="test_integration",
            seed=42,
            predictor=AnalyticalPredictor(),
        )

        engine = SimulationEngine(wl_cfg, net_cfg, key_cfg, eng_cfg)
        result = engine.run()

        assert result.total_requests == 25
        assert result.completed == 25
        assert result.failed == 0
        assert result.policy_name == "AI_ASSISTED"
        assert len(result.records) == 25
