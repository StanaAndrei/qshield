"""Scenario definitions and YAML loading utilities for Q-SHIELD simulation (TASK-3.3).

Provides typed scenario configuration objects and a loader that reads
``configs/scenarios/*.yaml`` files into structured Python objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from simulation.network_model import NetworkConfig
from simulation.workload_generator import WorkloadConfig


@dataclass
class KeyResourceScenarioConfig:
    """Key buffer parameters embedded in a scenario YAML."""

    source_id: str = "univ-lab"
    destination_id: str = "poly-clinic"
    initial_keys: int = 1000
    buffer_ceiling: int = 2000
    generation_rate_keys_per_second: float = 10.0
    keys_per_transfer: int = 1


@dataclass
class SimulationScenario:
    """Complete scenario specification for one simulation run.

    Parameters
    ----------
    name:
        Human-readable scenario identifier.
    description:
        Freeform text description of the scenario's purpose.
    seed:
        Global random seed for the scenario.
    workload:
        Workload generator configuration.
    network:
        Network link configuration.
    key_resource:
        Key buffer configuration.
    policies:
        List of scheduling policy names to evaluate.
    extra:
        Any additional raw key-value parameters from the YAML.
    """

    name: str
    description: str
    seed: int
    workload: WorkloadConfig
    network: NetworkConfig
    key_resource: KeyResourceScenarioConfig
    policies: list[str] = field(default_factory=lambda: ["FIFO", "PRIORITY", "RESOURCE_AWARE_HEURISTIC"])
    extra: dict[str, Any] = field(default_factory=dict)


def load_scenario(path: str | Path) -> SimulationScenario:
    """Load a ``SimulationScenario`` from a YAML file.

    Parameters
    ----------
    path:
        Path to the scenario YAML file.

    Returns
    -------
    SimulationScenario
        Fully populated scenario configuration object.
    """
    path = Path(path)
    with path.open() as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)

    seed = int(raw.get("seed", 42))
    network_raw = raw.get("network", {})
    key_raw = raw.get("key_resource", {})

    network_cfg = NetworkConfig(
        source_id=network_raw.get("source_id", "univ-lab"),
        destination_id=network_raw.get("destination_id", "poly-clinic"),
        baseline_throughput_mbps=float(network_raw.get("baseline_throughput_mbps", network_raw.get("throughput_mbps", 100.0))),
        throughput_std_mbps=float(network_raw.get("throughput_std_mbps", 10.0)),
        rtt_ms=float(network_raw.get("rtt_ms", network_raw.get("round_trip_time_ms", 10.0))),
        packet_drop_probability=float(network_raw.get("packet_drop_probability", network_raw.get("packet_loss_rate", 0.001))),
        min_throughput_mbps=float(network_raw.get("min_throughput_mbps", 1.0)),
        seed=seed,
    )

    key_cfg = KeyResourceScenarioConfig(
        source_id=network_raw.get("source_id", "univ-lab"),
        destination_id=network_raw.get("destination_id", "poly-clinic"),
        initial_keys=int(key_raw.get("initial_keys", 1000)),
        buffer_ceiling=int(key_raw.get("buffer_ceiling", 2000)),
        generation_rate_keys_per_second=float(key_raw.get("generation_rate_keys_per_second", 10.0)),
        keys_per_transfer=int(key_raw.get("keys_per_transfer", 1)),
    )

    # Build workload config
    priority_dist = raw.get("priority_distribution", {})
    category_dist = raw.get("category_distribution", {})
    size_dist = raw.get("data_size_distribution", {})
    deadline_cfg = raw.get("deadline", {})

    default_priority_weights: dict[str, float] = {"LOW": 0.20, "NORMAL": 0.40, "HIGH": 0.30, "CRITICAL": 0.10}
    priority_weights = {k: float(v) for k, v in priority_dist.items()} if priority_dist else default_priority_weights

    default_category_weights: dict[str, float] = {
        "DIAGNOSTIC_REPORT": 0.40,
        "MEDICAL_IMAGE": 0.30,
        "RESEARCH_DATASET": 0.20,
        "TELEMETRY_LOG": 0.10,
    }
    category_weights = {k: float(v) for k, v in category_dist.items()} if category_dist else default_category_weights

    workload_cfg = WorkloadConfig(
        source_id=network_raw.get("source_id", "univ-lab"),
        destination_id=network_raw.get("destination_id", "poly-clinic"),
        total_requests=int(raw.get("workload_size", raw.get("total_requests", 100))),
        seed=seed,
        arrival_rate_per_second=float(raw.get("arrival_rate_per_second", 0.5)),
        priority_weights=priority_weights,
        category_weights=category_weights,
        pareto_alpha=float(size_dist.get("pareto_alpha", 1.5)),
        global_size_min_bytes=int(size_dist["min_bytes"]) if "min_bytes" in size_dist else None,
        global_size_max_bytes=int(size_dist["max_bytes"]) if "max_bytes" in size_dist else None,
        deadline_fraction=float(deadline_cfg.get("fraction_with_deadline", 0.60)),
        deadline_slack_multiplier_min=float(deadline_cfg.get("slack_multiplier_min", 1.0)),
        deadline_slack_multiplier_max=float(deadline_cfg.get("slack_multiplier_max", 5.0)),
        network_throughput_mbps=float(network_raw.get("throughput_mbps", network_raw.get("baseline_throughput_mbps", 100.0))),
    )

    policies: list[str] = raw.get("policies", ["FIFO", "PRIORITY", "RESOURCE_AWARE_HEURISTIC"])

    return SimulationScenario(
        name=str(raw.get("scenario_name", path.stem)),
        description=str(raw.get("description", "")),
        seed=seed,
        workload=workload_cfg,
        network=network_cfg,
        key_resource=key_cfg,
        policies=policies,
        extra=raw,
    )


__all__ = [
    "KeyResourceScenarioConfig",
    "SimulationScenario",
    "load_scenario",
]
