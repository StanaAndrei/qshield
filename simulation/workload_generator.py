"""Synthetic medical workload generator for Q-SHIELD simulation (TASK-3.3).

Generates transfer requests using:
- Poisson inter-arrival times (λ = arrival_rate_per_second)
- Pareto payload size distribution (heavy-tailed, realistic medical data)
- Realistic medical categories with size profiles
- Configurable priority distributions
- Deterministic output for identical seeds
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.enums import DataCategory, PriorityLevel, SecurityProfile, TransferStatus
from app.models.transfer_request import TransferRequest

# ---------------------------------------------------------------------------
# Medical workload category profiles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MedicalProfile:
    """Size and timing constraints for a medical data category."""

    category: DataCategory
    typical_size_bytes: int
    """Representative median size in bytes."""

    size_min_bytes: int
    size_max_bytes: int
    deadline_seconds_min: float
    """Tightest deadline (seconds after submission)."""

    deadline_seconds_max: float
    """Loosest deadline (seconds after submission)."""

    deadline_probability: float = 0.6
    """Probability this category carries a hard deadline."""


MEDICAL_PROFILES: dict[DataCategory, MedicalProfile] = {
    DataCategory.DIAGNOSTIC_REPORT: MedicalProfile(
        category=DataCategory.DIAGNOSTIC_REPORT,
        typical_size_bytes=5 * 1024 * 1024,  # 5 MB
        size_min_bytes=512 * 1024,  # 512 KB
        size_max_bytes=20 * 1024 * 1024,  # 20 MB
        deadline_seconds_min=30.0,
        deadline_seconds_max=300.0,
        deadline_probability=0.85,
    ),
    DataCategory.MEDICAL_IMAGE: MedicalProfile(
        category=DataCategory.MEDICAL_IMAGE,
        typical_size_bytes=2 * 1024 * 1024 * 1024,  # 2 GB
        size_min_bytes=100 * 1024 * 1024,  # 100 MB
        size_max_bytes=5 * 1024 * 1024 * 1024,  # 5 GB
        deadline_seconds_min=300.0,
        deadline_seconds_max=1800.0,
        deadline_probability=0.6,
    ),
    DataCategory.RESEARCH_DATASET: MedicalProfile(
        category=DataCategory.RESEARCH_DATASET,
        typical_size_bytes=500 * 1024 * 1024,  # 500 MB
        size_min_bytes=10 * 1024 * 1024,  # 10 MB
        size_max_bytes=100 * 1024 * 1024 * 1024,  # 100 GB
        deadline_seconds_min=3600.0,
        deadline_seconds_max=86400.0,
        deadline_probability=0.3,
    ),
    DataCategory.TELEMETRY_LOG: MedicalProfile(
        category=DataCategory.TELEMETRY_LOG,
        typical_size_bytes=1 * 1024 * 1024,  # 1 MB
        size_min_bytes=10 * 1024,
        size_max_bytes=50 * 1024 * 1024,
        deadline_seconds_min=60.0,
        deadline_seconds_max=600.0,
        deadline_probability=0.4,
    ),
}


# ---------------------------------------------------------------------------
# Workload generator configuration
# ---------------------------------------------------------------------------

@dataclass
class WorkloadConfig:
    """Configuration for synthetic workload generation."""

    source_id: str = "univ-lab"
    destination_id: str = "poly-clinic"
    total_requests: int = 100

    seed: int = 42

    arrival_rate_per_second: float = 0.5
    """Poisson process rate: average arrivals per virtual second."""

    # Priority weights (must sum to 1.0)
    priority_weights: dict[str, float] = field(default_factory=lambda: {
        "LOW": 0.20,
        "NORMAL": 0.40,
        "HIGH": 0.30,
        "CRITICAL": 0.10,
    })

    # Category weights (must sum to 1.0)
    category_weights: dict[str, float] = field(default_factory=lambda: {
        "DIAGNOSTIC_REPORT": 0.40,
        "MEDICAL_IMAGE": 0.30,
        "RESEARCH_DATASET": 0.20,
        "TELEMETRY_LOG": 0.10,
    })

    # Pareto distribution parameters for size sampling
    pareto_alpha: float = 1.5
    """Shape parameter for Pareto heavy-tail distribution."""

    # Size distribution overrides (if set, overrides per-category profiles)
    global_size_min_bytes: int | None = None
    global_size_max_bytes: int | None = None

    # Deadline configuration
    deadline_fraction: float = 0.60
    """Fraction of requests that carry a hard deadline (overrides per-category)."""

    deadline_slack_multiplier_min: float = 1.0
    deadline_slack_multiplier_max: float = 5.0

    network_throughput_mbps: float = 100.0
    """Nominal network throughput used for expected-duration computation."""

    security_profile: SecurityProfile = SecurityProfile.QKD_AES_256_GCM


@dataclass
class WorkloadRequest:
    """A generated workload entry with virtual-time arrival."""

    request: TransferRequest
    arrival_time: float
    """Virtual simulation time (seconds) at which this request arrives."""

    expected_duration_seconds: float
    """Analytical estimate: size_bits / throughput_bps."""


# ---------------------------------------------------------------------------
# Workload generator
# ---------------------------------------------------------------------------


class WorkloadGenerator:
    """Synthetic medical transfer workload generator.

    Produces deterministic, reproducible request sets when given the same
    ``seed`` value.  Inter-arrival times follow an exponential distribution
    (consistent with Poisson process); payload sizes use a Pareto distribution
    to model the heavy-tailed nature of medical data.

    Parameters
    ----------
    config:
        ``WorkloadConfig`` specifying generation parameters.
    """

    def __init__(self, config: WorkloadConfig) -> None:
        self._config = config
        self._rng = random.Random(config.seed)

    @property
    def config(self) -> WorkloadConfig:
        return self._config

    # ------------------------------------------------------------------
    # Public generation API
    # ------------------------------------------------------------------

    def generate(self) -> list[WorkloadRequest]:
        """Generate the full workload defined by ``config.total_requests``.

        Returns
        -------
        list[WorkloadRequest]
            Requests in ascending arrival-time order. Identical seeds produce
            bit-for-bit identical output.
        """
        cfg = self._config

        # Reinitialise RNG to guarantee reproducibility regardless of previous calls
        self._rng = random.Random(cfg.seed)

        requests: list[WorkloadRequest] = []
        virtual_time: float = 0.0
        base_time = datetime(2026, 1, 1, tzinfo=UTC)

        priorities = [PriorityLevel[k] for k in cfg.priority_weights]
        priority_weights = [cfg.priority_weights[k.name] for k in priorities]

        categories = [DataCategory[k] for k in cfg.category_weights]
        category_weights_list = [cfg.category_weights[k.name] for k in categories]

        for _ in range(cfg.total_requests):
            # Poisson inter-arrival: exponentially distributed gap
            inter_arrival = self._rng.expovariate(cfg.arrival_rate_per_second)
            virtual_time += inter_arrival

            # Sample category and priority
            category = self._rng.choices(categories, weights=category_weights_list, k=1)[0]
            priority = self._rng.choices(priorities, weights=priority_weights, k=1)[0]

            # Sample payload size
            data_size_bytes = self._sample_size(category)

            # Compute expected duration
            throughput_bps = cfg.network_throughput_mbps * 1_000_000.0
            expected_duration = (data_size_bytes * 8.0) / throughput_bps

            # Sample deadline
            deadline = self._sample_deadline(expected_duration, category, virtual_time, base_time)

            # Build request
            submitted_at = base_time + timedelta(seconds=virtual_time)
            request = TransferRequest(
                request_id=self._deterministic_uuid(virtual_time, _),
                source_id=cfg.source_id,
                destination_id=cfg.destination_id,
                data_size_bytes=data_size_bytes,
                priority=priority,
                submitted_at=submitted_at,
                deadline=deadline,
                security_profile=cfg.security_profile,
                data_category=category,
                status=TransferStatus.QUEUED,
                metadata={
                    "workload_index": _,
                    "arrival_time": virtual_time,
                },
            )

            requests.append(WorkloadRequest(
                request=request,
                arrival_time=virtual_time,
                expected_duration_seconds=expected_duration,
            ))

        return requests

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _sample_size(self, category: DataCategory) -> int:
        """Sample payload size for the given category using Pareto distribution."""
        cfg = self._config
        profile = MEDICAL_PROFILES[category]

        # Determine range
        size_min = cfg.global_size_min_bytes if cfg.global_size_min_bytes is not None else profile.size_min_bytes
        size_max = cfg.global_size_max_bytes if cfg.global_size_max_bytes is not None else profile.size_max_bytes

        # Pareto variate: x_min * U^(-1/alpha) where U ~ Uniform(0,1)
        # Clipped to [size_min, size_max]
        u = self._rng.random()
        # Avoid u==0 to prevent division by zero
        u = max(u, 1e-9)
        pareto_sample = size_min * (u ** (-1.0 / cfg.pareto_alpha))
        return int(min(max(pareto_sample, size_min), size_max))

    def _sample_deadline(
        self,
        expected_duration: float,
        category: DataCategory,
        arrival_time: float,
        base_time: datetime,
    ) -> datetime | None:
        """Sample a deadline, or return None for best-effort requests."""
        cfg = self._config
        profile = MEDICAL_PROFILES[category]

        has_deadline = self._rng.random() < cfg.deadline_fraction

        if not has_deadline:
            return None

        # Deadline slack is a multiplier on the expected transfer duration
        slack_mult = self._rng.uniform(
            cfg.deadline_slack_multiplier_min,
            cfg.deadline_slack_multiplier_max,
        )
        deadline_seconds = expected_duration * slack_mult

        # Clamp to category-defined bounds
        deadline_seconds = max(profile.deadline_seconds_min, deadline_seconds)
        deadline_seconds = min(profile.deadline_seconds_max, deadline_seconds)

        return base_time + timedelta(seconds=arrival_time + deadline_seconds)

    def _deterministic_uuid(self, virtual_time: float, index: int) -> UUID:
        """Generate a deterministic UUID from seed, time and index."""
        import hashlib
        import struct
        digest = hashlib.md5(
            struct.pack(">dI", virtual_time, index) + str(self._config.seed).encode(),
            usedforsecurity=False,
        ).digest()
        return UUID(bytes=digest)

    @classmethod
    def from_config_dict(cls, data: dict[str, Any]) -> WorkloadGenerator:
        """Construct from a plain dictionary (e.g. loaded from YAML)."""
        network = data.get("network", {})
        priority_dist = data.get("priority_distribution", {})
        category_dist = data.get("category_distribution", {})
        size_dist = data.get("data_size_distribution", {})
        deadline_cfg = data.get("deadline", {})

        # Normalise priority weights
        default_priority_weights = {"LOW": 0.20, "NORMAL": 0.40, "HIGH": 0.30, "CRITICAL": 0.10}
        priority_weights = {k: float(v) for k, v in priority_dist.items()} if priority_dist else default_priority_weights

        default_category_weights = {
            "DIAGNOSTIC_REPORT": 0.40,
            "MEDICAL_IMAGE": 0.30,
            "RESEARCH_DATASET": 0.20,
            "TELEMETRY_LOG": 0.10,
        }
        category_weights = {k: float(v) for k, v in category_dist.items()} if category_dist else default_category_weights

        cfg = WorkloadConfig(
            source_id=network.get("source_id", data.get("source_id", "univ-lab")),
            destination_id=network.get("destination_id", data.get("destination_id", "poly-clinic")),
            total_requests=int(data.get("workload_size", data.get("total_requests", 100))),
            seed=int(data.get("seed", 42)),
            arrival_rate_per_second=float(data.get("arrival_rate_per_second", 0.5)),
            priority_weights=priority_weights,
            category_weights=category_weights,
            pareto_alpha=float(size_dist.get("pareto_alpha", 1.5)),
            global_size_min_bytes=int(size_dist["min_bytes"]) if "min_bytes" in size_dist else None,
            global_size_max_bytes=int(size_dist["max_bytes"]) if "max_bytes" in size_dist else None,
            deadline_fraction=float(deadline_cfg.get("fraction_with_deadline", 0.60)),
            deadline_slack_multiplier_min=float(deadline_cfg.get("slack_multiplier_min", 1.0)),
            deadline_slack_multiplier_max=float(deadline_cfg.get("slack_multiplier_max", 5.0)),
            network_throughput_mbps=float(network.get("throughput_mbps", 100.0)),
        )
        return cls(cfg)


__all__ = [
    "MEDICAL_PROFILES",
    "MedicalProfile",
    "WorkloadConfig",
    "WorkloadGenerator",
    "WorkloadRequest",
]
