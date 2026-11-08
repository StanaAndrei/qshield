"""Priority-based scheduling policy for Q-SHIELD.

Sorts eligible requests by priority (CRITICAL > HIGH > NORMAL > LOW) with
deterministic FIFO tie-breaking on submitted_at to guarantee repeatability.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.enums import SchedulingPolicyName
from app.models.scheduling import SchedulingDecision
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.scheduler.base import IPredictor, IScheduler


class PriorityScheduler(IScheduler):
    """Priority-based scheduler that selects the highest-priority eligible request.

    Sorting key: (priority DESC, submitted_at ASC, request_id ASC).
    - Requests with higher ``PriorityLevel`` integer values are served first.
    - Among equal-priority candidates the oldest submitted request wins (FIFO tiebreak).
    - request_id string provides a final deterministic tiebreaker so results are
      reproducible regardless of insertion order.

    DoD: CRITICAL request submitted later takes precedence over an earlier NORMAL request.
    """

    @property
    def policy_name(self) -> SchedulingPolicyName:
        return SchedulingPolicyName.PRIORITY

    def select_next(
        self,
        candidate_requests: list[TransferRequest],
        telemetry: TelemetrySnapshot,
        predictor: IPredictor | None = None,
    ) -> SchedulingDecision | None:
        """Selects the highest-priority eligible request with FIFO tiebreaking.

        Args:
            candidate_requests: Pool of transfer requests in system queue.
            telemetry: Real-time telemetry snapshot of network and link conditions.
            predictor: Optional IPredictor (unused by deterministic priority policy).

        Returns:
            SchedulingDecision for the highest-priority request, or None if the
            eligible pool is empty.
        """
        eligible = self.filter_eligible(candidate_requests)
        if not eligible:
            return None

        # Primary key: priority value DESCENDING (higher int = higher priority).
        # Secondary key: submitted_at ASCENDING (earlier submission wins tie).
        # Tertiary key: request_id string ASCENDING (fully deterministic tiebreaker).
        sorted_requests = sorted(
            eligible,
            key=lambda r: (-r.priority.value, r.submitted_at, str(r.request_id)),
        )
        selected = sorted_requests[0]

        now = datetime.now(UTC)
        starvation_age = max(0.0, (now - selected.submitted_at).total_seconds())

        # Estimate transfer duration from telemetry throughput
        link_key = f"{selected.source_id}->{selected.destination_id}"
        net_telemetry = telemetry.network_metrics.get(link_key)
        throughput_mbps = (
            net_telemetry.observed_throughput_mbps
            if net_telemetry and net_telemetry.observed_throughput_mbps > 0.0
            else 100.0  # nominal 100 Mbps fallback baseline
        )

        estimated_duration_sec = (selected.data_size_bytes * 8.0) / (throughput_mbps * 1_000_000.0)
        predicted_completion = now + timedelta(seconds=estimated_duration_sec)

        # Deadline miss probability
        deadline_miss_prob = 0.0
        if selected.deadline is not None:
            slack_sec = (selected.deadline - predicted_completion).total_seconds()
            deadline_miss_prob = 1.0 if slack_sec < 0.0 else 0.0

        reason = (
            f"PRIORITY policy: Selected {selected.priority.name} request "
            f"(submitted {selected.submitted_at.isoformat()}) "
            f"over {len(eligible) - 1} lower-priority candidate(s)"
        )

        return SchedulingDecision(
            decision_id=uuid4(),
            selected_request_id=selected.request_id,
            policy_name=self.policy_name,
            decision_reason=reason,
            estimated_start_time=now,
            predicted_completion_time=predicted_completion,
            predicted_deadline_miss_probability=deadline_miss_prob,
            evaluated_at=now,
            queue_length_at_decision=len(eligible),
            starvation_age_seconds=starvation_age,
        )


__all__ = ["PriorityScheduler"]
