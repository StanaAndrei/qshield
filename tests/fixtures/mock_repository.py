"""Thread-safe in-memory repository conforming to ITransferRepository."""

from __future__ import annotations

import threading
from uuid import UUID

from app.core.enums import PriorityLevel, TransferStatus
from app.core.exceptions import TransferNotFoundError
from app.database.repository import ITransferRepository
from app.models.policy_decision import AuditEvent
from app.models.scheduling import SchedulingDecision
from app.models.transfer_request import StateTransitionRecord, TransferRequest


class InMemoryTransferRepository(ITransferRepository):
    """Thread-safe in-memory transfer repository for hermetic testing.

    Conforms to ITransferRepository. Avoids SQLite file and lock contention
    during high-concurrency unit testing and discrete-event simulations.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requests: dict[UUID, TransferRequest] = {}
        self._transitions: list[StateTransitionRecord] = []
        self._scheduling_decisions: list[SchedulingDecision] = []
        self._audit_events: list[AuditEvent] = []

    def create_request(self, request: TransferRequest) -> TransferRequest:
        with self._lock:
            # Store a copy to avoid external reference mutation
            cloned = request.model_copy(deep=True)
            self._requests[cloned.request_id] = cloned
            return cloned.model_copy(deep=True)

    def get_request(self, request_id: UUID) -> TransferRequest | None:
        with self._lock:
            req = self._requests.get(request_id)
            return req.model_copy(deep=True) if req else None

    def update_status(
        self,
        request_id: UUID,
        new_status: TransferStatus,
        actor: str,
        reason: str | None = None,
    ) -> TransferRequest:
        with self._lock:
            req = self._requests.get(request_id)
            if not req:
                raise TransferNotFoundError(
                    f"Transfer request {request_id} not found",
                    code="TRANSFER_NOT_FOUND",
                )

            old_status = req.status
            req.status = new_status

            transition = StateTransitionRecord(
                request_id=request_id,
                from_status=old_status,
                to_status=new_status,
                actor=actor,
                reason=reason,
            )
            self._transitions.append(transition)
            return req.model_copy(deep=True)

    def list_requests(
        self,
        status: TransferStatus | None = None,
        priority: PriorityLevel | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TransferRequest]:
        with self._lock:
            results = list(self._requests.values())
            if status is not None:
                results = [r for r in results if r.status == status]
            if priority is not None:
                results = [r for r in results if r.priority == priority]

            # Sort by submitted_at ascending by default
            results.sort(key=lambda r: r.submitted_at)
            paginated = results[offset : offset + limit]
            return [r.model_copy(deep=True) for r in paginated]

    def record_scheduling_decision(self, decision: SchedulingDecision) -> None:
        with self._lock:
            self._scheduling_decisions.append(decision.model_copy(deep=True))

    def record_audit_event(self, event: AuditEvent) -> None:
        with self._lock:
            self._audit_events.append(event.model_copy(deep=True))

    # Test inspection helpers
    def get_transitions(self, request_id: UUID | None = None) -> list[StateTransitionRecord]:
        with self._lock:
            if request_id is None:
                return [t.model_copy(deep=True) for t in self._transitions]
            return [t.model_copy(deep=True) for t in self._transitions if t.request_id == request_id]

    def get_scheduling_decisions(self) -> list[SchedulingDecision]:
        with self._lock:
            return [d.model_copy(deep=True) for d in self._scheduling_decisions]

    def get_audit_events(self, request_id: UUID | None = None) -> list[AuditEvent]:
        with self._lock:
            if request_id is not None:
                return [e.model_copy(deep=True) for e in self._audit_events if e.request_id == request_id]
            return [e.model_copy(deep=True) for e in self._audit_events]

    def clear(self) -> None:
        with self._lock:
            self._requests.clear()
            self._transitions.clear()
            self._scheduling_decisions.clear()
            self._audit_events.clear()
