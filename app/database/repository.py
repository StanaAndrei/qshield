"""Persistence repository contract and SQLite implementation for Q-SHIELD."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import PriorityLevel, TransferStatus
from app.core.exceptions import TransferNotFoundError
from app.database.connection import get_session_factory
from app.database.schema import (
    AuditEventORM,
    PolicyDecisionORM,
    SchedulingDecisionORM,
    StateTransitionORM,
    TransferRequestORM,
)
from app.models.policy_decision import AuditEvent, PolicyDecision
from app.models.scheduling import SchedulingDecision
from app.models.transfer_request import StateTransitionRecord, TransferRequest


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


class SqliteTransferRepository(ITransferRepository):
    """Production and simulation SQLite repository implementation conforming to ITransferRepository."""

    def __init__(self, session: Session | None = None) -> None:
        self._session = session
        self._owns_session = False

    @property
    def session(self) -> Session:
        """Returns the active session or lazily initializes a dedicated session."""
        if self._session is None:
            factory = get_session_factory()
            self._session = factory()
            self._owns_session = True
        return self._session

    def close(self) -> None:
        """Closes the session if it was lazily instantiated by this repository."""
        if self._owns_session and self._session is not None:
            self._session.close()
            self._session = None
            self._owns_session = False

    def __enter__(self) -> SqliteTransferRepository:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()

    def create_request(self, request: TransferRequest) -> TransferRequest:
        """Persists a new transfer request in SQLite."""
        orm_req = TransferRequestORM.from_domain(request)
        self.session.add(orm_req)
        self.session.commit()
        self.session.refresh(orm_req)
        return orm_req.to_domain()

    def get_request(self, request_id: UUID) -> TransferRequest | None:
        """Retrieves a transfer request by UUID, returning None if not found."""
        stmt = select(TransferRequestORM).where(TransferRequestORM.request_id == str(request_id))
        orm_req = self.session.scalar(stmt)
        return orm_req.to_domain() if orm_req is not None else None

    def update_status(
        self,
        request_id: UUID,
        new_status: TransferStatus,
        actor: str,
        reason: str | None = None,
    ) -> TransferRequest:
        """Updates request status, records an immutable StateTransitionRecord, and updates timestamps."""
        stmt = select(TransferRequestORM).where(TransferRequestORM.request_id == str(request_id))
        orm_req = self.session.scalar(stmt)
        if orm_req is None:
            raise TransferNotFoundError(
                f"Transfer request {request_id} not found",
                code="TRANSFER_NOT_FOUND",
            )

        old_status = TransferStatus(orm_req.status)
        now = datetime.now(UTC)

        # Record immutable transition history
        transition = StateTransitionORM(
            transition_id=str(uuid4()),
            request_id=str(request_id),
            from_status=old_status.value,
            to_status=new_status.value,
            timestamp=now,
            actor=actor,
            reason=reason,
        )
        self.session.add(transition)

        # Operational timestamp checkpoints
        if new_status == TransferStatus.SCHEDULED and orm_req.scheduled_at is None:
            orm_req.scheduled_at = now
        elif new_status == TransferStatus.TRANSFERRING and orm_req.transfer_started_at is None:
            orm_req.transfer_started_at = now
        elif new_status == TransferStatus.VERIFYING and orm_req.transfer_completed_at is None:
            orm_req.transfer_completed_at = now
        elif new_status == TransferStatus.COMPLETED and orm_req.verification_completed_at is None:
            orm_req.verification_completed_at = now
        elif new_status == TransferStatus.FAILED and reason and not orm_req.failure_reason:
            orm_req.failure_reason = reason
        elif old_status == TransferStatus.TRANSFERRING and new_status == TransferStatus.QUEUED:
            orm_req.retry_count += 1

        orm_req.status = new_status.value
        self.session.commit()
        self.session.refresh(orm_req)
        return orm_req.to_domain()

    def list_requests(
        self,
        status: TransferStatus | None = None,
        priority: PriorityLevel | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TransferRequest]:
        """Queries requests with optional status/priority filtering, ordered by submitted_at."""
        stmt = select(TransferRequestORM)
        if status is not None:
            stmt = stmt.where(TransferRequestORM.status == status.value)
        if priority is not None:
            stmt = stmt.where(TransferRequestORM.priority == priority.value)

        stmt = stmt.order_by(TransferRequestORM.submitted_at.asc()).offset(offset).limit(limit)
        orm_items = self.session.scalars(stmt).all()
        return [item.to_domain() for item in orm_items]

    def record_scheduling_decision(self, decision: SchedulingDecision) -> None:
        """Records a scheduling decision in SQLite."""
        orm_dec = SchedulingDecisionORM.from_domain(decision)
        self.session.add(orm_dec)
        self.session.commit()

    def record_audit_event(self, event: AuditEvent) -> None:
        """Records an immutable security audit event in SQLite."""
        orm_evt = AuditEventORM.from_domain(event)
        self.session.add(orm_evt)
        self.session.commit()

    def record_policy_decision(self, decision: PolicyDecision) -> None:
        """Records a policy evaluation decision in SQLite."""
        orm_dec = PolicyDecisionORM.from_domain(decision)
        self.session.add(orm_dec)
        self.session.commit()

    def get_policy_decisions(self, request_id: UUID | None = None) -> list[PolicyDecision]:
        """Retrieves recorded policy decisions."""
        stmt = select(PolicyDecisionORM)
        if request_id is not None:
            stmt = stmt.where(PolicyDecisionORM.request_id == str(request_id))
        stmt = stmt.order_by(PolicyDecisionORM.evaluated_at.asc())
        return [d.to_domain() for d in self.session.scalars(stmt).all()]

    def get_transitions(self, request_id: UUID | None = None) -> list[StateTransitionRecord]:
        """Retrieves state transitions for a given request or all requests."""
        stmt = select(StateTransitionORM)
        if request_id is not None:
            stmt = stmt.where(StateTransitionORM.request_id == str(request_id))
        stmt = stmt.order_by(StateTransitionORM.timestamp.asc())
        return [t.to_domain() for t in self.session.scalars(stmt).all()]

    def get_scheduling_decisions(self, request_id: UUID | None = None) -> list[SchedulingDecision]:
        """Retrieves recorded scheduling decisions."""
        stmt = select(SchedulingDecisionORM)
        if request_id is not None:
            stmt = stmt.where(SchedulingDecisionORM.selected_request_id == str(request_id))
        stmt = stmt.order_by(SchedulingDecisionORM.evaluated_at.asc())
        return [d.to_domain() for d in self.session.scalars(stmt).all()]

    def get_audit_events(self, request_id: UUID | None = None) -> list[AuditEvent]:
        """Retrieves recorded audit events."""
        stmt = select(AuditEventORM)
        if request_id is not None:
            stmt = stmt.where(AuditEventORM.request_id == str(request_id))
        stmt = stmt.order_by(AuditEventORM.timestamp.asc())
        return [e.to_domain() for e in self.session.scalars(stmt).all()]


__all__ = [
    "ITransferRepository",
    "SqliteTransferRepository",
]
