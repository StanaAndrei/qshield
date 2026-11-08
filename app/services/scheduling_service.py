"""Scheduling dispatch service for Q-SHIELD.

Coordinates queue inspection, policy selection, scheduling execution,
decision persistence, and transfer execution triggering.

This service is the central orchestration point for the scheduling loop:
  1. Retrieve eligible QUEUED requests from the repository.
  2. Obtain a real-time telemetry snapshot.
  3. Delegate to the active IScheduler policy to pick the next request.
  4. Record the SchedulingDecision to persistent storage.
  5. Transition the selected request to SCHEDULED status.
  6. Optionally trigger the transfer executor.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.core.enums import AuditEventType, SchedulingPolicyName, TransferStatus
from app.database.repository import ITransferRepository
from app.models.policy_decision import AuditEvent
from app.models.scheduling import SchedulingDecision
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.scheduler.base import IPredictor, IScheduler
from app.scheduler.fifo import FIFOScheduler
from app.scheduler.priority import PriorityScheduler
from app.scheduler.resource_aware import ResourceAwareHeuristicScheduler

logger = logging.getLogger(__name__)


def _build_default_telemetry() -> TelemetrySnapshot:
    """Return a minimal telemetry snapshot used when no collector is provided."""
    return TelemetrySnapshot(
        timestamp=datetime.now(UTC),
        active_transfers_count=0,
        queue_length=0,
        system_load_factor=0.0,
    )


class SchedulingService:
    """Orchestrates policy selection, scheduling decisions, and status transitions.

    Parameters
    ----------
    repository:
        Repository used for reading the queue and persisting decisions/transitions.
    policy:
        Active scheduling policy name (FIFO, PRIORITY, RESOURCE_AWARE_HEURISTIC).
        Defaults to FIFO.
    predictor:
        Optional IPredictor injected for policies that use duration/risk estimates.
    telemetry_provider:
        Callable returning a fresh TelemetrySnapshot.  When omitted a minimal
        empty snapshot is used (safe for unit tests).
    scheduler_overrides:
        Dict of kwargs forwarded to ResourceAwareHeuristicScheduler constructor.
        Ignored for other policies.
    """

    def __init__(
        self,
        repository: ITransferRepository,
        policy: SchedulingPolicyName = SchedulingPolicyName.FIFO,
        predictor: IPredictor | None = None,
        telemetry_provider: Callable[[], TelemetrySnapshot] | None = None,
        scheduler_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._repository = repository
        self._policy_name = policy
        self._predictor = predictor
        self._telemetry_provider = telemetry_provider
        self._scheduler: IScheduler = self._build_scheduler(policy, scheduler_overrides or {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def active_policy(self) -> SchedulingPolicyName:
        """Name of the currently active scheduling policy."""
        return self._policy_name

    def switch_policy(
        self,
        new_policy: SchedulingPolicyName,
        scheduler_overrides: dict[str, Any] | None = None,
    ) -> None:
        """Swap the active scheduling policy at runtime.

        Args:
            new_policy: Target policy name.
            scheduler_overrides: Optional kwargs for ResourceAwareHeuristicScheduler.
        """
        logger.info("Switching scheduling policy: %s → %s", self._policy_name, new_policy)
        self._policy_name = new_policy
        self._scheduler = self._build_scheduler(new_policy, scheduler_overrides or {})

    def run_scheduling_cycle(self) -> SchedulingDecision | None:
        """Execute a single scheduling cycle.

        Steps:
          1. Fetch current QUEUED requests from repository.
          2. Collect telemetry snapshot.
          3. Run scheduler to select next request.
          4. Record SchedulingDecision to repository.
          5. Transition selected request to SCHEDULED.
          6. Emit audit event.

        Returns:
            The SchedulingDecision if a request was selected, else None.
        """
        start_ns = time.perf_counter_ns()

        # 1. Fetch queued candidates
        queued = self._repository.list_requests(status=TransferStatus.QUEUED)
        if not queued:
            logger.debug("No QUEUED requests available; scheduling cycle idle.")
            return None

        # 2. Get telemetry
        telemetry = self._get_telemetry()

        # 3. Select next request
        decision = self._scheduler.select_next(
            candidate_requests=queued,
            telemetry=telemetry,
            predictor=self._predictor,
        )

        if decision is None:
            logger.debug("Scheduler returned no decision; all candidates ineligible.")
            return None

        elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        # 4. Persist the scheduling decision
        self._repository.record_scheduling_decision(decision)
        logger.info(
            "Scheduling decision recorded | policy=%s | request=%s | "
            "queue_len=%d | duration_ms=%.2f | reason=%s",
            self._policy_name,
            decision.selected_request_id,
            decision.queue_length_at_decision,
            elapsed_ms,
            decision.decision_reason,
        )

        # 5. Transition request to SCHEDULED
        self._repository.update_status(
            request_id=decision.selected_request_id,
            new_status=TransferStatus.SCHEDULED,
            actor="SchedulingService",
            reason=f"Policy={self._policy_name}; {decision.decision_reason}",
        )

        # 6. Emit audit event
        audit = AuditEvent(
            event_type=AuditEventType.SCHEDULING_DECISION,
            request_id=decision.selected_request_id,
            component="SchedulingService",
            details={
                "policy": self._policy_name,
                "queue_length": decision.queue_length_at_decision,
                "starvation_age_seconds": decision.starvation_age_seconds,
                "decision_duration_ms": round(elapsed_ms, 3),
                "decision_reason": decision.decision_reason,
            },
        )
        self._repository.record_audit_event(audit)

        return decision

    def run_n_cycles(self, n: int) -> list[SchedulingDecision]:
        """Execute up to *n* consecutive scheduling cycles.

        Stops early when no eligible candidates remain.

        Args:
            n: Maximum number of scheduling cycles to execute.

        Returns:
            List of SchedulingDecisions produced (may be shorter than *n*).
        """
        decisions: list[SchedulingDecision] = []
        for _ in range(n):
            decision = self.run_scheduling_cycle()
            if decision is None:
                break
            decisions.append(decision)
        return decisions

    def get_queue_snapshot(self) -> list[TransferRequest]:
        """Return the current list of QUEUED requests (read-only snapshot)."""
        return self._repository.list_requests(status=TransferStatus.QUEUED)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_telemetry(self) -> TelemetrySnapshot:
        """Retrieve a fresh telemetry snapshot from the provider, or fallback."""
        if self._telemetry_provider is not None:
            try:
                return self._telemetry_provider()
            except Exception:
                logger.warning("Telemetry provider failed; using default snapshot.", exc_info=True)
        return _build_default_telemetry()

    @staticmethod
    def _build_scheduler(
        policy: SchedulingPolicyName,
        overrides: dict[str, Any],
    ) -> IScheduler:
        """Instantiate the correct IScheduler implementation for *policy*."""
        if policy == SchedulingPolicyName.FIFO:
            return FIFOScheduler()
        if policy == SchedulingPolicyName.PRIORITY:
            return PriorityScheduler()
        if policy == SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC:
            return ResourceAwareHeuristicScheduler(**overrides)
        # AI_ASSISTED is handled in Milestone 4; fall back to FIFO for now.
        logger.warning("Policy %s not yet implemented; falling back to FIFO.", policy)
        return FIFOScheduler()


__all__ = ["SchedulingService"]
