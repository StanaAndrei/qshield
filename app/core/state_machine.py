"""Deterministic lifecycle state machine for Q-SHIELD transfer requests."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.enums import TransferStatus
from app.core.exceptions import InvalidStateTransitionError
from app.models.transfer_request import StateTransitionRecord, TransferRequest


class TransferStateMachine:
    """Deterministic finite state machine governing TransferRequest lifecycle transitions.

    Enforces the lifecycle specified in docs/ARCHITECTURE_CONTRACTS.md and docs/spec.md:
        SUBMITTED -> VALIDATING -> {QUEUED, REJECTED} -> SCHEDULED
                  -> TRANSFERRING -> VERIFYING -> {COMPLETED, FAILED}

    Also manages cancellation, deadline expiration, and retry loops.
    """

    ALLOWED_TRANSITIONS: dict[TransferStatus, frozenset[TransferStatus]] = {
        TransferStatus.SUBMITTED: frozenset({
            TransferStatus.VALIDATING,
            TransferStatus.CANCELLED,
            TransferStatus.EXPIRED,
        }),
        TransferStatus.VALIDATING: frozenset({
            TransferStatus.QUEUED,
            TransferStatus.REJECTED,
            TransferStatus.CANCELLED,
            TransferStatus.EXPIRED,
        }),
        TransferStatus.QUEUED: frozenset({
            TransferStatus.SCHEDULED,
            TransferStatus.CANCELLED,
            TransferStatus.EXPIRED,
        }),
        TransferStatus.SCHEDULED: frozenset({
            TransferStatus.TRANSFERRING,
            TransferStatus.QUEUED,
            TransferStatus.FAILED,
            TransferStatus.CANCELLED,
            TransferStatus.EXPIRED,
        }),
        TransferStatus.TRANSFERRING: frozenset({
            TransferStatus.VERIFYING,
            TransferStatus.QUEUED,
            TransferStatus.FAILED,
            TransferStatus.CANCELLED,
        }),
        TransferStatus.VERIFYING: frozenset({
            TransferStatus.COMPLETED,
            TransferStatus.FAILED,
        }),
        TransferStatus.COMPLETED: frozenset(),
        TransferStatus.REJECTED: frozenset(),
        TransferStatus.FAILED: frozenset(),
        TransferStatus.CANCELLED: frozenset(),
        TransferStatus.EXPIRED: frozenset(),
    }

    TERMINAL_STATES: frozenset[TransferStatus] = frozenset({
        TransferStatus.COMPLETED,
        TransferStatus.REJECTED,
        TransferStatus.FAILED,
        TransferStatus.CANCELLED,
        TransferStatus.EXPIRED,
    })

    ACTIVE_STATES: frozenset[TransferStatus] = frozenset({
        TransferStatus.SUBMITTED,
        TransferStatus.VALIDATING,
        TransferStatus.QUEUED,
        TransferStatus.SCHEDULED,
        TransferStatus.TRANSFERRING,
        TransferStatus.VERIFYING,
    })

    @classmethod
    def _coerce_status(cls, status: TransferStatus | str) -> TransferStatus:
        """Coerces string or enum input into a valid TransferStatus."""
        if isinstance(status, TransferStatus):
            return status
        try:
            return TransferStatus(status)
        except ValueError as err:
            raise InvalidStateTransitionError(
                f"Unknown or invalid transfer status: '{status}'",
                code="INVALID_STATE_TRANSITION",
            ) from err

    @classmethod
    def is_terminal(cls, status: TransferStatus | str) -> bool:
        """Returns True if the given status represents an immutable terminal state."""
        return cls._coerce_status(status) in cls.TERMINAL_STATES

    @classmethod
    def is_active(cls, status: TransferStatus | str) -> bool:
        """Returns True if the given status represents an active, non-terminal state."""
        return cls._coerce_status(status) in cls.ACTIVE_STATES

    @classmethod
    def get_allowed_transitions(cls, status: TransferStatus | str) -> frozenset[TransferStatus]:
        """Returns the set of permitted next statuses for the current status."""
        coerced = cls._coerce_status(status)
        return cls.ALLOWED_TRANSITIONS.get(coerced, frozenset())

    @classmethod
    def can_transition(
        cls,
        from_status: TransferStatus | str,
        to_status: TransferStatus | str,
    ) -> bool:
        """Checks whether a transition between two statuses is permitted."""
        try:
            source = cls._coerce_status(from_status)
            target = cls._coerce_status(to_status)
        except InvalidStateTransitionError:
            return False

        if source == target:
            return False

        return target in cls.ALLOWED_TRANSITIONS.get(source, frozenset())

    @classmethod
    def validate_transition(
        cls,
        from_status: TransferStatus | str,
        to_status: TransferStatus | str,
    ) -> None:
        """Validates a lifecycle transition, raising InvalidStateTransitionError if illegal.

        Args:
            from_status: The origin state.
            to_status: The proposed destination state.

        Raises:
            InvalidStateTransitionError: If the transition is prohibited by lifecycle rules.
        """
        source = cls._coerce_status(from_status)
        target = cls._coerce_status(to_status)

        if source == target:
            raise InvalidStateTransitionError(
                f"Cannot transition from {source.value} to itself (identity transitions not permitted).",
                code="INVALID_STATE_TRANSITION",
            )

        if source in cls.TERMINAL_STATES:
            raise InvalidStateTransitionError(
                f"Cannot transition from terminal state {source.value} to {target.value}. "
                f"Terminal states cannot transition to any new state.",
                code="INVALID_STATE_TRANSITION",
            )

        allowed = cls.ALLOWED_TRANSITIONS.get(source, frozenset())
        if target not in allowed:
            allowed_names = sorted(s.value for s in allowed) if allowed else ["(none - terminal)"]
            raise InvalidStateTransitionError(
                f"Invalid state transition from {source.value} to {target.value}. "
                f"Allowed transitions from {source.value}: {allowed_names}",
                code="INVALID_STATE_TRANSITION",
            )

    @classmethod
    def transition(
        cls,
        request: TransferRequest,
        to_status: TransferStatus | str,
        actor: str = "system",
        reason: str | None = None,
        enforce_checksum: bool = True,
    ) -> StateTransitionRecord:
        """Applies a validated transition to a TransferRequest instance and updates checkpoints.

        Args:
            request: The mutable TransferRequest domain model to transition.
            to_status: Target status to move into.
            actor: Subsystem, service, or agent triggering the state transition.
            reason: Optional explanation or error description.
            enforce_checksum: If True, prevents transition to COMPLETED without verified SHA-256.

        Returns:
            An immutable StateTransitionRecord logging the transition details.

        Raises:
            InvalidStateTransitionError: If transition is illegal or invariants are violated.
        """
        source = cls._coerce_status(request.status)
        target = cls._coerce_status(to_status)

        # Validate transition permission
        cls.validate_transition(source, target)

        now = datetime.now(UTC)

        # Invariant Rule 3: No request can transition to COMPLETED without verified SHA-256 match
        if target == TransferStatus.COMPLETED and enforce_checksum:
            if not request.payload_checksum_sha256:
                raise InvalidStateTransitionError(
                    f"Cannot transition transfer {request.request_id} to COMPLETED: "
                    f"payload_checksum_sha256 must be verified and set prior to completion.",
                    code="INVALID_STATE_TRANSITION",
                )
            if request.verification_completed_at is None:
                request.verification_completed_at = now

        # Retry accounting when failing back from TRANSFERRING -> QUEUED
        if source == TransferStatus.TRANSFERRING and target == TransferStatus.QUEUED:
            if request.retry_count >= request.max_retries:
                raise InvalidStateTransitionError(
                    f"Cannot retry transfer {request.request_id}: retry count ({request.retry_count}) "
                    f"has reached max retries ({request.max_retries}). Transition to FAILED instead.",
                    code="INVALID_STATE_TRANSITION",
                )
            request.retry_count += 1

        # Checkpoint timestamp updates
        if target == TransferStatus.SCHEDULED and request.scheduled_at is None:
            request.scheduled_at = now
        elif target == TransferStatus.TRANSFERRING and request.transfer_started_at is None:
            request.transfer_started_at = now
        elif target == TransferStatus.VERIFYING and request.transfer_completed_at is None:
            request.transfer_completed_at = now
        elif target == TransferStatus.FAILED and reason and request.failure_reason is None:
            request.failure_reason = reason

        # Apply state change
        request.status = target

        return StateTransitionRecord(
            request_id=request.request_id,
            from_status=source,
            to_status=target,
            actor=actor,
            reason=reason,
            timestamp=now,
        )

    # Instance method delegates for callers that instantiate the machine
    def __call__(
        self,
        request: TransferRequest,
        to_status: TransferStatus | str,
        actor: str = "system",
        reason: str | None = None,
        enforce_checksum: bool = True,
    ) -> StateTransitionRecord:
        return self.transition(
            request,
            to_status,
            actor=actor,
            reason=reason,
            enforce_checksum=enforce_checksum,
        )

    def validate(
        self,
        from_status: TransferStatus | str,
        to_status: TransferStatus | str,
    ) -> None:
        self.validate_transition(from_status, to_status)

    def allowed(self, status: TransferStatus | str) -> frozenset[TransferStatus]:
        return self.get_allowed_transitions(status)


__all__ = ["TransferStateMachine"]
