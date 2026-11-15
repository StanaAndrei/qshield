"""Data retrieval and aggregation layer for the Q-SHIELD Dashboard (TASK-6.1).

Provides high-level queries against SQLite repositories, historical observation stores,
and optional FastAPI endpoints to power the live operational monitor and analytics.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.enums import PriorityLevel, TransferStatus
from app.database.connection import get_session_factory
from app.database.repository import SqliteTransferRepository
from app.models.policy_decision import PolicyDecision
from app.models.telemetry import TransferObservation
from app.models.transfer_request import StateTransitionRecord, TransferRequest
from app.qkd.simulator import QKDSimulatorAdapter
from app.telemetry.history import SqliteTelemetryHistoryStore

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Formatting Helpers
# -----------------------------------------------------------------------------


def format_bytes(bytes_count: int | float | None) -> str:
    """Formats byte counts into human-readable binary size strings."""
    if bytes_count is None:
        return "N/A"
    count = float(bytes_count)
    if count < 1024:
        return f"{count:.0f} B"
    elif count < 1024**2:
        return f"{count / 1024:.2f} KB"
    elif count < 1024**3:
        return f"{count / (1024**2):.2f} MB"
    else:
        return f"{count / (1024**3):.2f} GB"


def format_timestamp(dt: datetime | None) -> str:
    """Formats a datetime object to UTC ISO format string."""
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_duration(seconds: float | None) -> str:
    """Formats duration in seconds with millisecond precision."""
    if seconds is None:
        return "N/A"
    if seconds < 1.0:
        return f"{seconds * 1000:.1f} ms"
    return f"{seconds:.2f} s"


def format_throughput(mbps: float | None) -> str:
    """Formats network throughput in Mbps."""
    if mbps is None:
        return "N/A"
    return f"{mbps:.2f} Mbps"


# -----------------------------------------------------------------------------
# Repository & Store Access
# -----------------------------------------------------------------------------


def get_repository(session: Session | None = None) -> SqliteTransferRepository:
    """Returns a SqliteTransferRepository bound to the provided or default session."""
    if session is not None:
        return SqliteTransferRepository(session=session)
    factory = get_session_factory()
    return SqliteTransferRepository(session=factory())


def get_history_store(session: Session | None = None) -> SqliteTelemetryHistoryStore:
    """Returns a SqliteTelemetryHistoryStore bound to the provided or default session."""
    if session is not None:
        return SqliteTelemetryHistoryStore(session=session)
    factory = get_session_factory()
    return SqliteTelemetryHistoryStore(session=factory())


# -----------------------------------------------------------------------------
# Request Queries
# -----------------------------------------------------------------------------


def get_active_transfers(session: Session | None = None) -> list[TransferRequest]:
    """Retrieves all requests currently active in transmission or cryptographic verification."""
    owns_session = session is None
    sess = session or get_session_factory()()
    try:
        repo = SqliteTransferRepository(session=sess)
        transferring = repo.list_requests(status=TransferStatus.TRANSFERRING, limit=100)
        verifying = repo.list_requests(status=TransferStatus.VERIFYING, limit=100)
        return transferring + verifying
    finally:
        if owns_session:
            sess.close()


def get_queue_transfers(session: Session | None = None) -> list[TransferRequest]:
    """Retrieves all pending transfer requests awaiting scheduling or validation."""
    owns_session = session is None
    sess = session or get_session_factory()()
    try:
        repo = SqliteTransferRepository(session=sess)
        queued = repo.list_requests(status=TransferStatus.QUEUED, limit=100)
        scheduled = repo.list_requests(status=TransferStatus.SCHEDULED, limit=100)
        validating = repo.list_requests(status=TransferStatus.VALIDATING, limit=100)
        submitted = repo.list_requests(status=TransferStatus.SUBMITTED, limit=100)
        # Order by priority descending, then submitted_at ascending
        all_pending = scheduled + queued + validating + submitted
        prio_order = {
            PriorityLevel.CRITICAL: 0,
            PriorityLevel.HIGH: 1,
            PriorityLevel.NORMAL: 2,
            PriorityLevel.LOW: 3,
        }
        all_pending.sort(key=lambda r: (prio_order.get(r.priority, 99), r.submitted_at))
        return all_pending
    finally:
        if owns_session:
            sess.close()


def get_completed_transfers(
    session: Session | None = None, limit: int = 50
) -> list[TransferRequest]:
    """Retrieves recently completed, failed, rejected, or expired transfers."""
    owns_session = session is None
    sess = session or get_session_factory()()
    try:
        repo = SqliteTransferRepository(session=sess)
        all_reqs = repo.list_requests(limit=limit * 2)
        terminal_statuses = {
            TransferStatus.COMPLETED,
            TransferStatus.FAILED,
            TransferStatus.REJECTED,
            TransferStatus.EXPIRED,
            TransferStatus.CANCELLED,
        }
        filtered = [r for r in all_reqs if r.status in terminal_statuses]
        # Most recently submitted/processed first
        filtered.sort(key=lambda r: r.submitted_at, reverse=True)
        return filtered[:limit]
    finally:
        if owns_session:
            sess.close()


def get_all_transfers(
    session: Session | None = None, limit: int = 200
) -> list[TransferRequest]:
    """Retrieves all transfers with limit, ordered by submitted_at descending."""
    owns_session = session is None
    sess = session or get_session_factory()()
    try:
        repo = SqliteTransferRepository(session=sess)
        reqs = repo.list_requests(limit=limit)
        reqs.sort(key=lambda r: r.submitted_at, reverse=True)
        return reqs
    finally:
        if owns_session:
            sess.close()


def get_transfer_observations(
    session: Session | None = None, limit: int = 50
) -> list[TransferObservation]:
    """Retrieves historical completed transfer observations from SQLite telemetry store."""
    owns_session = session is None
    sess = session or get_session_factory()()
    try:
        store = SqliteTelemetryHistoryStore(session=sess)
        return store.get_observations(limit=limit)
    finally:
        if owns_session:
            sess.close()


def get_request_transitions(
    request_id: UUID, session: Session | None = None
) -> list[StateTransitionRecord]:
    """Retrieves state transition audit trail for a specific request."""
    owns_session = session is None
    sess = session or get_session_factory()()
    try:
        repo = SqliteTransferRepository(session=sess)
        return repo.get_transitions(request_id=request_id)
    finally:
        if owns_session:
            sess.close()


def get_request_policy_decisions(
    request_id: UUID, session: Session | None = None
) -> list[PolicyDecision]:
    """Retrieves recorded policy decisions for a request."""
    owns_session = session is None
    sess = session or get_session_factory()()
    try:
        repo = SqliteTransferRepository(session=sess)
        return repo.get_policy_decisions(request_id=request_id)
    finally:
        if owns_session:
            sess.close()


# -----------------------------------------------------------------------------
# QKD Metrics & Gauges
# -----------------------------------------------------------------------------

_DEFAULT_QKD_LINKS: list[tuple[str, str]] = [
    ("univ-lab", "poly-clinic"),
    ("poly-clinic", "univ-lab"),
    ("univ-lab", "central-hospital"),
    ("central-hospital", "univ-lab"),
    ("univ-lab", "research-cluster"),
]


def get_qkd_links_metrics(
    custom_links: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Queries QKD link metrics and buffer availability for active topologies.

    Adheres strictly to spec §16.3 by attaching provenance metadata `is_simulated=True`.
    """
    links_to_query = custom_links or _DEFAULT_QKD_LINKS
    adapter = QKDSimulatorAdapter(
        initial_keys=1000,
        max_buffer_capacity=2000,
        key_gen_rate_bps=50000.0,
        service_latency_ms=5.0,
    )

    results: list[dict[str, Any]] = []
    for src, dst in links_to_query:
        metrics = adapter.get_resource_metrics(src, dst)
        link_status = adapter.get_status(src, dst)
        pct = min(100.0, (metrics.key_buffer_available_keys / adapter.max_buffer_capacity) * 100.0)

        results.append({
            "source_id": src,
            "destination_id": dst,
            "link_key": f"{src} → {dst}",
            "status": link_status.value,
            "available_keys": metrics.key_buffer_available_keys,
            "max_capacity": adapter.max_buffer_capacity,
            "buffer_percent": pct,
            "key_generation_rate_bps": metrics.key_generation_rate_bps,
            "key_service_latency_ms": metrics.key_service_latency_ms,
            "is_simulated": True,  # Spec §16.3 strict provenance tag
        })
    return results


