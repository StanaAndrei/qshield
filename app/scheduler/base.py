"""Abstract interface contracts for schedulers and performance predictors."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.enums import SchedulingPolicyName, TransferStatus
from app.models.scheduling import (
    PredictionFeatures,
    PredictionOutput,
    SchedulingDecision,
)
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest


class IPredictor(ABC):
    """Abstract predictor interface for transfer duration and deadline risk estimation."""

    @abstractmethod
    def predict(self, features: PredictionFeatures) -> PredictionOutput:
        """Computes predicted transfer duration and deadline-miss risk."""
        pass

    @abstractmethod
    def get_model_version(self) -> str:
        """Returns the semantic version tag of the active prediction model."""
        pass


class IScheduler(ABC):
    """Abstract interface for all transfer scheduling policies (FIFO, Priority, Heuristic, AI)."""

    @property
    @abstractmethod
    def policy_name(self) -> SchedulingPolicyName:
        """Unique identifier name for this scheduling policy."""
        pass

    def filter_eligible(self, candidate_requests: list[TransferRequest]) -> list[TransferRequest]:
        """Filters candidate requests to those in QUEUED lifecycle status eligible for dispatch."""
        return [r for r in candidate_requests if r.status == TransferStatus.QUEUED]

    @abstractmethod
    def select_next(
        self,
        candidate_requests: list[TransferRequest],
        telemetry: TelemetrySnapshot,
        predictor: IPredictor | None = None,
    ) -> SchedulingDecision | None:
        """Selects the next eligible request to execute according to policy rules.

        Args:
            candidate_requests: List of candidate requests in queue.
            telemetry: Real-time telemetry snapshot of network and QKD links.
            predictor: Optional performance predictor for estimation.

        Returns:
            Structured SchedulingDecision if an eligible request exists, otherwise None.
        """
        pass


__all__ = [
    "IPredictor",
    "IScheduler",
]
