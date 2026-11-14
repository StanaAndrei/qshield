"""Leaky-bucket simulated QKD adapter implementation (TASK-5.2)."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from app.core.enums import QKDLinkStatus
from app.core.exceptions import QKDError, QKDKeyBufferDepletedError
from app.models.scheduling import KeyHandle
from app.models.telemetry import QKDResourceMetrics
from app.qkd.base import IQKDAdapter
from app.qkd.capabilities import QKDCapabilities

logger = logging.getLogger(__name__)


class QKDSimulatorAdapter(IQKDAdapter):
    """Simulated QKD adapter implementing a leaky-bucket key pool with stochastic/manual outages.

    Features:
    - Configurable initial key count, buffer limit, and replenishment rate.
    - Deterministic time advancement or wall-clock leaky-bucket replenishment.
    - Simulated link outages (status UNAVAILABLE).
    - Resource wait reporting when buffer is 0 (status DEGRADED).
    - Safe return of opaque KeyHandle instances without secret key leakage.
    """

    def __init__(
        self,
        initial_keys: int = 1000,
        max_buffer_capacity: int = 2000,
        key_gen_rate_bps: float = 50000.0,
        replenish_rate_keys_per_second: float | None = None,
        service_latency_ms: float = 5.0,
        seed: int | None = 42,
        time_provider: Callable[[], float] | None = None,
    ) -> None:
        self.initial_keys = initial_keys
        self.max_buffer_capacity = max_buffer_capacity
        self.key_gen_rate_bps = key_gen_rate_bps
        self.replenish_rate_keys_per_second = (
            replenish_rate_keys_per_second
            if replenish_rate_keys_per_second is not None
            else key_gen_rate_bps / 256.0
        )
        self.service_latency_ms = service_latency_ms
        self.seed = seed
        self._rng = random.Random(seed) if seed is not None else random.Random()
        self._time_provider = time_provider or time.monotonic

        # Link state maps: link_key -> value
        self._buffers: dict[str, int] = {}
        self._fractional_keys: dict[str, float] = {}
        self._last_replenish_times: dict[str, float] = {}
        self._outages: dict[str, bool] = {}
        self._outage_expiry: dict[str, float] = {}

    def _canonical_key(self, source_id: str, destination_id: str) -> str:
        return f"{source_id}->{destination_id}"

    def _ensure_link_initialized(self, link_key: str) -> None:
        if link_key not in self._buffers:
            self._buffers[link_key] = self.initial_keys
            self._fractional_keys[link_key] = 0.0
            self._last_replenish_times[link_key] = self._time_provider()
            self._outages[link_key] = False
            self._outage_expiry[link_key] = 0.0

    def _update_buffer(self, link_key: str) -> None:
        self._ensure_link_initialized(link_key)
        now = self._time_provider()

        # Check outage expiry
        if self._outages.get(link_key, False):
            expiry = self._outage_expiry.get(link_key, 0.0)
            if 0.0 < expiry <= now:
                self._outages[link_key] = False
                self._outage_expiry[link_key] = 0.0
                logger.info("QKD outage ended for link %s at t=%.2f", link_key, now)

        last_time = self._last_replenish_times[link_key]
        if now <= last_time:
            return

        elapsed = now - last_time
        self._last_replenish_times[link_key] = now

        # Replenish only if not currently in an outage
        if not self._outages.get(link_key, False) and self.replenish_rate_keys_per_second > 0:
            fractional = self._fractional_keys[link_key] + (elapsed * self.replenish_rate_keys_per_second)
            whole_keys = int(fractional)
            self._fractional_keys[link_key] = fractional - whole_keys

            current = self._buffers[link_key]
            updated = min(self.max_buffer_capacity, current + whole_keys)
            self._buffers[link_key] = updated

    def set_outage(self, source_id: str, destination_id: str, is_outage: bool = True) -> None:
        """Sets or clears a manual outage for the specified link."""
        link_key = self._canonical_key(source_id, destination_id)
        self._ensure_link_initialized(link_key)
        self._outages[link_key] = is_outage
        if not is_outage:
            self._outage_expiry[link_key] = 0.0

    def simulate_outage(self, source_id: str, destination_id: str, duration_seconds: float) -> None:
        """Simulates a temporary outage that automatically clears after duration_seconds."""
        link_key = self._canonical_key(source_id, destination_id)
        self._ensure_link_initialized(link_key)
        now = self._time_provider()
        self._outages[link_key] = True
        self._outage_expiry[link_key] = now + max(0.0, duration_seconds)

    def is_in_outage(self, source_id: str, destination_id: str) -> bool:
        """Checks if the link is currently in an outage state."""
        link_key = self._canonical_key(source_id, destination_id)
        self._update_buffer(link_key)
        return self._outages.get(link_key, False)

    def is_resource_wait(self, source_id: str, destination_id: str) -> bool:
        """Checks if the link buffer is depleted, necessitating a resource wait."""
        link_key = self._canonical_key(source_id, destination_id)
        self._update_buffer(link_key)
        return self._buffers[link_key] <= 0

    def get_status(self, source_id: str, destination_id: str) -> QKDLinkStatus:
        """Queries link status: UNAVAILABLE (outage), DEGRADED (buffer empty), or CONNECTED."""
        link_key = self._canonical_key(source_id, destination_id)
        self._update_buffer(link_key)

        if self._outages.get(link_key, False):
            return QKDLinkStatus.UNAVAILABLE

        if self._buffers[link_key] <= 0:
            return QKDLinkStatus.DEGRADED

        return QKDLinkStatus.CONNECTED

    def get_resource_metrics(self, source_id: str, destination_id: str) -> QKDResourceMetrics:
        """Returns instantaneous key buffer levels, link status, and generation rate."""
        link_key = self._canonical_key(source_id, destination_id)
        self._update_buffer(link_key)
        status = self.get_status(source_id, destination_id)
        available = self._buffers[link_key]

        return QKDResourceMetrics(
            source_id=source_id,
            destination_id=destination_id,
            link_status=status,
            key_buffer_available_keys=available,
            key_generation_rate_bps=self.key_gen_rate_bps if status == QKDLinkStatus.CONNECTED else 0.0,
            key_service_latency_ms=self.service_latency_ms,
            is_simulated=True,
            measured_at=datetime.now(UTC),
        )

    def acquire_key_handle(
        self, source_id: str, destination_id: str, key_size_bits: int = 256
    ) -> KeyHandle:
        """Consumes a key from the buffer and returns an opaque KeyHandle.

        Raises:
            QKDError: If the link is experiencing an outage.
            QKDKeyBufferDepletedError: If the buffer is depleted (resource wait required).
        """
        link_key = self._canonical_key(source_id, destination_id)
        self._update_buffer(link_key)

        if self._outages.get(link_key, False):
            raise QKDError(
                f"Cannot acquire key: QKD link {source_id}->{destination_id} is UNAVAILABLE due to outage.",
                code="QKD_LINK_UNAVAILABLE",
            )

        if self._buffers[link_key] <= 0:
            raise QKDKeyBufferDepletedError(
                f"Cannot acquire key: QKD key buffer for {source_id}->{destination_id} is depleted (0 keys available). Resource wait required.",
                code="KEY_BUFFER_DEPLETED",
            )

        self._buffers[link_key] -= 1
        key_id = f"qkd-key-{uuid4()}"

        return KeyHandle(
            key_id=key_id,
            key_length_bits=key_size_bits,
            source_id=source_id,
            destination_id=destination_id,
            acquired_at=datetime.now(UTC),
        )

    def try_acquire_key_handle(
        self, source_id: str, destination_id: str, key_size_bits: int = 256
    ) -> tuple[KeyHandle | None, QKDLinkStatus]:
        """Attempts to acquire a key handle, returning (handle, status).

        When buffer is 0, returns (None, QKDLinkStatus.DEGRADED) indicating resource wait.
        """
        status = self.get_status(source_id, destination_id)
        if status != QKDLinkStatus.CONNECTED:
            return None, status

        handle = self.acquire_key_handle(source_id, destination_id, key_size_bits=key_size_bits)
        return handle, QKDLinkStatus.CONNECTED

    def replenish(
        self,
        keys: int | None = None,
        source_id: str | None = None,
        destination_id: str | None = None,
    ) -> int:
        """Restores key availability to the buffer up to max_buffer_capacity.

        Args:
            keys: Number of keys to add. If None, tops up to max_buffer_capacity.
            source_id: Optional source node (if None, replenishes all active links).
            destination_id: Optional destination node.

        Returns:
            Number of keys added.
        """
        if source_id is not None and destination_id is not None:
            link_keys = [self._canonical_key(source_id, destination_id)]
        else:
            link_keys = list(self._buffers.keys()) if self._buffers else ["univ-lab->poly-clinic"]

        total_added = 0
        for lk in link_keys:
            self._ensure_link_initialized(lk)
            current = self._buffers[lk]
            if keys is None:
                add = max(0, self.max_buffer_capacity - current)
            else:
                add = max(0, min(keys, self.max_buffer_capacity - current))
            self._buffers[lk] = current + add
            total_added += add

        return total_added

    def get_capabilities(
        self, source_id: str | None = None, destination_id: str | None = None
    ) -> QKDCapabilities:
        """Returns capability metadata for this simulator adapter."""
        return QKDCapabilities(
            supported_key_lengths_bits=[128, 256, 512],
            max_buffer_capacity=self.max_buffer_capacity,
            key_generation_rate_bps=self.key_gen_rate_bps,
            supports_key_routing=False,
            supports_outage_simulation=True,
            is_simulated=True,
            vendor_info="Q-SHIELD Leaky-Bucket QKD Simulator",
        )


# Alias per ARCHITECTURE_CONTRACTS.md §4.1
MockQKDAdapter = QKDSimulatorAdapter

__all__ = ["MockQKDAdapter", "QKDSimulatorAdapter"]
