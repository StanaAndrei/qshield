"""Unit tests for TransferStateMachine lifecycle transitions and guards."""

from __future__ import annotations

import pytest

from app.core.enums import TransferStatus
from app.core.exceptions import InvalidStateTransitionError
from app.core.state_machine import TransferStateMachine
from app.models.transfer_request import TransferRequest


def test_full_happy_path_lifecycle(sample_transfer_request: TransferRequest) -> None:
    """Verifies standard lifecycle progression from SUBMITTED to COMPLETED."""
    req = sample_transfer_request
    assert req.status == TransferStatus.SUBMITTED

    # 1. SUBMITTED -> VALIDATING
    t1 = TransferStateMachine.transition(req, TransferStatus.VALIDATING, actor="api", reason="Received")
    assert req.status == TransferStatus.VALIDATING
    assert t1.from_status == TransferStatus.SUBMITTED
    assert t1.to_status == TransferStatus.VALIDATING

    # 2. VALIDATING -> QUEUED
    t2 = TransferStateMachine.transition(req, TransferStatus.QUEUED, actor="policy_engine", reason="Authorized")
    assert req.status == TransferStatus.QUEUED
    assert t2.from_status == TransferStatus.VALIDATING
    assert t2.to_status == TransferStatus.QUEUED

    # 3. QUEUED -> SCHEDULED
    t3 = TransferStateMachine.transition(req, TransferStatus.SCHEDULED, actor="fifo_scheduler", reason="FIFO pick")
    assert req.status == TransferStatus.SCHEDULED
    assert req.scheduled_at is not None
    assert t3.to_status == TransferStatus.SCHEDULED

    # 4. SCHEDULED -> TRANSFERRING
    t4 = TransferStateMachine.transition(req, TransferStatus.TRANSFERRING, actor="transfer_executor")
    assert req.status == TransferStatus.TRANSFERRING
    assert req.transfer_started_at is not None
    assert t4.to_status == TransferStatus.TRANSFERRING

    # 5. TRANSFERRING -> VERIFYING
    t5 = TransferStateMachine.transition(req, TransferStatus.VERIFYING, actor="transfer_executor")
    assert req.status == TransferStatus.VERIFYING
    assert req.transfer_completed_at is not None
    assert t5.to_status == TransferStatus.VERIFYING

    # Invariant: cannot transition to COMPLETED without checksum
    with pytest.raises(InvalidStateTransitionError, match="payload_checksum_sha256"):
        TransferStateMachine.transition(req, TransferStatus.COMPLETED, actor="verifier")

    # Set checksum then complete
    req.payload_checksum_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    t6 = TransferStateMachine.transition(req, TransferStatus.COMPLETED, actor="verifier", reason="Checksum match")
    assert req.status == TransferStatus.COMPLETED
    assert req.verification_completed_at is not None
    assert t6.to_status == TransferStatus.COMPLETED


def test_invalid_state_transition_dod_examples(sample_transfer_request: TransferRequest) -> None:
    """Explicitly verifies Definition of Done requirements for TASK-1.1.

    DoD: Invalid state transitions (e.g. COMPLETED -> QUEUED or SUBMITTED -> TRANSFERRING)
    raise InvalidStateTransitionError.
    """
    req = sample_transfer_request
    assert req.status == TransferStatus.SUBMITTED

    # Direct skip: SUBMITTED -> TRANSFERRING must fail
    with pytest.raises(InvalidStateTransitionError, match="Invalid state transition from SUBMITTED to TRANSFERRING"):
        TransferStateMachine.validate_transition(TransferStatus.SUBMITTED, TransferStatus.TRANSFERRING)

    with pytest.raises(InvalidStateTransitionError):
        TransferStateMachine.transition(req, TransferStatus.TRANSFERRING, actor="attacker")

    # Setup terminal state COMPLETED
    req.status = TransferStatus.COMPLETED
    req.payload_checksum_sha256 = "dummy_checksum"

    # Terminal backward transition: COMPLETED -> QUEUED must fail
    with pytest.raises(InvalidStateTransitionError, match="terminal state COMPLETED"):
        TransferStateMachine.validate_transition(TransferStatus.COMPLETED, TransferStatus.QUEUED)

    with pytest.raises(InvalidStateTransitionError):
        TransferStateMachine.transition(req, TransferStatus.QUEUED, actor="system")


def test_validation_rejection_flow(sample_transfer_request: TransferRequest) -> None:
    """Verifies that unauthorized requests can transition to REJECTED and become terminal."""
    req = sample_transfer_request
    TransferStateMachine.transition(req, TransferStatus.VALIDATING, actor="policy_engine")
    TransferStateMachine.transition(req, TransferStatus.REJECTED, actor="policy_engine", reason="DEST_UNAUTHORIZED")
    assert req.status == TransferStatus.REJECTED
    assert TransferStateMachine.is_terminal(req.status)

    # REJECTED is terminal; no further transitions allowed
    with pytest.raises(InvalidStateTransitionError, match="terminal state REJECTED"):
        TransferStateMachine.transition(req, TransferStatus.QUEUED, actor="policy_engine")


