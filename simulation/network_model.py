"""Network variability and latency model for Q-SHIELD simulation (TASK-3.1).

Models configurable baseline throughput with Gaussian variability,
RTT propagation delay, and stochastic packet drop events.

Deterministic output is guaranteed when instantiated with a fixed seed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NetworkConfig:
    """Configuration parameters for a simulated network link."""

    source_id: str
    destination_id: str
    baseline_throughput_mbps: float = 100.0
    """Mean throughput in Mbit/s when link is healthy."""

    throughput_std_mbps: float = 10.0
    """Standard deviation of Gaussian throughput variability in Mbit/s."""

    rtt_ms: float = 10.0
    """Round-trip-time propagation delay in milliseconds."""

    packet_drop_probability: float = 0.001
    """Per-transfer probability of a stochastic packet-drop event."""

    min_throughput_mbps: float = 1.0
    """Hard floor: throughput never drops below this value."""

    seed: int | None = None
    """Optional fixed random seed for reproducible sampling."""


@dataclass
class NetworkSample:
    """A single sampled network state snapshot."""

    source_id: str
    destination_id: str
    throughput_mbps: float
    """Effective throughput for this simulation step."""

    rtt_ms: float
    """Round-trip time in milliseconds."""

    packet_drop_occurred: bool
    """Whether a stochastic packet-drop event was sampled."""

    effective_throughput_mbps: float
    """Throughput after applying drop penalty (may be reduced)."""

    simulation_time: float = 0.0
    """Virtual simulation clock time at which this sample was taken."""


class NetworkModel:
    """Stochastic network link model for discrete-event simulation.

    Supports:
    - Configurable baseline throughput with Gaussian variability.
    - RTT propagation delay (fixed per link, applied to completion timing).
    - Stochastic packet-drop events that reduce effective throughput.
    - Deterministic output when instantiated with a fixed ``seed``.

    Parameters
    ----------
    config:
        ``NetworkConfig`` describing the link characteristics.
    """

    def __init__(self, config: NetworkConfig) -> None:
        self._config = config
        # Isolate random state so this model does not affect other RNG streams
        self._rng = random.Random(config.seed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def config(self) -> NetworkConfig:
        """Return the link configuration (read-only)."""
        return self._config

    @property
    def link_key(self) -> str:
        """Canonical key for this link: ``'<src>-><dst>'``."""
        return f"{self._config.source_id}->{self._config.destination_id}"

    def sample(self, simulation_time: float = 0.0) -> NetworkSample:
        """Draw a stochastic network state sample.

        Throughput is drawn from ``Normal(baseline, std)`` then clipped to
        ``[min_throughput, +inf)``.  A packet-drop event is drawn
        independently from a Bernoulli distribution and further reduces the
        effective throughput by 50 % if it occurs.

        Parameters
        ----------
        simulation_time:
            Current virtual clock time (seconds) for tagging the sample.

        Returns
        -------
        NetworkSample
            Immutable snapshot of sampled link state.
        """
        cfg = self._config

        # Gaussian throughput sample
        raw_throughput = self._rng.gauss(
            cfg.baseline_throughput_mbps,
            cfg.throughput_std_mbps,
        )
        throughput_mbps = max(cfg.min_throughput_mbps, raw_throughput)

        # Stochastic packet-drop event
        packet_drop = self._rng.random() < cfg.packet_drop_probability
        effective_throughput = throughput_mbps * 0.5 if packet_drop else throughput_mbps

        return NetworkSample(
            source_id=cfg.source_id,
            destination_id=cfg.destination_id,
            throughput_mbps=throughput_mbps,
            rtt_ms=cfg.rtt_ms,
            packet_drop_occurred=packet_drop,
            effective_throughput_mbps=effective_throughput,
            simulation_time=simulation_time,
        )

    def transfer_duration_seconds(self, data_size_bytes: int, simulation_time: float = 0.0) -> float:
        """Estimate transfer duration including RTT overhead.

        Duration = (size_bits / effective_throughput_bps) + rtt_seconds

        Parameters
        ----------
        data_size_bytes:
            Payload size in bytes.
        simulation_time:
            Virtual clock time for tagging the internal sample.

        Returns
        -------
        float
            Estimated wall-clock seconds (in virtual time) for the transfer.
        """
        sample = self.sample(simulation_time)
        effective_bps = sample.effective_throughput_mbps * 1_000_000.0
        transfer_sec = (data_size_bytes * 8.0) / effective_bps
        rtt_sec = self._config.rtt_ms / 1_000.0
        return transfer_sec + rtt_sec

    def reset(self, seed: int | None = None) -> None:
        """Reset the internal RNG state.

        Parameters
        ----------
        seed:
            New seed value; if ``None`` reuses the original configured seed.
        """
        new_seed = seed if seed is not None else self._config.seed
        self._rng = random.Random(new_seed)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NetworkModel:
        """Construct a ``NetworkModel`` from a plain dictionary.

        Useful for loading scenario configurations directly from YAML.

        Parameters
        ----------
        data:
            Dictionary with keys matching ``NetworkConfig`` field names.
        """
        cfg = NetworkConfig(
            source_id=data["source_id"],
            destination_id=data["destination_id"],
            baseline_throughput_mbps=float(data.get("baseline_throughput_mbps", data.get("throughput_mbps", 100.0))),
            throughput_std_mbps=float(data.get("throughput_std_mbps", 10.0)),
            rtt_ms=float(data.get("rtt_ms", data.get("round_trip_time_ms", 10.0))),
            packet_drop_probability=float(data.get("packet_drop_probability", data.get("packet_loss_rate", 0.001))),
            min_throughput_mbps=float(data.get("min_throughput_mbps", 1.0)),
            seed=data.get("seed"),
        )
        return cls(cfg)


# ---------------------------------------------------------------------------
# Multi-link registry helper
# ---------------------------------------------------------------------------


@dataclass
class NetworkModelRegistry:
    """Container for multiple ``NetworkModel`` instances, keyed by link id.

    Provides a convenient lookup API used by the simulation engine.
    """

    _models: dict[str, NetworkModel] = field(default_factory=dict)

    def register(self, model: NetworkModel) -> None:
        """Register a network model under its canonical link key."""
        self._models[model.link_key] = model

    def get(self, source_id: str, destination_id: str) -> NetworkModel | None:
        """Retrieve model by source and destination identifiers."""
        return self._models.get(f"{source_id}->{destination_id}")

    def get_or_default(
        self,
        source_id: str,
        destination_id: str,
        default_throughput_mbps: float = 100.0,
        seed: int | None = None,
    ) -> NetworkModel:
        """Return the registered model, or a default one if not found."""
        model = self.get(source_id, destination_id)
        if model is None:
            cfg = NetworkConfig(
                source_id=source_id,
                destination_id=destination_id,
                baseline_throughput_mbps=default_throughput_mbps,
                seed=seed,
            )
            model = NetworkModel(cfg)
        return model

    def __len__(self) -> int:
        return len(self._models)


__all__ = [
    "NetworkConfig",
    "NetworkModel",
    "NetworkModelRegistry",
    "NetworkSample",
]