# -----------------------------------------------------------------------------
# KPI Metrics Summary
# -----------------------------------------------------------------------------


def get_kpi_summary(session: Session | None = None) -> dict[str, Any]:
    """Calculates consolidated operational KPIs across the database and telemetry stores."""
    owns_session = session is None
    sess = session or get_session_factory()()
    try:
        repo = SqliteTransferRepository(session=sess)
        all_reqs = repo.list_requests(limit=500)

        active_count = sum(
            1 for r in all_reqs if r.status in (TransferStatus.TRANSFERRING, TransferStatus.VERIFYING)
        )
        queued_count = sum(
            1
            for r in all_reqs
            if r.status
            in (
                TransferStatus.QUEUED,
                TransferStatus.SCHEDULED,
                TransferStatus.VALIDATING,
                TransferStatus.SUBMITTED,
            )
        )
        completed_count = sum(1 for r in all_reqs if r.status == TransferStatus.COMPLETED)
        failed_count = sum(
            1
            for r in all_reqs
            if r.status
            in (
                TransferStatus.FAILED,
                TransferStatus.REJECTED,
                TransferStatus.EXPIRED,
                TransferStatus.CANCELLED,
            )
        )

        # Observations throughput mean
        history = SqliteTelemetryHistoryStore(session=sess)
        observations = history.get_observations(limit=50)
        avg_throughput = 0.0
        if observations:
            successful_obs = [o for o in observations if o.success]
            if successful_obs:
                avg_throughput = sum(o.effective_throughput_mbps for o in successful_obs) / len(
                    successful_obs
                )

        # QKD buffer totals
        qkd_links = get_qkd_links_metrics()
        total_keys = sum(link["available_keys"] for link in qkd_links)

        # Load factor calculation: queue depth & active transfers against baseline capacity
        max_capacity = 10.0
        load_factor = min(1.0, (active_count + (0.5 * queued_count)) / max_capacity)

        return {
            "active_transfers_count": active_count,
            "queue_length": queued_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "total_requests": len(all_reqs),
            "total_keys_available": total_keys,
            "avg_throughput_mbps": avg_throughput,
            "system_load_factor": load_factor,
            "is_simulated": True,
        }
    finally:
        if owns_session:
            sess.close()


__all__ = [
    "format_bytes",
    "format_duration",
    "format_throughput",
    "format_timestamp",
    "get_active_transfers",
    "get_all_transfers",
    "get_completed_transfers",
    "get_history_store",
    "get_kpi_summary",
    "get_qkd_links_metrics",
    "get_queue_transfers",
    "get_repository",
    "get_request_policy_decisions",
    "get_request_transitions",
    "get_transfer_observations",
]
