"""Discrete key-pool model for simulated QKD key resources (TASK-3.2).

Tracks available 256-bit keys, replenishment rate, buffer ceiling, and
key consumption per transfer. Blocks or throttles transfers when the key
buffer is depleted until replenishment occurs.

All time parameters are expressed in virtual simulation seconds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

logger = logging.getLogger(__name__)


class KeyAcquisitionStatus(Enum):
    """Result status of a key acquisition attempt."""

    SUCCESS = auto()
    """Key was available and consumed."""

    INSUFFICIENT_KEYS = auto()
    """Buffer depleted; transfer must wait for replenishment."""

    BUFFER_EMPTY = auto()
    """Buffer is fully empty; no keys available at all."""


@dataclass
class KeyAcquisitionResult:
    """Result of a key acquisition attempt from the buffer."""

    status: KeyAcquisitionStatus
    keys_consumed: int
    keys_remaining: int
    wait_time_seconds: float
    """Virtual seconds the caller must wait before keys are available (0 if SUCCESS)."""

    simulation_time: float = 0.0


@dataclass
class KeyResourceConfig:
    """Configuration for a simulated QKD key buffer."""

    source_id: str
    destination_id: str

    initial_keys: int = 1000
    """Initial key count in buffer at simulation start."""

    buffer_ceiling: int = 2000
    """Maximum number of keys the buffer can hold."""

    generation_rate_keys_per_second: float = 10.0
    """R_gen: key replenishment rate (256-bit keys per virtual second)."""

    keys_per_transfer: int = 1
    """Number of 256-bit keys consumed per transfer."""

    seed: int | None = None
    """Optional seed (reserved for future stochastic outage modelling)."""


@dataclass
class KeyBufferStats:
    """Snapshot of key buffer state at a point in virtual time."""

    source_id: str
    destination_id: str
    available_keys: int
    buffer_ceiling: int
    generation_rate_keys_per_second: float
    keys_per_transfer: int
    total_consumed: int
    total_replenished: int
    simulation_time: float
    depletion_events: int
    """Count of times the buffer was depleted (blocked a transfer)."""


class KeyResourceModel:
    """Discrete key-pool tracking 256-bit QKD keys.

    The model maintains an integer counter of available keys.  On each
    simulation tick, keys replenish at ``generation_rate_keys_per_second``
    (fractional accumulation is tracked until a whole key materialises).
    Transfers consume ``keys_per_transfer`` keys from the pool; if the pool
    is insufficient the model returns the virtual wait-time needed for
    replenishment.

    Parameters
    ----------
    config:
        ``KeyResourceConfig`` describing the buffer characteristics.
    """

    def __init__(self, config: KeyResourceConfig) -> None:
        self._config = config
        self._available_keys: int = config.initial_keys
        self._fractional_keys: float = 0.0  # Accumulated sub-key replenishment
        self._simulation_time: float = 0.0
        self._last_replenish_time: float = 0.0

        # Statistics
        self._total_consumed: int = 0
        self._total_replenished: int = 0
        self._depletion_events: int = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def available_keys(self) -> int:
        """Current number of whole keys in the buffer."""
        return self._available_keys

    @property
    def config(self) -> KeyResourceConfig:
        """Return the buffer configuration (read-only)."""
        return self._config

    @property
    def link_key(self) -> str:
        """Canonical link key: ``'<src>-><dst>'``."""
        return f"{self._config.source_id}->{self._config.destination_id}"

    @property
    def is_depleted(self) -> bool:
        """True when the buffer holds fewer keys than a single transfer requires."""
        return self._available_keys < self._config.keys_per_transfer

    # ------------------------------------------------------------------
    # Core simulation methods
    # ------------------------------------------------------------------

    def advance_time(self, simulation_time: float) -> int:
        """Advance the virtual clock to *simulation_time* and replenish keys.

        Keys are generated at ``generation_rate_keys_per_second`` since the
        last ``advance_time`` call.  Fractional keys accumulate until they
        total ≥ 1.0, then are added as whole keys.

        Parameters
        ----------
        simulation_time:
            New virtual clock time in seconds (must be ≥ current time).

        Returns
        -------
        int
            Number of whole keys added during this time step.
        """
        if simulation_time <= self._last_replenish_time:
            self._simulation_time = simulation_time
            return 0

        elapsed = simulation_time - self._last_replenish_time
        generated_fractional = elapsed * self._config.generation_rate_keys_per_second
        self._fractional_keys += generated_fractional

        whole_keys_added = int(self._fractional_keys)
        self._fractional_keys -= whole_keys_added

        new_total = min(self._available_keys + whole_keys_added, self._config.buffer_ceiling)
        actual_added = new_total - self._available_keys
        self._available_keys = new_total
        self._total_replenished += actual_added

        self._last_replenish_time = simulation_time
        self._simulation_time = simulation_time

        if actual_added > 0:
            logger.debug(
                "Key buffer replenished: +%d keys (total=%d) at t=%.2f",
                actual_added,
                self._available_keys,
                simulation_time,
            )

        return actual_added

    def try_acquire(self, simulation_time: float, keys_needed: int | None = None) -> KeyAcquisitionResult:
        """Attempt to acquire keys from the buffer for a transfer.

        First advances the clock to *simulation_time* (triggering
        replenishment), then checks whether sufficient keys are available.

        Parameters
        ----------
        simulation_time:
            Current virtual clock time.
        keys_needed:
            Number of keys to consume; defaults to ``config.keys_per_transfer``.

        Returns
        -------
        KeyAcquisitionResult
            SUCCESS if keys were consumed, INSUFFICIENT_KEYS/BUFFER_EMPTY otherwise.
            ``wait_time_seconds`` indicates how long (virtual) the caller must
            wait before retrying.
        """
        self.advance_time(simulation_time)
        needed = keys_needed if keys_needed is not None else self._config.keys_per_transfer

        if self._available_keys >= needed:
            self._available_keys -= needed
            self._total_consumed += needed
            logger.debug(
                "Key buffer: acquired %d key(s) at t=%.2f (remaining=%d)",
                needed,
                simulation_time,
                self._available_keys,
            )
            return KeyAcquisitionResult(
                status=KeyAcquisitionStatus.SUCCESS,
                keys_consumed=needed,
                keys_remaining=self._available_keys,
                wait_time_seconds=0.0,
                simulation_time=simulation_time,
            )

        # Not enough keys — compute wait time for replenishment
        self._depletion_events += 1
        deficit = needed - self._available_keys
        wait_seconds = deficit / self._config.generation_rate_keys_per_second if self._config.generation_rate_keys_per_second > 0 else float("inf")

        status = KeyAcquisitionStatus.BUFFER_EMPTY if self._available_keys == 0 else KeyAcquisitionStatus.INSUFFICIENT_KEYS

        logger.debug(
            "Key buffer depleted (have=%d, need=%d). Wait=%.2fs at t=%.2f",
            self._available_keys,
            needed,
            wait_seconds,
            simulation_time,
        )

        return KeyAcquisitionResult(
            status=status,
            keys_consumed=0,
            keys_remaining=self._available_keys,
            wait_time_seconds=wait_seconds,
            simulation_time=simulation_time,
        )

    def release(self, keys: int, simulation_time: float) -> None:
        """Return *keys* to the buffer (e.g. on transfer abort/retry).

        Parameters
        ----------
        keys:
            Number of keys to return.
        simulation_time:
            Current virtual clock time.
        """
        self.advance_time(simulation_time)
        returned = min(keys, self._config.buffer_ceiling - self._available_keys)
        self._available_keys += returned
        logger.debug(
            "Key buffer: returned %d key(s) at t=%.2f (total=%d)",
            returned,
            simulation_time,
            self._available_keys,
        )

    def snapshot(self) -> KeyBufferStats:
        """Return an immutable snapshot of current buffer state."""
        return KeyBufferStats(
            source_id=self._config.source_id,
            destination_id=self._config.destination_id,
            available_keys=self._available_keys,
            buffer_ceiling=self._config.buffer_ceiling,
            generation_rate_keys_per_second=self._config.generation_rate_keys_per_second,
            keys_per_transfer=self._config.keys_per_transfer,
            total_consumed=self._total_consumed,
            total_replenished=self._total_replenished,
            simulation_time=self._simulation_time,
            depletion_events=self._depletion_events,
        )

    def reset(self) -> None:
        """Reset buffer to its initial configuration state."""
        self._available_keys = self._config.initial_keys
        self._fractional_keys = 0.0
        self._simulation_time = 0.0
        self._last_replenish_time = 0.0
        self._total_consumed = 0
        self._total_replenished = 0
        self._depletion_events = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KeyResourceModel:
        """Construct from a plain dictionary (e.g. loaded from YAML)."""
        cfg = KeyResourceConfig(
            source_id=data["source_id"],
            destination_id=data["destination_id"],
            initial_keys=int(data.get("initial_keys", 1000)),
            buffer_ceiling=int(data.get("buffer_ceiling", 2000)),
            generation_rate_keys_per_second=float(data.get("generation_rate_keys_per_second", 10.0)),
            keys_per_transfer=int(data.get("keys_per_transfer", 1)),
            seed=data.get("seed"),
        )
        return cls(cfg)


__all__ = [
    "KeyAcquisitionResult",
    "KeyAcquisitionStatus",
    "KeyBufferStats",
    "KeyResourceConfig",
    "KeyResourceModel",
]
