"""Queue management service for Q-SHIELD (TASK-2.2).

Evaluates pending requests, computes current wait times, detects deadline
expirations, and transitions expired requests to ``EXPIRED`` status.

The ``QueueService`` is intentionally decoupled from scheduling:  it concerns
itself only with queue *health* (expiry, wait-time statistics) rather than
selecting which request to execute next.  The ``SchedulingService`` handles
the selection side.

DoD (TASK-2.2):
    Requests with past deadlines are transitioned to EXPIRED and excluded
    from scheduling.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from app.core.enums import AuditEventType, TransferStatus
from app.database.repository import ITransferRepository
from app.models.policy_decision import AuditEvent
from app.models.transfer_request import TransferRequest

logger = logging.getLogger(__name__)

# Statuses that are still "in-flight" and therefore eligible for deadline checking.
_EXPIRABLE_STATUSES: frozenset[TransferStatus] = frozenset(
    {
        TransferStatus.SUBMITTED,
        TransferStatus.VALIDATING,
        TransferStatus.QUEUED,
        TransferStatus.SCHEDULED,
    }
)


class QueueEntry:
    """Lightweight descriptor for a queued request's wait-time and deadline state.

    Attributes
    ----------
    request:
        The underlying ``TransferRequest``.
    wait_seconds:
        Seconds the request has been in the queue (from ``submitted_at`` to *now*).
    is_expired:
        ``True`` when the request has a deadline that has already passed.
    seconds_until_deadline:
        Remaining seconds until the deadline, or ``None`` if no deadline is set.
        Negative values indicate the deadline has already passed.
    """

    __slots__ = ("request", "wait_seconds", "is_expired", "seconds_until_deadline")

    def __init__(
        self,
        request: TransferRequest,
        now: datetime,
    ) -> None:
        self.request = request
        self.wait_seconds: float = max(0.0, (now - request.submitted_at).total_seconds())
        self.seconds_until_deadline: float | None = None
        self.is_expired: bool = False

        if request.deadline is not None:
            self.seconds_until_deadline = (request.deadline - now).total_seconds()
            self.is_expired = self.seconds_until_deadline < 0.0


class QueueSnapshot:
    """Aggregated view of the current queue state.

    Attributes
    ----------
    entries:
        All ``QueueEntry`` objects built from QUEUED requests at snapshot time.
    expired_count:
        Number of requests whose deadlines have already passed.
    total_count:
        Total number of QUEUED requests.
    max_wait_seconds:
        Longest wait time across all current QUEUED requests.
    mean_wait_seconds:
        Mean wait time across all current QUEUED requests (0.0 if empty).
    snapshot_at:
        UTC timestamp when this snapshot was taken.
    """

    def __init__(self, entries: list[QueueEntry], snapshot_at: datetime) -> None:
        self.entries = entries
        self.snapshot_at = snapshot_at

        self.total_count = len(entries)
        self.expired_count = sum(1 for e in entries if e.is_expired)

        waits = [e.wait_seconds for e in entries]
        self.max_wait_seconds: float = max(waits) if waits else 0.0
        self.mean_wait_seconds: float = sum(waits) / len(waits) if waits else 0.0

    @property
    def active_count(self) -> int:
        """Non-expired QUEUED request count."""
        return self.total_count - self.expired_count


class QueueService:
    """Manages queue health: expiry detection and wait-time accounting.

    Parameters
    ----------
    repository:
        Repository used to read and update request state.
    actor_name:
        Identification string recorded in ``StateTransitionRecord`` and audit
        events when this service transitions requests.  Defaults to
        ``"QueueService"``.
    """

    def __init__(
        self,
        repository: ITransferRepository,
        actor_name: str = "QueueService",
    ) -> None:
        self._repository = repository
        self._actor = actor_name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_queue_snapshot(self) -> QueueSnapshot:
        """Return a point-in-time snapshot of all QUEUED requests.

        Computes wait times and deadline states for every QUEUED request
        in the repository.  Does **not** perform any state transitions.

        Returns
        -------
        QueueSnapshot
            Aggregated wait-time and expiry statistics.
        """
        now = datetime.now(UTC)
        queued = self._repository.list_requests(status=TransferStatus.QUEUED, limit=10_000)
        entries = [QueueEntry(r, now) for r in queued]
        return QueueSnapshot(entries, now)

    def expire_overdue_requests(self) -> list[UUID]:
        """Scan QUEUED requests and transition any with passed deadlines to EXPIRED.

        Only requests in ``_EXPIRABLE_STATUSES`` with a non-None deadline that
        has elapsed are transitioned.  The method is idempotent — if no request
        is overdue it returns an empty list without side-effects.

        Returns
        -------
        list[UUID]
            UUIDs of requests that were transitioned to ``EXPIRED``.
        """
        now = datetime.now(UTC)
        expired_ids: list[UUID] = []

        # Scan all expirable statuses for deadline violations.
        for status in _EXPIRABLE_STATUSES:
            requests = self._repository.list_requests(status=status, limit=10_000)
            for req in requests:
                if req.deadline is not None and req.deadline < now:
                    logger.info(
                        "Expiring request %s (status=%s, deadline=%s, now=%s)",
                        req.request_id,
                        status.value,
                        req.deadline.isoformat(),
                        now.isoformat(),
                    )
                    self._repository.update_status(
                        request_id=req.request_id,
                        new_status=TransferStatus.EXPIRED,
                        actor=self._actor,
                        reason=(
                            f"Deadline exceeded: deadline={req.deadline.isoformat()}, "
                            f"now={now.isoformat()}"
                        ),
                    )
                    self._emit_expiry_audit(req, now)
                    expired_ids.append(req.request_id)

        if expired_ids:
            logger.info("Expired %d request(s): %s", len(expired_ids), expired_ids)
        return expired_ids

    def get_wait_time(self, request_id: UUID) -> float | None:
        """Return seconds a specific request has been waiting, or ``None`` if not found.

        Parameters
        ----------
        request_id:
            UUID of the request to query.

        Returns
        -------
        float | None
            Seconds since ``submitted_at``, or ``None`` when the request does
            not exist in the repository.
        """
        req = self._repository.get_request(request_id)
        if req is None:
            return None
        now = datetime.now(UTC)
        return max(0.0, (now - req.submitted_at).total_seconds())

    def is_expired(self, request_id: UUID) -> bool:
        """Return ``True`` if the request's deadline has already passed.

        Parameters
        ----------
        request_id:
            UUID of the request to check.

        Returns
        -------
        bool
            ``True`` when the request exists and has an elapsed deadline,
            ``False`` otherwise (including when the request is not found or
            has no deadline).
        """
        req = self._repository.get_request(request_id)
        if req is None or req.deadline is None:
            return False
        return req.deadline < datetime.now(UTC)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_expiry_audit(self, request: TransferRequest, now: datetime) -> None:
        """Record an audit event for a deadline expiry transition."""
        event = AuditEvent(
            event_type=AuditEventType.STATUS_TRANSITION,
            request_id=request.request_id,
            component=self._actor,
            details={
                "action": "deadline_expired",
                "from_status": request.status.value,
                "to_status": TransferStatus.EXPIRED.value,
                "deadline": request.deadline.isoformat() if request.deadline else None,
                "expired_at": now.isoformat(),
            },
        )
        try:
            self._repository.record_audit_event(event)
        except Exception:
            logger.warning("Failed to record expiry audit event for %s", request.request_id, exc_info=True)


__all__ = [
    "QueueEntry",
    "QueueService",
    "QueueSnapshot",
]
