"""First-In-First-Out (FIFO) deterministic scheduling policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.enums import SchedulingPolicyName
from app.models.scheduling import SchedulingDecision
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.scheduler.base import IPredictor, IScheduler


class FIFOScheduler(IScheduler):
    """First-In-First-Out scheduler selecting the earliest submitted eligible request.

    Enforces strict FIFO ordering on `submitted_at` timestamps with UUID tie-breaking
    to guarantee 100% deterministic decision repeatability.
    """

    @property
    def policy_name(self) -> SchedulingPolicyName:
        return SchedulingPolicyName.FIFO

    def select_next(
        self,
        candidate_requests: list[TransferRequest],
        telemetry: TelemetrySnapshot,
        predictor: IPredictor | None = None,
    ) -> SchedulingDecision | None:
        """Selects the oldest eligible request by submitted_at timestamp.

        Args:
            candidate_requests: Pool of transfer requests in system queue.
            telemetry: Real-time telemetry snapshot of network and link conditions.
            predictor: Optional IPredictor (unused by naive FIFO).

        Returns:
            SchedulingDecision for the oldest request, or None if no eligible requests.
        """
        eligible = self.filter_eligible(candidate_requests)
        if not eligible:
            return None

        # Sort strictly by submitted_at ascending, using request_id string as deterministic tie-breaker
        sorted_requests = sorted(eligible, key=lambda r: (r.submitted_at, str(r.request_id)))
        selected = sorted_requests[0]

        now = datetime.now(UTC)
        starvation_age = max(0.0, (now - selected.submitted_at).total_seconds())

        # Determine estimated duration from telemetry throughput if available
        link_key = f"{selected.source_id}->{selected.destination_id}"
        net_telemetry = telemetry.network_metrics.get(link_key)
        throughput_mbps = (
            net_telemetry.observed_throughput_mbps
            if net_telemetry and net_telemetry.observed_throughput_mbps > 0.0
            else 100.0  # nominal 100 Mbps fallback baseline
        )

        estimated_duration_sec = (selected.data_size_bytes * 8.0) / (throughput_mbps * 1_000_000.0)
        predicted_completion = now + timedelta(seconds=estimated_duration_sec)

        # Estimate deadline miss risk
        deadline_miss_prob = 0.0
        if selected.deadline is not None:
            slack_sec = (selected.deadline - predicted_completion).total_seconds()
            deadline_miss_prob = 1.0 if slack_sec < 0.0 else 0.0

        return SchedulingDecision(
            decision_id=uuid4(),
            selected_request_id=selected.request_id,
            policy_name=self.policy_name,
            decision_reason=f"FIFO policy: Selected earliest submitted request at {selected.submitted_at.isoformat()}",
            estimated_start_time=now,
            predicted_completion_time=predicted_completion,
            predicted_deadline_miss_probability=deadline_miss_prob,
            evaluated_at=now,
            queue_length_at_decision=len(eligible),
            starvation_age_seconds=starvation_age,
        )


__all__ = ["FIFOScheduler"]
