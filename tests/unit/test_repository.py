"""Unit tests for SqliteTransferRepository implementation (TASK-1.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import (
    AuditEventType,
    DataCategory,
    PriorityLevel,
    SchedulingPolicyName,
    SecurityProfile,
    TransferStatus,
)
from app.core.exceptions import TransferNotFoundError
from app.database.repository import SqliteTransferRepository
from app.database.schema import Base
from app.models.policy_decision import AuditEvent, PolicyDecision
from app.models.scheduling import SchedulingDecision
from app.models.transfer_request import TransferRequest


@pytest.fixture
def sqlite_repo() -> SqliteTransferRepository:
    """Creates an isolated in-memory SQLite repository for unit testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    repo = SqliteTransferRepository(session=session)
    return repo


def test_create_and_get_request_preserves_uuid_and_timestamps(sqlite_repo: SqliteTransferRepository) -> None:
    """Verifies DoD for TASK-1.3: Creating and retrieving requests preserves all timestamp and UUID fields."""
    fixed_uuid = uuid4()
    fixed_submitted_at = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)
    fixed_deadline = datetime(2026, 11, 7, 14, 30, 0, tzinfo=UTC)

    request = TransferRequest(
        request_id=fixed_uuid,
        source_id="univ-lab",
        destination_id="poly-clinic",
        data_size_bytes=10_485_760,  # 10 MB
        priority=PriorityLevel.HIGH,
        submitted_at=fixed_submitted_at,
        deadline=fixed_deadline,
        security_profile=SecurityProfile.QKD_AES_256_GCM,
        data_category=DataCategory.MEDICAL_IMAGE,
        status=TransferStatus.SUBMITTED,
        metadata={"scanner_id": "MRI-04", "anonymous_tag": "test-sample"},
    )

    created = sqlite_repo.create_request(request)

    # Assert creation returns matching fields
    assert created.request_id == fixed_uuid
    assert isinstance(created.request_id, UUID)
    assert created.submitted_at == fixed_submitted_at
    assert created.deadline == fixed_deadline

    # Assert retrieval preserves exact UUID and timestamps
    retrieved = sqlite_repo.get_request(fixed_uuid)
    assert retrieved is not None
    assert retrieved.request_id == fixed_uuid
    assert isinstance(retrieved.request_id, UUID)
    assert retrieved.submitted_at == fixed_submitted_at
    assert retrieved.deadline == fixed_deadline
    assert retrieved.priority == PriorityLevel.HIGH
    assert retrieved.data_category == DataCategory.MEDICAL_IMAGE
    assert retrieved.security_profile == SecurityProfile.QKD_AES_256_GCM
    assert retrieved.metadata["scanner_id"] == "MRI-04"


def test_update_status_records_immutable_transition(sqlite_repo: SqliteTransferRepository) -> None:
    """Verifies that update_status records an immutable transition log in SQLite."""
    request_id = uuid4()
    req = TransferRequest(
        request_id=request_id,
        source_id="univ-lab",
        destination_id="poly-clinic",
        data_size_bytes=1024,
        priority=PriorityLevel.NORMAL,
        security_profile=SecurityProfile.QKD_AES_256_GCM,
        data_category=DataCategory.DIAGNOSTIC_REPORT,
        status=TransferStatus.SUBMITTED,
    )
    sqlite_repo.create_request(req)

    # 1. Transition SUBMITTED -> VALIDATING
    updated1 = sqlite_repo.update_status(
        request_id,
        new_status=TransferStatus.VALIDATING,
        actor="policy_engine",
        reason="Validation started",
    )
    assert updated1.status == TransferStatus.VALIDATING

    # 2. Transition VALIDATING -> QUEUED
    updated2 = sqlite_repo.update_status(
        request_id,
        new_status=TransferStatus.QUEUED,
        actor="policy_engine",
        reason="AUTH_SUCCESS",
    )
    assert updated2.status == TransferStatus.QUEUED

    # 3. Transition QUEUED -> SCHEDULED
    updated3 = sqlite_repo.update_status(
        request_id,
        new_status=TransferStatus.SCHEDULED,
        actor="fifo_scheduler",
    )
    assert updated3.status == TransferStatus.SCHEDULED
    assert updated3.scheduled_at is not None

    # Verify transitions history
    transitions = sqlite_repo.get_transitions(request_id)
    assert len(transitions) == 3
    assert transitions[0].from_status == TransferStatus.SUBMITTED
    assert transitions[0].to_status == TransferStatus.VALIDATING
    assert transitions[0].actor == "policy_engine"
    assert transitions[1].from_status == TransferStatus.VALIDATING
    assert transitions[1].to_status == TransferStatus.QUEUED
    assert transitions[2].from_status == TransferStatus.QUEUED
    assert transitions[2].to_status == TransferStatus.SCHEDULED


