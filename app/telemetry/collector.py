"""Real-time telemetry collector and snapshot builder for Q-SHIELD (TASK-4.1).

Aggregates active transfer states, queue depths, rolling network throughput,
packet loss rates, and QKD resource dynamics into unified TelemetrySnapshot objects.
Ingests completed transfer results and persists them to the historical observation store.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.enums import QKDLinkStatus, TransferStatus
from app.database.repository import ITransferRepository
from app.models.scheduling import TransferResult
from app.models.telemetry import (
    NetworkTelemetry,
    QKDResourceMetrics,
    TelemetrySnapshot,
    TransferObservation,
)
from app.models.transfer_request import TransferRequest
from app.telemetry.history import InMemoryTelemetryHistoryStore, ITelemetryHistoryStore
from app.telemetry.metrics import (
    LinkMetricsAggregator,
    QKDMetricsAggregator,
    compute_system_load_factor,
    utc_now,
)

logger = logging.getLogger(__name__)


class ITelemetryCollector(ABC):
    """Abstract interface for system telemetry collection and snapshot provision."""

    @abstractmethod
    def get_snapshot(self) -> TelemetrySnapshot:
        """Returns the current consolidated system snapshot."""
        pass

    @abstractmethod
    def record_transfer_completed(
        self,
        result: TransferResult,
        request: TransferRequest | None = None,
    ) -> None:
        """Ingests completed transfer observations for telemetry and model retraining."""
        pass


class TelemetryCollector(ITelemetryCollector):
    """Thread-safe real-time telemetry collector and snapshot generator.

    Tracks:
      - Currently active concurrent transfers in real time.
      - Queue depth from persistent repository or internal queue tracker.
      - Rolling per-link network throughput and packet loss.
      - Real-time QKD key buffer levels and key generation rates.
    """

    def __init__(
        self,
        repository: ITransferRepository | None = None,
        history_store: ITelemetryHistoryStore | None = None,
    ) -> None:
        self._repository = repository
        self._history_store = history_store or InMemoryTelemetryHistoryStore()
        self._lock = threading.RLock()

        # Active transfers: request_id -> {source_id, destination_id, data_size_bytes, started_at}
        self._active_transfers: dict[UUID, dict[str, Any]] = {}

        # Internal queue count override (if operating without repository)
        self._manual_queue_count: int = 0

        # Link metrics: "source->dest" -> LinkMetricsAggregator
        self._links: dict[str, LinkMetricsAggregator] = {}

        # QKD metrics: "source->dest" -> QKDMetricsAggregator
        self._qkd_links: dict[str, QKDMetricsAggregator] = {}

    # ------------------------------------------------------------------
    # Lifecycle hooks: start / complete
    # ------------------------------------------------------------------

    def notify_transfer_started(
        self,
        request_id: UUID,
        source_id: str,
        destination_id: str,
        data_size_bytes: int,
        started_at: datetime | None = None,
    ) -> None:
        """Register that a transfer has entered active transmission."""
        with self._lock:
            self._active_transfers[request_id] = {
                "source_id": source_id,
                "destination_id": destination_id,
                "data_size_bytes": data_size_bytes,
                "started_at": started_at or utc_now(),
            }
            logger.debug("Telemetry: transfer %s started (active=%d)", request_id, len(self._active_transfers))

    def record_transfer_completed(
        self,
        result: TransferResult,
        request: TransferRequest | None = None,
    ) -> None:
        """Record completion of a transfer, update rolling metrics, and persist observation."""
        with self._lock:
            transfer_info = self._active_transfers.pop(result.request_id, None)

            # Determine routing identifiers
            source_id = "unknown"
            dest_id = "unknown"
            size_bytes = result.bytes_transferred
            prio_val = 20
            category = "DIAGNOSTIC_REPORT"
            queue_wait = 0.0

            if request is not None:
                source_id = request.source_id
                dest_id = request.destination_id
                size_bytes = request.data_size_bytes
                prio_val = (
                    request.priority.value
                    if hasattr(request.priority, "value")
                    else int(request.priority)
                )
                category = (
                    request.data_category.value
                    if hasattr(request.data_category, "value")
                    else str(request.data_category)
                )
                if request.scheduled_at and request.submitted_at:
                    queue_wait = max(0.0, (request.scheduled_at - request.submitted_at).total_seconds())
            elif transfer_info is not None:
                source_id = transfer_info["source_id"]
                dest_id = transfer_info["destination_id"]
                size_bytes = transfer_info["data_size_bytes"]

            # Update rolling link aggregator
            link_key = f"{source_id}->{dest_id}"
            if link_key not in self._links:
                self._links[link_key] = LinkMetricsAggregator(source_id, dest_id)

            self._links[link_key].record_transfer(
                throughput_mbps=result.effective_throughput_mbps,
                duration_seconds=result.actual_duration_seconds,
                success=result.success and result.verification_success,
            )

            # Persist observation to history store
            obs = TransferObservation(
                observation_id=uuid4(),
                request_id=result.request_id,
                source_id=source_id,
                destination_id=dest_id,
                data_size_bytes=size_bytes,
                priority_level=prio_val,
                data_category=category,
                actual_duration_seconds=result.actual_duration_seconds,
                effective_throughput_mbps=result.effective_throughput_mbps,
                success=result.success,
                verification_success=result.verification_success,
                payload_checksum_sha256=result.payload_checksum_sha256,
                error_message=result.error_message,
                queue_wait_seconds=queue_wait,
                observed_at=utc_now(),
            )
            try:
                self._history_store.record_observation(obs)
            except Exception:
                logger.warning("Could not persist transfer observation %s", result.request_id, exc_info=True)

            logger.debug(
                "Telemetry: transfer %s completed (active=%d, throughput=%.2f Mbps)",
                result.request_id,
                len(self._active_transfers),
                result.effective_throughput_mbps,
            )

    # ------------------------------------------------------------------
    # Telemetry configuration & external sensors
    # ------------------------------------------------------------------

    def set_queue_count(self, count: int) -> None:
        """Manually update queue count when repository is not attached."""
        with self._lock:
            self._manual_queue_count = max(0, count)

    def update_network_telemetry(
        self,
        source_id: str,
        destination_id: str,
        throughput_mbps: float,
        packet_loss: float = 0.0,
        rtt_ms: float = 10.0,
    ) -> None:
        """Explicitly set or update network link parameters."""
        link_key = f"{source_id}->{destination_id}"
        with self._lock:
            if link_key not in self._links:
                self._links[link_key] = LinkMetricsAggregator(
                    source_id=source_id,
                    destination_id=destination_id,
                    baseline_throughput_mbps=throughput_mbps,
                    rtt_ms=rtt_ms,
                )
            agg = self._links[link_key]
            agg.baseline_throughput_mbps = throughput_mbps
            agg.rtt_ms = rtt_ms
            agg.last_observed_throughput_mbps = throughput_mbps
            agg.throughput_stats.add(throughput_mbps)

    def update_qkd_metrics(
        self,
        source_id: str,
        destination_id: str,
        link_status: QKDLinkStatus = QKDLinkStatus.CONNECTED,
        available_keys: int = 1000,
        generation_rate_bps: float = 50000.0,
        query_latency_ms: float = 5.0,
        is_simulated: bool = True,
    ) -> None:
        """Update QKD resource metrics for a link."""
        link_key = f"{source_id}->{destination_id}"
        with self._lock:
            if link_key not in self._qkd_links:
                self._qkd_links[link_key] = QKDMetricsAggregator(
                    source_id=source_id,
                    destination_id=destination_id,
                    link_status=link_status,
                    initial_keys=available_keys,
                    generation_rate_bps=generation_rate_bps,
                    query_latency_ms=query_latency_ms,
                    is_simulated=is_simulated,
                )
            else:
                self._qkd_links[link_key].update(
                    link_status=link_status,
                    available_keys=available_keys,
                    generation_rate_bps=generation_rate_bps,
                    query_latency_ms=query_latency_ms,
                )

    # ------------------------------------------------------------------
    # Telemetry Snapshot Provider
    # ------------------------------------------------------------------

    def get_snapshot(self) -> TelemetrySnapshot:
        """Construct consolidated real-time system state snapshot."""
        with self._lock:
            # 1. Determine active transfers count
            active_count = len(self._active_transfers)
            if self._repository is not None:
                try:
                    repo_active = self._repository.list_requests(status=TransferStatus.TRANSFERRING)
                    active_count = max(active_count, len(repo_active))
                except Exception:
                    pass

            # 2. Determine queue depth
            queue_depth = self._manual_queue_count
            if self._repository is not None:
                try:
                    queued_requests = self._repository.list_requests(status=TransferStatus.QUEUED)
                    queue_depth = len(queued_requests)
                except Exception:
                    pass

            # 3. Snapshot network metrics
            net_metrics: dict[str, NetworkTelemetry] = {
                k: agg.to_network_telemetry() for k, agg in self._links.items()
            }

            # 4. Snapshot QKD metrics
            qkd_metrics: dict[str, QKDResourceMetrics] = {
                k: agg.to_qkd_metrics() for k, agg in self._qkd_links.items()
            }

            # 5. Calculate load factor
            load_factor = compute_system_load_factor(
                active_transfers=active_count,
                queue_length=queue_depth,
            )

            return TelemetrySnapshot(
                snapshot_id=uuid4(),
                timestamp=utc_now(),
                active_transfers_count=active_count,
                queue_length=queue_depth,
                network_metrics=net_metrics,
                qkd_metrics=qkd_metrics,
                system_load_factor=load_factor,
            )


__all__ = [
    "ITelemetryCollector",
    "TelemetryCollector",
]
