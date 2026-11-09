"""AI-Assisted Scheduling Policy for Q-SHIELD (TASK-4.6).

Ranks eligible transfer requests using machine learning or analytical estimates of
transfer duration and deadline-miss probability:
    utility = alpha * priority + beta * (1 - P(miss)) - gamma * duration_pred + delta * age

Gracefully falls back to the analytical baseline when an ML model is not supplied
or fails. Strictly preserves deterministic security and eligibility constraints.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.core.enums import SchedulingPolicyName
from app.models.scheduling import SchedulingDecision
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.predictor.baseline import AnalyticalPredictor
from app.predictor.features import extract_features
from app.scheduler.base import IPredictor, IScheduler

logger = logging.getLogger(__name__)

# Default utility weights
_DEFAULT_ALPHA_PRIORITY: float = 2.0
_DEFAULT_BETA_DEADLINE: float = 50.0
_DEFAULT_GAMMA_DURATION: float = 0.05
_DEFAULT_DELTA_AGE: float = 0.05


class AIAssistedScheduler(IScheduler):
    """Scheduling policy leveraging AI/analytical predictions for queue prioritization.

    Calculates candidate utility:
        utility = alpha * priority
                + beta * (1 - P(deadline_miss))
                - gamma * predicted_duration_seconds
                + delta * starvation_age_seconds
    """

    def __init__(
        self,
        predictor: IPredictor | None = None,
        alpha_priority: float = _DEFAULT_ALPHA_PRIORITY,
        beta_deadline: float = _DEFAULT_BETA_DEADLINE,
        gamma_duration: float = _DEFAULT_GAMMA_DURATION,
        delta_age: float = _DEFAULT_DELTA_AGE,
    ) -> None:
        self._predictor = predictor or AnalyticalPredictor()
        self._alpha = alpha_priority
        self._beta = beta_deadline
        self._gamma = gamma_duration
        self._delta = delta_age

    @property
    def policy_name(self) -> SchedulingPolicyName:
        return SchedulingPolicyName.AI_ASSISTED

    @property
    def predictor(self) -> IPredictor:
        return self._predictor

    def set_predictor(self, predictor: IPredictor) -> None:
        """Dynamically update the active predictor instance."""
        self._predictor = predictor

    def select_next(
        self,
        candidate_requests: list[TransferRequest],
        telemetry: TelemetrySnapshot,
        predictor: IPredictor | None = None,
    ) -> SchedulingDecision | None:
        """Select highest utility candidate from eligible requests."""
        eligible = self.filter_eligible(candidate_requests)
        if not eligible:
            return None

        active_pred = predictor or self._predictor
        now = datetime.now(UTC)

        best_request: TransferRequest | None = None
        best_utility = float("-inf")
        best_pred_duration = 0.0
        best_miss_prob = 0.0
        best_reason = ""

        # Score all eligible requests
        for req in eligible:
            # Respect timezone awareness on submitted_at
            sub_dt = req.submitted_at
            if sub_dt.tzinfo is None:
                sub_dt = sub_dt.replace(tzinfo=UTC)
            age = max(0.0, (now - sub_dt).total_seconds())

            # Feature extraction & prediction
            features = extract_features(req, telemetry, now=now)
            pred_out = active_pred.predict(features)
            dur = pred_out.predicted_duration_seconds
            miss_p = pred_out.deadline_miss_probability

            prio_val = (
                float(req.priority.value)
                if hasattr(req.priority, "value")
                else float(req.priority)
            )

            # Utility computation
            utility = (
                self._alpha * prio_val
                + self._beta * (1.0 - miss_p)
                - self._gamma * dur
                + self._delta * age
            )

            # Tie-breaking: higher utility, or if tied earlier submission
            if utility > best_utility:
                best_utility = utility
                best_request = req
                best_pred_duration = dur
                best_miss_prob = miss_p
                best_reason = (
                    f"AI_ASSISTED utility={utility:.2f} (prio={prio_val}, "
                    f"P(miss)={miss_p:.2f}, pred_dur={dur:.1f}s, age={age:.1f}s, "
                    f"model={pred_out.model_version})"
                )
            elif abs(utility - best_utility) < 1e-6 and best_request is not None:
                if sub_dt < best_request.submitted_at:
                    best_utility = utility
                    best_request = req
                    best_pred_duration = dur
                    best_miss_prob = miss_p
                    best_reason = (
                        f"AI_ASSISTED utility={utility:.2f} (tie-broken by submitted_at)"
                    )

        if best_request is None:
            return None

        sub_dt = best_request.submitted_at
        if sub_dt.tzinfo is None:
            sub_dt = sub_dt.replace(tzinfo=UTC)
        starvation_age = max(0.0, (now - sub_dt).total_seconds())
        pred_completion = now + timedelta(seconds=best_pred_duration)

        return SchedulingDecision(
            decision_id=uuid4(),
            selected_request_id=best_request.request_id,
            policy_name=self.policy_name,
            decision_reason=best_reason,
            estimated_start_time=now,
            predicted_completion_time=pred_completion,
            predicted_deadline_miss_probability=best_miss_prob,
            evaluated_at=now,
            queue_length_at_decision=len(eligible),
            starvation_age_seconds=starvation_age,
        )


__all__ = [
    "AIAssistedScheduler",
]