def test_update_status_not_found(sqlite_repo: SqliteTransferRepository) -> None:
    """Verifies TransferNotFoundError when attempting to update unknown UUID."""
    unknown_id = uuid4()
    with pytest.raises(TransferNotFoundError, match="not found"):
        sqlite_repo.update_status(unknown_id, TransferStatus.QUEUED, actor="test")


def test_list_requests_filtering_and_pagination(sqlite_repo: SqliteTransferRepository) -> None:
    """Verifies filtering by status, priority, and pagination (limit/offset)."""
    now = datetime.now(UTC)
    for i in range(10):
        req = TransferRequest(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=1000 * (i + 1),
            priority=PriorityLevel.CRITICAL if i < 3 else PriorityLevel.LOW,
            submitted_at=now + timedelta(seconds=i),
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.QUEUED if i % 2 == 0 else TransferStatus.SUBMITTED,
        )
        sqlite_repo.create_request(req)

    # Filter by status
    queued = sqlite_repo.list_requests(status=TransferStatus.QUEUED)
    assert len(queued) == 5
    assert all(r.status == TransferStatus.QUEUED for r in queued)

    # Filter by priority
    critical = sqlite_repo.list_requests(priority=PriorityLevel.CRITICAL)
    assert len(critical) == 3
    assert all(r.priority == PriorityLevel.CRITICAL for r in critical)

    # Pagination: limit=3, offset=2
    paginated = sqlite_repo.list_requests(limit=3, offset=2)
    assert len(paginated) == 3


def test_record_scheduling_and_audit_events(sqlite_repo: SqliteTransferRepository) -> None:
    """Verifies recording and retrieving scheduling decisions, policy decisions, and audit events."""
    req_id = uuid4()
    sched = SchedulingDecision(
        selected_request_id=req_id,
        policy_name=SchedulingPolicyName.FIFO,
        decision_reason="FIFO pick",
        estimated_start_time=datetime.now(UTC),
        predicted_completion_time=datetime.now(UTC) + timedelta(seconds=5),
        predicted_deadline_miss_probability=0.01,
        queue_length_at_decision=2,
        starvation_age_seconds=1.2,
    )
    sqlite_repo.record_scheduling_decision(sched)
    decisions = sqlite_repo.get_scheduling_decisions(req_id)
    assert len(decisions) == 1
    assert decisions[0].selected_request_id == req_id
    assert decisions[0].policy_name == SchedulingPolicyName.FIFO

    # Policy decision
    pol = PolicyDecision(
        request_id=req_id,
        allowed=True,
        reason_code="AUTH_SUCCESS",
        explanation="Access granted",
        required_security_profile=SecurityProfile.QKD_AES_256_GCM,
    )
    sqlite_repo.record_policy_decision(pol)
    p_decisions = sqlite_repo.get_policy_decisions(req_id)
    assert len(p_decisions) == 1
    assert p_decisions[0].allowed is True

    # Audit event
    audit = AuditEvent(
        request_id=req_id,
        event_type=AuditEventType.POLICY_EVALUATED,
        component="policy_engine",
        details={"result": "allowed", "key": "secret_key_material"},  # should be redacted
    )
    sqlite_repo.record_audit_event(audit)
    audit_events = sqlite_repo.get_audit_events(req_id)
    assert len(audit_events) == 1
    assert audit_events[0].details["key"] == "[REDACTED_BY_AUDIT_POLICY]"
