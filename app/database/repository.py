"""Persistence repository contract for Q-SHIELD."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.core.enums import PriorityLevel, TransferStatus
from app.models.policy_decision import AuditEvent
from app.models.scheduling import SchedulingDecision
from app.models.transfer_request import TransferRequest


class ITransferRepository(ABC):
    """Persistence repository contract for requests, decisions, and audit events."""

    @abstractmethod
    def create_request(self, request: TransferRequest) -> TransferRequest:
        """Persists a new transfer request."""
        pass

    @abstractmethod
    def get_request(self, request_id: UUID) -> TransferRequest | None:
        """Retrieves a request by its unique UUID."""
        pass

    @abstractmethod
    def update_status(
        self,
        request_id: UUID,
        new_status: TransferStatus,
        actor: str,
        reason: str | None = None,
    ) -> TransferRequest:
        """Updates request status and appends an immutable state transition record."""
        pass

    @abstractmethod
    def list_requests(
        self,
        status: TransferStatus | None = None,
        priority: PriorityLevel | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TransferRequest]:
        """Lists requests with optional status and priority filtering."""
        pass

    @abstractmethod
    def record_scheduling_decision(self, decision: SchedulingDecision) -> None:
        """Appends a scheduling decision record."""
        pass

    @abstractmethod
    def record_audit_event(self, event: AuditEvent) -> None:
        """Appends an immutable audit event."""
        pass
