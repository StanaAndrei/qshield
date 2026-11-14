"""Vendor-neutral abstract interface contract for QKD adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.enums import QKDLinkStatus
from app.models.scheduling import KeyHandle
from app.models.telemetry import QKDResourceMetrics
from app.qkd.capabilities import QKDCapabilities


class IQKDAdapter(ABC):
    """Vendor-neutral abstraction over QKD key management infrastructure.

    Strictly guarantees that raw secret key bytes are never exposed to callers,
    schedulers, predictors, or loggers. All key references are mediated through
    opaque KeyHandle instances.
    """

    @abstractmethod
    def get_status(self, source_id: str, destination_id: str) -> QKDLinkStatus:
        """Queries physical/simulated link connectivity status.

        Args:
            source_id: Logical source node identifier.
            destination_id: Logical destination node identifier.

        Returns:
            QKDLinkStatus: Current status (CONNECTED, DEGRADED, DISCONNECTED, UNAVAILABLE).
        """
        pass

    @abstractmethod
    def get_resource_metrics(self, source_id: str, destination_id: str) -> QKDResourceMetrics:
        """Retrieves key buffer levels and generation metrics.

        Args:
            source_id: Logical source node identifier.
            destination_id: Logical destination node identifier.

        Returns:
            QKDResourceMetrics detailing key buffer, generation rate, and latency.
        """
        pass

    @abstractmethod
    def acquire_key_handle(
        self, source_id: str, destination_id: str, key_size_bits: int = 256
    ) -> KeyHandle:
        """Consumes/allocates a key and returns an opaque handle.

        CRITICAL: Never exposes secret key bytes to callers outside crypto adapters.

        Args:
            source_id: Logical source node identifier.
            destination_id: Logical destination node identifier.
            key_size_bits: Key length in bits (typically 256).

        Returns:
            KeyHandle: Opaque reference to the allocated key material.

        Raises:
            QKDKeyBufferDepletedError: When buffer is depleted.
            QKDError: When link is unavailable or disconnected.
        """
        pass

    @abstractmethod
    def get_capabilities(
        self, source_id: str | None = None, destination_id: str | None = None
    ) -> QKDCapabilities:
        """Returns supported cryptographic and hardware capabilities for the link/adapter.

        Args:
            source_id: Optional source node identifier.
            destination_id: Optional destination node identifier.

        Returns:
            QKDCapabilities object.
        """
        pass


__all__ = ["IQKDAdapter"]
