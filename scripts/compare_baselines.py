#!/usr/bin/env python3
"""Baseline Policy Comparison CLI Tool for Q-SHIELD (TASK-2.6).

Simulates a configurable workload queue under FIFO, Priority, and
Resource-Aware Heuristic scheduling policies and prints a formatted
comparison table covering:

  • Mean scheduling latency (ms)
  • Deadline miss count / deadline miss rate
  • Max starvation wait time (s)
  • Total requests scheduled

Usage
-----
# Using a workload YAML file:
    python scripts/compare_baselines.py \\
        --workload configs/scenarios/baseline_test.yaml

# Inline parameters (override or supplement YAML):
    python scripts/compare_baselines.py \\
        --workload-size 100 \\
        --seed 42 \\
        --policies FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC

Both ``--workload`` and inline flags may be combined; inline flags take
precedence over YAML values.

DoD (TASK-2.6):
    ``python scripts/compare_baselines.py --workload configs/scenarios/baseline_test.yaml``
    executes and prints comparison summary.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path regardless of invocation directory.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import yaml  # noqa: E402  (after sys.path manipulation)

from app.core.enums import (  # noqa: E402
    DataCategory,
    PriorityLevel,
    SchedulingPolicyName,
    SecurityProfile,
    TransferStatus,
)
from app.models.telemetry import NetworkTelemetry, TelemetrySnapshot  # noqa: E402
from app.models.transfer_request import TransferRequest  # noqa: E402
from app.services.scheduling_service import SchedulingService  # noqa: E402
from tests.fixtures.mock_repository import InMemoryTransferRepository  # noqa: E402

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PRIORITY_VALUES: list[tuple[PriorityLevel, float]] = [
    (PriorityLevel.LOW, 0.20),
    (PriorityLevel.NORMAL, 0.40),
    (PriorityLevel.HIGH, 0.30),
    (PriorityLevel.CRITICAL, 0.10),
]

_DEFAULT_THROUGHPUT_MBPS = 100.0
_DATA_CATEGORIES = list(DataCategory)
_SECURITY_PROFILES = [SecurityProfile.QKD_AES_256_GCM]


# ---------------------------------------------------------------------------
# Workload generation
# ---------------------------------------------------------------------------


def _weighted_choice(choices: list[tuple[Any, float]], rng: random.Random) -> Any:
    """Select an item from (value, weight) pairs using the provided RNG."""
    population, weights = zip(*choices, strict=False)
    return rng.choices(list(population), weights=list(weights), k=1)[0]


def _pareto_sample(rng: random.Random, min_bytes: int, max_bytes: int, alpha: float) -> int:
    """Sample a Pareto-distributed integer payload size."""
    # Use inverse CDF: x = x_m / u^(1/alpha)
    u = rng.random()
    value = min_bytes / (u ** (1.0 / alpha))
    return int(min(max(value, min_bytes), max_bytes))


def generate_workload(
    *,
    n: int,
    seed: int,
    source_id: str = "univ-lab",
    destination_id: str = "poly-clinic",
    throughput_mbps: float = _DEFAULT_THROUGHPUT_MBPS,
    priority_distribution: list[tuple[PriorityLevel, float]] | None = None,
    min_bytes: int = 1_000_000,
    max_bytes: int = 500_000_000,
    pareto_alpha: float = 1.5,
    fraction_with_deadline: float = 0.60,
    slack_multiplier_min: float = 1.0,
    slack_multiplier_max: float = 5.0,
    submission_window_seconds: float = 300.0,
) -> list[TransferRequest]:
    """Generate a reproducible synthetic workload of *n* transfer requests.

    Parameters
    ----------
    n:
        Number of requests to generate.
    seed:
        Random seed for full reproducibility.
    source_id / destination_id:
        Logical node identifiers for all requests.
    throughput_mbps:
        Assumed link throughput used to compute realistic deadlines.
    priority_distribution:
        List of ``(PriorityLevel, weight)`` pairs.  Defaults to the
        distribution specified in ``configs/scenarios/baseline_test.yaml``.
    min_bytes / max_bytes:
        Payload size bounds for the Pareto sampler.
    pareto_alpha:
        Shape parameter for the Pareto payload distribution.
    fraction_with_deadline:
        Fraction of requests that carry a hard deadline (0.0–1.0).
    slack_multiplier_min / slack_multiplier_max:
        Deadline slack as a multiplier on the analytical transfer duration.
    submission_window_seconds:
        Requests are staggered uniformly across this time window.

    Returns
    -------
    list[TransferRequest]
        Deterministically generated requests in submission order.
    """
    rng = random.Random(seed)
    if priority_distribution is None:
        priority_distribution = _PRIORITY_VALUES

    base_time = datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
    requests: list[TransferRequest] = []

    # Pre-generate submission offsets sorted ascending to simulate arrivals
    offsets = sorted(rng.uniform(0.0, submission_window_seconds) for _ in range(n))

    for i, offset in enumerate(offsets):
        priority = _weighted_choice(priority_distribution, rng)
        data_size = _pareto_sample(rng, min_bytes, max_bytes, pareto_alpha)
        data_category = rng.choice(_DATA_CATEGORIES)
        security_profile = rng.choice(_SECURITY_PROFILES)

        submitted_at = base_time + timedelta(seconds=offset)

        # Optionally assign a deadline
        deadline: datetime | None = None
        if rng.random() < fraction_with_deadline:
            # Analytical transfer duration in seconds
            analytical_duration = (data_size * 8.0) / (throughput_mbps * 1_000_000.0)
            slack_mult = rng.uniform(slack_multiplier_min, slack_multiplier_max)
            deadline = submitted_at + timedelta(seconds=analytical_duration * slack_mult)

        requests.append(
            TransferRequest(
                request_id=uuid4(),
                source_id=source_id,
                destination_id=destination_id,
                data_size_bytes=data_size,
                priority=priority,
                submitted_at=submitted_at,
                deadline=deadline,
                security_profile=security_profile,
                data_category=data_category,
                status=TransferStatus.QUEUED,
            )
        )

    return requests


# ---------------------------------------------------------------------------
# Policy simulation runner
# ---------------------------------------------------------------------------


class _PolicyResult:
    """Aggregated scheduling metrics for a single policy run."""

    def __init__(self, policy: SchedulingPolicyName) -> None:
        self.policy = policy
        self.scheduling_latencies_ms: list[float] = []
        self.deadline_miss_count: int = 0
        self.starvation_wait_seconds: list[float] = []
        self.total_scheduled: int = 0
        self.total_with_deadlines: int = 0

    # Computed aggregates
    @property
    def mean_latency_ms(self) -> float:
        return sum(self.scheduling_latencies_ms) / len(self.scheduling_latencies_ms) if self.scheduling_latencies_ms else 0.0

    @property
    def max_starvation_seconds(self) -> float:
        return max(self.starvation_wait_seconds) if self.starvation_wait_seconds else 0.0

    @property
    def deadline_miss_rate(self) -> float:
        if self.total_with_deadlines == 0:
            return 0.0
        return self.deadline_miss_count / self.total_with_deadlines


def _build_telemetry(
    source_id: str,
    destination_id: str,
    throughput_mbps: float,
) -> TelemetrySnapshot:
    link_key = f"{source_id}->{destination_id}"
    return TelemetrySnapshot(
        timestamp=datetime.now(UTC),
        network_metrics={
            link_key: NetworkTelemetry(
                source_id=source_id,
                destination_id=destination_id,
                observed_throughput_mbps=throughput_mbps,
                packet_loss_rate=0.001,
                round_trip_time_ms=10.0,
            )
        },
    )


def run_policy(
    policy: SchedulingPolicyName,
    requests: list[TransferRequest],
    throughput_mbps: float,
    heuristic_overrides: dict[str, Any] | None = None,
) -> _PolicyResult:
    """Simulate a full scheduling run for *policy* over *requests*.

    Parameters
    ----------
    policy:
        Scheduling policy to simulate.
    requests:
        Pre-generated workload (all in QUEUED status).
    throughput_mbps:
        Simulated link throughput for telemetry and latency estimation.
    heuristic_overrides:
        Optional kwarg overrides forwarded to ``ResourceAwareHeuristicScheduler``.

    Returns
    -------
    _PolicyResult
        Aggregated metrics collected during the scheduling run.
    """
    result = _PolicyResult(policy)
    repo = InMemoryTransferRepository()

    # Populate repository with fresh copies in QUEUED state.
    for req in requests:
        copy = req.model_copy(deep=True)
        copy.status = TransferStatus.QUEUED
        repo.create_request(copy)
        if copy.deadline is not None:
            result.total_with_deadlines += 1

    source_id = requests[0].source_id if requests else "univ-lab"
    destination_id = requests[0].destination_id if requests else "poly-clinic"
    telemetry = _build_telemetry(source_id, destination_id, throughput_mbps)

    service = SchedulingService(
        repository=repo,
        policy=policy,
        telemetry_provider=lambda: telemetry,
        scheduler_overrides=heuristic_overrides or {},
    )

    # Run scheduling cycles until the queue is drained.
    n = len(requests)
    for _ in range(n):
        t0 = time.perf_counter_ns()
        decision = service.run_scheduling_cycle()
        elapsed_ms = (time.perf_counter_ns() - t0) / 1_000_000.0

        if decision is None:
            break  # Queue exhausted

        result.total_scheduled += 1
        result.scheduling_latencies_ms.append(elapsed_ms)
        result.starvation_wait_seconds.append(decision.starvation_age_seconds)

        # Count deadline miss: check the original request's deadline
        req = repo.get_request(decision.selected_request_id)
        if req is not None and req.deadline is not None:
            if decision.predicted_deadline_miss_probability >= 0.5:
                result.deadline_miss_count += 1

    return result


# ---------------------------------------------------------------------------
# CLI argument parsing and config loading
# ---------------------------------------------------------------------------


def _load_yaml_config(path: Path) -> dict[str, Any]:
    """Load a scenario YAML file and return a flat config dict."""
    with path.open() as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid YAML config at {path}: expected a mapping")
    return raw  # type: ignore[return-value]


def _parse_priority_distribution(raw: dict[str, float]) -> list[tuple[PriorityLevel, float]]:
    mapping = {
        "LOW": PriorityLevel.LOW,
        "NORMAL": PriorityLevel.NORMAL,
        "HIGH": PriorityLevel.HIGH,
        "CRITICAL": PriorityLevel.CRITICAL,
    }
    result = []
    for name, weight in raw.items():
        level = mapping.get(name.upper())
        if level is None:
            raise ValueError(f"Unknown priority level in config: {name!r}")
        result.append((level, float(weight)))
    return result


def _parse_policy_names(raw: str) -> list[SchedulingPolicyName]:
    mapping = {
        "FIFO": SchedulingPolicyName.FIFO,
        "PRIORITY": SchedulingPolicyName.PRIORITY,
        "RESOURCE_AWARE_HEURISTIC": SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC,
    }
    result = []
    for name in raw.split(","):
        name = name.strip().upper()
        policy = mapping.get(name)
        if policy is None:
            raise ValueError(
                f"Unknown policy {name!r}. Valid options: {', '.join(mapping)}"
            )
        result.append(policy)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compare_baselines",
        description="Q-SHIELD Baseline Policy Comparison CLI (TASK-2.6)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--workload",
        metavar="YAML",
        type=Path,
        default=None,
        help="Path to scenario YAML file (e.g. configs/scenarios/baseline_test.yaml)",
    )
    parser.add_argument(
        "--workload-size",
        metavar="N",
        type=int,
        default=None,
        help="Number of requests to generate (overrides YAML value)",
    )
    parser.add_argument(
        "--seed",
        metavar="SEED",
        type=int,
        default=None,
        help="Random seed for workload generation (overrides YAML value)",
    )
    parser.add_argument(
        "--policies",
        metavar="P1,P2,...",
        type=str,
        default=None,
        help=(
            "Comma-separated list of policies to compare. "
            "Valid: FIFO, PRIORITY, RESOURCE_AWARE_HEURISTIC "
            "(default: all three)"
        ),
    )
    parser.add_argument(
        "--throughput-mbps",
        metavar="MBPS",
        type=float,
        default=None,
        help="Simulated link throughput in Mbps (overrides YAML value, default 100.0)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging output",
    )
    return parser


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _render_table(results: list[_PolicyResult]) -> str:
    """Render a formatted comparison table as a string."""
    col_policy = max(len("Policy"), max(len(r.policy.value) for r in results))
    col_scheduled = max(len("Scheduled"), 10)
    col_latency = max(len("Mean Latency (ms)"), 18)
    col_misses = max(len("Deadline Misses"), 15)
    col_miss_rate = max(len("Miss Rate"), 10)
    col_starvation = max(len("Max Starvation (s)"), 18)

    sep = (
        f"+-{'-' * col_policy}-+-{'-' * col_scheduled}-+"
        f"-{'-' * col_latency}-+-{'-' * col_misses}-+"
        f"-{'-' * col_miss_rate}-+-{'-' * col_starvation}-+"
    )
    header = (
        f"| {'Policy':<{col_policy}} | {'Scheduled':>{col_scheduled}} |"
        f" {'Mean Latency (ms)':>{col_latency}} | {'Deadline Misses':>{col_misses}} |"
        f" {'Miss Rate':>{col_miss_rate}} | {'Max Starvation (s)':>{col_starvation}} |"
    )

    rows = [sep, header, sep]
    for r in results:
        row = (
            f"| {r.policy.value:<{col_policy}} "
            f"| {r.total_scheduled:>{col_scheduled}} "
            f"| {r.mean_latency_ms:>{col_latency}.4f} "
            f"| {r.deadline_miss_count:>{col_misses}} "
            f"| {r.deadline_miss_rate:>{col_miss_rate}.2%} "
            f"| {r.max_starvation_seconds:>{col_starvation}.2f} "
            f"|"
        )
        rows.append(row)
    rows.append(sep)
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0912
    """Entry point for the compare_baselines CLI.

    Returns
    -------
    int
        Exit code (0 = success, non-zero = failure).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, force=True)

    # ----- Load YAML config (if provided) -----
    yaml_cfg: dict[str, Any] = {}
    if args.workload is not None:
        workload_path = args.workload
        if not workload_path.exists():
            print(f"ERROR: Workload file not found: {workload_path}", file=sys.stderr)
            return 2
        yaml_cfg = _load_yaml_config(workload_path)

    # ----- Resolve final config (CLI overrides YAML) -----
    workload_size: int = (
        args.workload_size
        if args.workload_size is not None
        else int(yaml_cfg.get("workload_size", 100))
    )
    seed: int = (
        args.seed
        if args.seed is not None
        else int(yaml_cfg.get("seed", 42))
    )

    # Network config
    net_cfg: dict[str, Any] = yaml_cfg.get("network", {})
    throughput_mbps: float = (
        args.throughput_mbps
        if args.throughput_mbps is not None
        else float(net_cfg.get("throughput_mbps", _DEFAULT_THROUGHPUT_MBPS))
    )
    source_id: str = net_cfg.get("source_id", "univ-lab")
    destination_id: str = net_cfg.get("destination_id", "poly-clinic")

    # Priority distribution
    pri_cfg = yaml_cfg.get("priority_distribution", {})
    priority_distribution = (
        _parse_priority_distribution(pri_cfg) if pri_cfg else None
    )

    # Data-size distribution
    ds_cfg: dict[str, Any] = yaml_cfg.get("data_size_distribution", {})
    min_bytes: int = int(ds_cfg.get("min_bytes", 1_000_000))
    max_bytes: int = int(ds_cfg.get("max_bytes", 500_000_000))
    pareto_alpha: float = float(ds_cfg.get("pareto_alpha", 1.5))

    # Deadline config
    dl_cfg: dict[str, Any] = yaml_cfg.get("deadline", {})
    fraction_with_deadline: float = float(dl_cfg.get("fraction_with_deadline", 0.60))
    slack_multiplier_min: float = float(dl_cfg.get("slack_multiplier_min", 1.0))
    slack_multiplier_max: float = float(dl_cfg.get("slack_multiplier_max", 5.0))
    submission_window: float = float(yaml_cfg.get("submission_window_seconds", 300.0))

    # Heuristic overrides
    heuristic_overrides: dict[str, Any] = dict(yaml_cfg.get("heuristic_overrides", {}))

    # Policies to compare
    if args.policies is not None:
        policies = _parse_policy_names(args.policies)
    else:
        policies = [
            SchedulingPolicyName.FIFO,
            SchedulingPolicyName.PRIORITY,
            SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC,
        ]

    # ----- Generate workload -----
    print(f"\nQ-SHIELD Baseline Policy Comparison")
    print(f"{'=' * 60}")
    print(f"Workload size : {workload_size} requests")
    print(f"Random seed   : {seed}")
    print(f"Throughput    : {throughput_mbps} Mbps")
    print(f"Source → Dest : {source_id} → {destination_id}")
    print(f"Policies      : {', '.join(p.value for p in policies)}")
    print(f"{'=' * 60}\n")

    print("Generating workload...", end=" ", flush=True)
    t_gen_start = time.perf_counter()
    workload = generate_workload(
        n=workload_size,
        seed=seed,
        source_id=source_id,
        destination_id=destination_id,
        throughput_mbps=throughput_mbps,
        priority_distribution=priority_distribution,
        min_bytes=min_bytes,
        max_bytes=max_bytes,
        pareto_alpha=pareto_alpha,
        fraction_with_deadline=fraction_with_deadline,
        slack_multiplier_min=slack_multiplier_min,
        slack_multiplier_max=slack_multiplier_max,
        submission_window_seconds=submission_window,
    )
    t_gen = time.perf_counter() - t_gen_start
    print(f"done ({len(workload)} requests in {t_gen * 1000:.1f} ms)")

    with_deadlines = sum(1 for r in workload if r.deadline is not None)
    print(f"  Priority mix : ", end="")
    for prio in [PriorityLevel.CRITICAL, PriorityLevel.HIGH, PriorityLevel.NORMAL, PriorityLevel.LOW]:
        count = sum(1 for r in workload if r.priority == prio)
        print(f"{prio.name}={count}", end="  ")
    print()
    print(f"  With deadline: {with_deadlines}/{len(workload)}")
    print()

    # ----- Run each policy -----
    all_results: list[_PolicyResult] = []
    for policy in policies:
        print(f"Running {policy.value:<30}", end=" ", flush=True)
        t0 = time.perf_counter()
        result = run_policy(
            policy=policy,
            requests=workload,
            throughput_mbps=throughput_mbps,
            heuristic_overrides=heuristic_overrides if policy == SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC else None,
        )
        elapsed = time.perf_counter() - t0
        all_results.append(result)
        print(f"→ {result.total_scheduled} scheduled in {elapsed * 1000:.1f} ms")

    # ----- Print comparison table -----
    print()
    print("Policy Comparison Summary")
    print(_render_table(all_results))
    print()

    # Identify best policy per metric
    if len(all_results) > 1:
        best_latency = min(all_results, key=lambda r: r.mean_latency_ms)
        best_miss = min(all_results, key=lambda r: r.deadline_miss_rate)
        best_starvation = min(all_results, key=lambda r: r.max_starvation_seconds)
        print("Best by metric:")
        print(f"  Lowest mean latency    : {best_latency.policy.value}")
        print(f"  Lowest deadline miss   : {best_miss.policy.value}")
        print(f"  Lowest max starvation  : {best_starvation.policy.value}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
