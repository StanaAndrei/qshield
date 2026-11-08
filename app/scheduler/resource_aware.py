"""Resource-Aware Heuristic Scheduler with starvation aging for Q-SHIELD.

Implements a composite scoring function that balances priority, deadline urgency,
and starvation aging so that LOW-priority requests cannot be indefinitely deferred
by a continuous stream of higher-priority arrivals.

Scoring function (from TASK-2.3 spec):
    score = w_p * priority_value
            - w_s * slack_seconds
            + w_a * wait_time_seconds

Additionally enforces a configurable ``max_consecutive_high_priority`` cap after
which the most-aged low-priority request is force-promoted regardless of its score.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import yaml

from app.core.enums import PriorityLevel, SchedulingPolicyName
from app.models.scheduling import SchedulingDecision
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.scheduler.base import IPredictor, IScheduler

logger = logging.getLogger(__name__)

# Default weight / policy values (overridden by configs/scheduling.yaml)
_DEFAULT_W_PRIORITY: float = 2.0
_DEFAULT_W_SLACK: float = 0.01
_DEFAULT_W_AGING: float = 0.05
_DEFAULT_MAX_CONSECUTIVE_HIGH: int = 5
_DEFAULT_NO_DEADLINE_SENTINEL: float = 86_400.0
_DEFAULT_AGING_THRESHOLD: float = 0.0

_CONFIG_PATH = Path(__file__).parent.parent.parent / "configs" / "scheduling.yaml"


def _load_config() -> dict[str, Any]:
    """Load heuristic scheduler config from YAML, return empty dict on failure."""
    try:
        if _CONFIG_PATH.exists():
            with _CONFIG_PATH.open() as fh:
                raw = yaml.safe_load(fh)
                if isinstance(raw, dict):
                    section = raw.get("heuristic_scheduler")
                    if isinstance(section, dict):
                        return cast(dict[str, Any], section)
    except Exception:  # pragma: no cover
        logger.warning("Failed to load scheduling.yaml; using defaults.", exc_info=True)
    return {}


class ResourceAwareHeuristicScheduler(IScheduler):
    """Composite heuristic scheduler balancing priority, deadline slack, and aging.

    The scheduler computes a *score* for each eligible request and selects the
    candidate with the **highest** score:

        score = w_priority * priority_value
                - w_slack    * slack_seconds          (lower slack → higher urgency)
                + w_aging    * wait_time_seconds       (older requests gain bonus)

    Starvation protection:
        After ``max_consecutive_high_priority`` consecutive selections of requests
        at HIGH or CRITICAL priority level the scheduler switches to age-first
        ordering for one cycle, ensuring that lower-priority requests eventually
        receive service.

    Parameters
    ----------
    weight_priority:
        Multiplier on the integer priority value (CRITICAL=40 … LOW=10).
    weight_slack:
        Penalty per remaining second until deadline. Set to 0.0 to ignore deadlines.
    weight_aging:
        Bonus per second of queue wait time. Must be > 0 to enable starvation aging.
    max_consecutive_high_priority:
        Maximum consecutive HIGH/CRITICAL picks before forced aging preemption.
        0 disables the cap.
    no_deadline_sentinel_seconds:
        Slack substituted when a request carries no deadline.
    aging_threshold_seconds:
        Minimum wait before aging bonus applies (default 0 → always applies).
    """

    def __init__(
        self,
        weight_priority: float | None = None,
        weight_slack: float | None = None,
        weight_aging: float | None = None,
        max_consecutive_high_priority: int | None = None,
        no_deadline_sentinel_seconds: float | None = None,
        aging_threshold_seconds: float | None = None,
    ) -> None:
        cfg = _load_config()

        self._w_priority = weight_priority if weight_priority is not None else float(
            cfg.get("weight_priority", _DEFAULT_W_PRIORITY)
        )
        self._w_slack = weight_slack if weight_slack is not None else float(
            cfg.get("weight_slack", _DEFAULT_W_SLACK)
        )
        self._w_aging = weight_aging if weight_aging is not None else float(
            cfg.get("weight_aging", _DEFAULT_W_AGING)
        )
        self._max_consecutive_high = (
            max_consecutive_high_priority
            if max_consecutive_high_priority is not None
            else int(cfg.get("max_consecutive_high_priority", _DEFAULT_MAX_CONSECUTIVE_HIGH))
        )
        self._no_deadline_sentinel = (
            no_deadline_sentinel_seconds
            if no_deadline_sentinel_seconds is not None
            else float(cfg.get("no_deadline_sentinel_seconds", _DEFAULT_NO_DEADLINE_SENTINEL))
        )
        self._aging_threshold = (
            aging_threshold_seconds
            if aging_threshold_seconds is not None
            else float(cfg.get("aging_threshold_seconds", _DEFAULT_AGING_THRESHOLD))
        )

        # Mutable state tracking consecutive high-priority selections
        self._consecutive_high_priority_count: int = 0

    # ------------------------------------------------------------------
    # IScheduler interface
    # ------------------------------------------------------------------

    @property
    def policy_name(self) -> SchedulingPolicyName:
        return SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC

    def select_next(
        self,
        candidate_requests: list[TransferRequest],
        telemetry: TelemetrySnapshot,
        predictor: IPredictor | None = None,
    ) -> SchedulingDecision | None:
        """Select the next eligible request using the composite heuristic score.

        Args:
            candidate_requests: Pool of transfer requests in system queue.
            telemetry: Real-time telemetry snapshot of network and link conditions.
            predictor: Optional IPredictor (unused by this policy).

        Returns:
            SchedulingDecision for the best-scored request, or None if the
            eligible pool is empty.
        """
        eligible = self.filter_eligible(candidate_requests)
        if not eligible:
            return None

        now = datetime.now(UTC)

        # Compute score for every candidate
        scored = [(req, self._compute_score(req, now)) for req in eligible]

        # --- Starvation preemption ---
        # If we have been picking HIGH/CRITICAL too many times in a row, force-select
        # the most-aged (longest-waiting) candidate irrespective of score.
        force_age_selection = (
            self._max_consecutive_high > 0
            and self._consecutive_high_priority_count >= self._max_consecutive_high
        )

        if force_age_selection:
            # Choose the request with the largest wait time
            selected = max(eligible, key=lambda r: (now - r.submitted_at).total_seconds())
            selection_reason = (
                f"RESOURCE_AWARE_HEURISTIC: Forced age-based preemption after "
                f"{self._consecutive_high_priority_count} consecutive HIGH/CRITICAL picks. "
                f"Selected oldest waiting request ({selected.priority.name})."
            )
            self._consecutive_high_priority_count = 0
        else:
            # Normal path: highest composite score wins; ties broken by submitted_at then UUID
            selected, best_score = max(
                scored,
                key=lambda item: (item[1], -item[0].submitted_at.timestamp(), str(item[0].request_id)),
            )
            selection_reason = (
                f"RESOURCE_AWARE_HEURISTIC: Selected {selected.priority.name} request "
                f"(score={best_score:.4f}, waited={self._wait_seconds(selected, now):.1f}s, "
                f"slack={self._slack_seconds(selected, now):.1f}s)"
            )
            # Track consecutive high-priority selections
            if selected.priority in (PriorityLevel.HIGH, PriorityLevel.CRITICAL):
                self._consecutive_high_priority_count += 1
            else:
                self._consecutive_high_priority_count = 0

        # --- Build SchedulingDecision ---
        starvation_age = max(0.0, (now - selected.submitted_at).total_seconds())

        link_key = f"{selected.source_id}->{selected.destination_id}"
        net_telemetry = telemetry.network_metrics.get(link_key)
        throughput_mbps = (
            net_telemetry.observed_throughput_mbps
            if net_telemetry and net_telemetry.observed_throughput_mbps > 0.0
            else 100.0
        )

        estimated_duration_sec = (selected.data_size_bytes * 8.0) / (throughput_mbps * 1_000_000.0)
        predicted_completion = now + timedelta(seconds=estimated_duration_sec)

        deadline_miss_prob = 0.0
        if selected.deadline is not None:
            slack_sec = (selected.deadline - predicted_completion).total_seconds()
            deadline_miss_prob = 1.0 if slack_sec < 0.0 else 0.0

        return SchedulingDecision(
            decision_id=uuid4(),
            selected_request_id=selected.request_id,
            policy_name=self.policy_name,
            decision_reason=selection_reason,
            estimated_start_time=now,
            predicted_completion_time=predicted_completion,
            predicted_deadline_miss_probability=deadline_miss_prob,
            evaluated_at=now,
            queue_length_at_decision=len(eligible),
            starvation_age_seconds=starvation_age,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_score(self, request: TransferRequest, now: datetime) -> float:
        """Compute composite heuristic score for *request* at time *now*.

        score = w_priority * priority_value
                - w_slack    * slack_seconds
                + w_aging    * max(0, wait_seconds - aging_threshold)
        """
        priority_value = float(request.priority.value)
        slack_sec = self._slack_seconds(request, now)
        wait_sec = self._wait_seconds(request, now)
        aging_bonus_sec = max(0.0, wait_sec - self._aging_threshold)

        return (
            self._w_priority * priority_value
            - self._w_slack * slack_sec
            + self._w_aging * aging_bonus_sec
        )

    def _slack_seconds(self, request: TransferRequest, now: datetime) -> float:
        """Return seconds of remaining slack (sentinel when no deadline)."""
        if request.deadline is None:
            return self._no_deadline_sentinel
        return max(0.0, (request.deadline - now).total_seconds())

    @staticmethod
    def _wait_seconds(request: TransferRequest, now: datetime) -> float:
        """Return seconds the request has been waiting in queue."""
        return max(0.0, (now - request.submitted_at).total_seconds())


__all__ = ["ResourceAwareHeuristicScheduler"]