def test_cancellation_from_active_states(sample_transfer_request: TransferRequest) -> None:
    """Verifies that active requests can be CANCELLED, but terminal ones cannot."""
    # From SUBMITTED
    req = sample_transfer_request.model_copy()
    TransferStateMachine.transition(req, TransferStatus.CANCELLED, actor="user")
    assert req.status == TransferStatus.CANCELLED
    assert TransferStateMachine.is_terminal(req.status)

    # From QUEUED
    req = sample_transfer_request.model_copy()
    req.status = TransferStatus.QUEUED
    TransferStateMachine.transition(req, TransferStatus.CANCELLED, actor="user")
    assert req.status == TransferStatus.CANCELLED

    # From TRANSFERRING
    req = sample_transfer_request.model_copy()
    req.status = TransferStatus.TRANSFERRING
    TransferStateMachine.transition(req, TransferStatus.CANCELLED, actor="user")
    assert req.status == TransferStatus.CANCELLED

    # Cancelled is terminal
    with pytest.raises(InvalidStateTransitionError):
        TransferStateMachine.transition(req, TransferStatus.TRANSFERRING, actor="user")


def test_deadline_expiration_flow(sample_transfer_request: TransferRequest) -> None:
    """Verifies transition to EXPIRED from pending queue states."""
    req = sample_transfer_request
    req.status = TransferStatus.QUEUED

    TransferStateMachine.transition(req, TransferStatus.EXPIRED, actor="queue_service", reason="Deadline passed")
    assert req.status == TransferStatus.EXPIRED
    assert TransferStateMachine.is_terminal(req.status)

    with pytest.raises(InvalidStateTransitionError):
        TransferStateMachine.transition(req, TransferStatus.SCHEDULED, actor="scheduler")


def test_transfer_retry_accounting(sample_transfer_request: TransferRequest) -> None:
    """Verifies retry increment on TRANSFERRING -> QUEUED and enforcement of max_retries."""
    req = sample_transfer_request
    req.status = TransferStatus.TRANSFERRING
    req.max_retries = 2
    req.retry_count = 0

    # Retry 1
    TransferStateMachine.transition(req, TransferStatus.QUEUED, actor="executor", reason="Network timeout 1")
    assert req.status == TransferStatus.QUEUED
    assert req.retry_count == 1

    # Re-schedule and start
    req.status = TransferStatus.TRANSFERRING

    # Retry 2
    TransferStateMachine.transition(req, TransferStatus.QUEUED, actor="executor", reason="Network timeout 2")
    assert req.status == TransferStatus.QUEUED
    assert req.retry_count == 2

    # Re-schedule and start
    req.status = TransferStatus.TRANSFERRING

    # Retry 3 should fail because max_retries is 2
    with pytest.raises(InvalidStateTransitionError, match="reached max retries"):
        TransferStateMachine.transition(req, TransferStatus.QUEUED, actor="executor", reason="Network timeout 3")

    # Transitioning to FAILED is permitted
    TransferStateMachine.transition(req, TransferStatus.FAILED, actor="executor", reason="Max retries exhausted")
    assert req.status == TransferStatus.FAILED
    assert req.failure_reason == "Max retries exhausted"


def test_verification_failure_transition(sample_transfer_request: TransferRequest) -> None:
    """Verifies failure on corrupted payload during verification."""
    req = sample_transfer_request
    req.status = TransferStatus.VERIFYING

    TransferStateMachine.transition(req, TransferStatus.FAILED, actor="integrity_verifier", reason="SHA256 mismatch")
    assert req.status == TransferStatus.FAILED
    assert req.failure_reason == "SHA256 mismatch"
    assert TransferStateMachine.is_terminal(req.status)


def test_state_machine_helpers_and_coercion() -> None:
    """Tests string coercion, can_transition, and query helper functions."""
    assert TransferStateMachine.can_transition("SUBMITTED", "VALIDATING")
    assert not TransferStateMachine.can_transition("SUBMITTED", "COMPLETED")
    assert not TransferStateMachine.can_transition("SUBMITTED", "SUBMITTED")
    assert not TransferStateMachine.can_transition("COMPLETED", "QUEUED")

    assert TransferStateMachine.is_terminal("COMPLETED")
    assert TransferStateMachine.is_terminal(TransferStatus.FAILED)
    assert not TransferStateMachine.is_terminal("QUEUED")

    assert TransferStateMachine.is_active("SCHEDULED")
    assert not TransferStateMachine.is_active("COMPLETED")

    allowed = TransferStateMachine.get_allowed_transitions("QUEUED")
    assert TransferStatus.SCHEDULED in allowed
    assert TransferStatus.CANCELLED in allowed
    assert TransferStatus.EXPIRED in allowed

    # Test unknown state error
    with pytest.raises(InvalidStateTransitionError, match="Unknown or invalid"):
        TransferStateMachine.validate_transition("INVALID_STATUS", "QUEUED")
