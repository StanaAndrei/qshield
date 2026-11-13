"""Telemetry metric tracking and rolling window statistics for Q-SHIELD (TASK-4.1).

Provides thread-safe statistical accumulators for network throughput, latency,
packet loss, and QKD resource dynamics across logical transfer routes.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from datetime import UTC, datetime, timedelta

from app.core.enums import QKDLinkStatus
from app.models.telemetry import NetworkTelemetry, QKDResourceMetrics


def utc_now() -> datetime:
    return datetime.now(UTC)


class RollingWindowStats:
    """Thread-safe time-decayed rolling statistical window."""

    def __init__(self, window_seconds: float = 300.0, max_samples: int = 1000) -> None:
        self.window_seconds = window_seconds
        self.max_samples = max_samples
        self._lock = threading.Lock()
        self._samples: deque[tuple[datetime, float]] = deque(maxlen=max_samples)

    def add(self, value: float, timestamp: datetime | None = None) -> None:
        """Add a new observation to the sliding window."""
        ts = timestamp or utc_now()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        with self._lock:
            self._samples.append((ts, float(value)))
            self._prune_locked(ts)

    def _prune_locked(self, now: datetime) -> None:
        """Prune samples older than the rolling window duration."""
        cutoff = now - timedelta(seconds=self.window_seconds)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def get_values(self) -> list[float]:
        """Return all active values currently inside the window."""
        now = utc_now()
        with self._lock:
            self._prune_locked(now)
            return [v for _, v in self._samples]

    @property
    def count(self) -> int:
        return len(self.get_values())

    @property
    def mean(self) -> float:
        values = self.get_values()
        if not values:
            return 0.0
        return sum(values) / len(values)

    @property
    def std(self) -> float:
        values = self.get_values()
        if len(values) < 2:
            return 0.0
        avg = sum(values) / len(values)
        variance = sum((x - avg) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)

    @property
    def min(self) -> float:
        values = self.get_values()
        return min(values) if values else 0.0

    @property
    def max(self) -> float:
        values = self.get_values()
        return max(values) if values else 0.0


class LinkMetricsAggregator:
    """Aggregates real-time and rolling network telemetry for a specific source-destination route."""

    def __init__(
        self,
        source_id: str,
        destination_id: str,
        baseline_throughput_mbps: float = 100.0,
        rtt_ms: float = 10.0,
        is_simulated: bool = True,
    ) -> None:
        self.source_id = source_id
        self.destination_id = destination_id
        self.baseline_throughput_mbps = baseline_throughput_mbps
        self.rtt_ms = rtt_ms
        self.is_simulated = is_simulated

        self._lock = threading.Lock()
        self.throughput_stats = RollingWindowStats(window_seconds=300.0)
        self.duration_stats = RollingWindowStats(window_seconds=300.0)
        self.total_transfers: int = 0
        self.failed_transfers: int = 0
        self.last_observed_throughput_mbps: float = baseline_throughput_mbps
        self.last_updated_at: datetime = utc_now()

    def record_transfer(
        self,
        throughput_mbps: float,
        duration_seconds: float,
        success: bool,
        rtt_ms: float | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Record outcome of a completed transfer on this link."""
        ts = timestamp or utc_now()
        with self._lock:
            self.total_transfers += 1
            if not success:
                self.failed_transfers += 1
            if throughput_mbps > 0.0:
                self.throughput_stats.add(throughput_mbps, ts)
                self.last_observed_throughput_mbps = throughput_mbps
            if duration_seconds > 0.0:
                self.duration_stats.add(duration_seconds, ts)
            if rtt_ms is not None and rtt_ms >= 0.0:
                self.rtt_ms = rtt_ms
            self.last_updated_at = ts

    def to_network_telemetry(self) -> NetworkTelemetry:
        """Render a NetworkTelemetry snapshot model."""
        with self._lock:
            rolling_tp = self.throughput_stats.mean
            effective_tp = rolling_tp if rolling_tp > 0.0 else self.last_observed_throughput_mbps
            loss_rate = (
                (self.failed_transfers / self.total_transfers)
                if self.total_transfers > 0
                else 0.0
            )

            return NetworkTelemetry(
                source_id=self.source_id,
                destination_id=self.destination_id,
                observed_throughput_mbps=round(effective_tp, 2),
                packet_loss_rate=round(min(1.0, loss_rate), 4),
                round_trip_time_ms=round(self.rtt_ms, 2),
                is_simulated=self.is_simulated,
                measured_at=self.last_updated_at,
            )


class QKDMetricsAggregator:
    """Aggregates quantum key service telemetry for a specific link."""

    def __init__(
        self,
        source_id: str,
        destination_id: str,
        link_status: QKDLinkStatus = QKDLinkStatus.CONNECTED,
        initial_keys: int = 1000,
        generation_rate_bps: float = 50000.0,
        query_latency_ms: float = 5.0,
        is_simulated: bool = True,
    ) -> None:
        self.source_id = source_id
        self.destination_id = destination_id
        self.link_status = link_status
        self.available_keys = initial_keys
        self.generation_rate_bps = generation_rate_bps
        self.query_latency_ms = query_latency_ms
        self.is_simulated = is_simulated
        self.last_updated_at: datetime = utc_now()
        self._lock = threading.Lock()

    def update(
        self,
        link_status: QKDLinkStatus | None = None,
        available_keys: int | None = None,
        generation_rate_bps: float | None = None,
        query_latency_ms: float | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Update QKD telemetry fields."""
        with self._lock:
            if link_status is not None:
                self.link_status = link_status
            if available_keys is not None:
                self.available_keys = max(0, available_keys)
            if generation_rate_bps is not None:
                self.generation_rate_bps = max(0.0, generation_rate_bps)
            if query_latency_ms is not None:
                self.query_latency_ms = max(0.0, query_latency_ms)
            self.last_updated_at = timestamp or utc_now()

    def to_qkd_metrics(self) -> QKDResourceMetrics:
        """Render a QKDResourceMetrics snapshot model."""
        with self._lock:
            return QKDResourceMetrics(
                source_id=self.source_id,
                destination_id=self.destination_id,
                link_status=self.link_status,
                key_buffer_available_keys=self.available_keys,
                key_generation_rate_bps=self.generation_rate_bps,
                key_service_latency_ms=self.query_latency_ms,
                is_simulated=self.is_simulated,
                measured_at=self.last_updated_at,
            )


def compute_system_load_factor(
    active_transfers: int,
    queue_length: int,
    max_active_transfers: int = 10,
    max_queue_depth: int = 50,
) -> float:
    """Compute normalized composite system load factor in [0.0, 1.0]."""
    transfer_component = min(1.0, active_transfers / max(1, max_active_transfers))
    queue_component = min(1.0, queue_length / max(1, max_queue_depth))
    # 70% weight on active concurrent transfers, 30% weight on queue backlog
    load = 0.70 * transfer_component + 0.30 * queue_component
    return round(min(1.0, max(0.0, load)), 3)


__all__ = [
    "LinkMetricsAggregator",
    "QKDMetricsAggregator",
    "RollingWindowStats",
    "compute_system_load_factor",
    "utc_now",
]
