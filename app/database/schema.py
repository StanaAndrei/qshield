"""SQLAlchemy ORM schemas and mapping models for Q-SHIELD SQLite persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.enums import (
    AuditEventType,
    DataCategory,
    PriorityLevel,
    SchedulingPolicyName,
    SecurityProfile,
    TransferStatus,
)
from app.models.policy_decision import AuditEvent, PolicyDecision
from app.models.scheduling import SchedulingDecision
from app.models.transfer_request import StateTransitionRecord, TransferRequest


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class Base(DeclarativeBase):
    """Declarative base for all Q-SHIELD database entities."""

    pass


class TransferRequestORM(Base):
    """SQLAlchemy ORM table mapping for transfer_requests."""

    __tablename__ = "transfer_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    destination_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    data_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    security_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    data_category: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transfer_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transfer_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    transitions: Mapped[list[StateTransitionORM]] = relationship(
        "StateTransitionORM",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="StateTransitionORM.timestamp",
    )

    def to_domain(self) -> TransferRequest:
        """Converts ORM model to domain Pydantic entity."""
        return TransferRequest(
            request_id=UUID(self.request_id),
            source_id=self.source_id,
            destination_id=self.destination_id,
            data_size_bytes=self.data_size_bytes,
            priority=PriorityLevel(self.priority),
            submitted_at=_ensure_utc(self.submitted_at) or datetime.now(UTC),
            deadline=_ensure_utc(self.deadline),
            security_profile=SecurityProfile(self.security_profile),
            data_category=DataCategory(self.data_category),
            status=TransferStatus(self.status),
            retry_count=self.retry_count,
            max_retries=self.max_retries,
            metadata=dict(self.metadata_json),
            scheduled_at=_ensure_utc(self.scheduled_at),
            transfer_started_at=_ensure_utc(self.transfer_started_at),
            transfer_completed_at=_ensure_utc(self.transfer_completed_at),
            verification_completed_at=_ensure_utc(self.verification_completed_at),
            failure_reason=self.failure_reason,
            payload_checksum_sha256=self.payload_checksum_sha256,
        )

    @classmethod
    def from_domain(cls, req: TransferRequest) -> TransferRequestORM:
        """Creates ORM instance from domain Pydantic entity."""
        return cls(
            request_id=str(req.request_id),
            source_id=req.source_id,
            destination_id=req.destination_id,
            data_size_bytes=req.data_size_bytes,
            priority=req.priority.value,
            submitted_at=req.submitted_at,
            deadline=req.deadline,
            security_profile=req.security_profile.value,
            data_category=req.data_category.value,
            status=req.status.value,
            retry_count=req.retry_count,
            max_retries=req.max_retries,
            metadata_json=req.metadata,
            scheduled_at=req.scheduled_at,
            transfer_started_at=req.transfer_started_at,
            transfer_completed_at=req.transfer_completed_at,
            verification_completed_at=req.verification_completed_at,
            failure_reason=req.failure_reason,
            payload_checksum_sha256=req.payload_checksum_sha256,
        )


class StateTransitionORM(Base):
    """SQLAlchemy ORM table mapping for state_transitions."""

    __tablename__ = "state_transitions"

    transition_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("transfer_requests.request_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    request: Mapped[TransferRequestORM] = relationship("TransferRequestORM", back_populates="transitions")

    def to_domain(self) -> StateTransitionRecord:
        """Converts ORM model to domain Pydantic entity."""
        return StateTransitionRecord(
            transition_id=UUID(self.transition_id),
            request_id=UUID(self.request_id),
            from_status=TransferStatus(self.from_status),
            to_status=TransferStatus(self.to_status),
            timestamp=_ensure_utc(self.timestamp) or datetime.now(UTC),
            actor=self.actor,
            reason=self.reason,
        )

    @classmethod
    def from_domain(cls, rec: StateTransitionRecord) -> StateTransitionORM:
        """Creates ORM instance from domain Pydantic entity."""
        return cls(
            transition_id=str(rec.transition_id),
            request_id=str(rec.request_id),
            from_status=rec.from_status.value,
            to_status=rec.to_status.value,
            timestamp=rec.timestamp,
            actor=rec.actor,
            reason=rec.reason,
        )


class PolicyDecisionORM(Base):
    """SQLAlchemy ORM table mapping for policy_decisions."""

    __tablename__ = "policy_decisions"

    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    required_security_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)

    def to_domain(self) -> PolicyDecision:
        """Converts ORM model to domain Pydantic entity."""
        return PolicyDecision(
            decision_id=UUID(self.decision_id),
            request_id=UUID(self.request_id) if self.request_id else None,
            allowed=self.allowed,
            reason_code=self.reason_code,
            explanation=self.explanation,
            required_security_profile=SecurityProfile(self.required_security_profile),
            evaluated_at=_ensure_utc(self.evaluated_at) or datetime.now(UTC),
            policy_version=self.policy_version,
        )

    @classmethod
    def from_domain(cls, dec: PolicyDecision) -> PolicyDecisionORM:
        """Creates ORM instance from domain Pydantic entity."""
        return cls(
            decision_id=str(dec.decision_id),
            request_id=str(dec.request_id) if dec.request_id else None,
            allowed=dec.allowed,
            reason_code=dec.reason_code,
            explanation=dec.explanation,
            required_security_profile=dec.required_security_profile.value,
            evaluated_at=dec.evaluated_at,
            policy_version=dec.policy_version,
        )


class SchedulingDecisionORM(Base):
    """SQLAlchemy ORM table mapping for scheduling_decisions."""

    __tablename__ = "scheduling_decisions"

    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    selected_request_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    policy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    predicted_completion_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    predicted_deadline_miss_probability: Mapped[float] = mapped_column(Float, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    queue_length_at_decision: Mapped[int] = mapped_column(Integer, nullable=False)
    starvation_age_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    def to_domain(self) -> SchedulingDecision:
        """Converts ORM model to domain Pydantic entity."""
        return SchedulingDecision(
            decision_id=UUID(self.decision_id),
            selected_request_id=UUID(self.selected_request_id),
            policy_name=SchedulingPolicyName(self.policy_name),
            decision_reason=self.decision_reason,
            estimated_start_time=_ensure_utc(self.estimated_start_time) or datetime.now(UTC),
            predicted_completion_time=_ensure_utc(self.predicted_completion_time) or datetime.now(UTC),
            predicted_deadline_miss_probability=self.predicted_deadline_miss_probability,
            evaluated_at=_ensure_utc(self.evaluated_at) or datetime.now(UTC),
            queue_length_at_decision=self.queue_length_at_decision,
            starvation_age_seconds=self.starvation_age_seconds,
        )

    @classmethod
    def from_domain(cls, dec: SchedulingDecision) -> SchedulingDecisionORM:
        """Creates ORM instance from domain Pydantic entity."""
        return cls(
            decision_id=str(dec.decision_id),
            selected_request_id=str(dec.selected_request_id),
            policy_name=dec.policy_name.value,
            decision_reason=dec.decision_reason,
            estimated_start_time=dec.estimated_start_time,
            predicted_completion_time=dec.predicted_completion_time,
            predicted_deadline_miss_probability=dec.predicted_deadline_miss_probability,
            evaluated_at=dec.evaluated_at,
            queue_length_at_decision=dec.queue_length_at_decision,
            starvation_age_seconds=dec.starvation_age_seconds,
        )


class AuditEventORM(Base):
    """SQLAlchemy ORM table mapping for audit_events."""

    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    def to_domain(self) -> AuditEvent:
        """Converts ORM model to domain Pydantic entity."""
        return AuditEvent(
            event_id=UUID(self.event_id),
            event_type=AuditEventType(self.event_type),
            request_id=UUID(self.request_id) if self.request_id else None,
            timestamp=_ensure_utc(self.timestamp) or datetime.now(UTC),
            component=self.component,
            details=dict(self.details_json),
        )

    @classmethod
    def from_domain(cls, evt: AuditEvent) -> AuditEventORM:
        """Creates ORM instance from domain Pydantic entity."""
        return cls(
            event_id=str(evt.event_id),
            event_type=evt.event_type.value,
            request_id=str(evt.request_id) if evt.request_id else None,
            timestamp=evt.timestamp,
            component=evt.component,
            details_json=evt.details,
        )


__all__ = [
    "AuditEventORM",
    "Base",
    "PolicyDecisionORM",
    "SchedulingDecisionORM",
    "StateTransitionORM",
    "TransferRequestORM",
]
