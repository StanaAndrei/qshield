"""Tests for workload generator determinism and properties (TASK-3.7)."""

from __future__ import annotations

from app.core.enums import PriorityLevel, TransferStatus
from simulation.workload_generator import MEDICAL_PROFILES, WorkloadConfig, WorkloadGenerator


def _default_config(seed: int = 42, n: int = 100) -> WorkloadConfig:
    return WorkloadConfig(
        source_id="univ-lab",
        destination_id="poly-clinic",
        total_requests=n,
        seed=seed,
        arrival_rate_per_second=0.5,
        priority_weights={"LOW": 0.20, "NORMAL": 0.40, "HIGH": 0.30, "CRITICAL": 0.10},
        category_weights={
            "DIAGNOSTIC_REPORT": 0.40,
            "MEDICAL_IMAGE": 0.30,
            "RESEARCH_DATASET": 0.20,
            "TELEMETRY_LOG": 0.10,
        },
        pareto_alpha=1.5,
        deadline_fraction=0.6,
        deadline_slack_multiplier_min=1.0,
        deadline_slack_multiplier_max=5.0,
        network_throughput_mbps=100.0,
    )


class TestWorkloadGeneratorDeterminism:
    """Verify workload generator produces identical sets for identical seeds."""

    def test_same_seed_same_requests(self) -> None:
        """Two generators with same seed must produce identical request IDs and sizes."""
        cfg = _default_config(seed=42, n=50)
        gen1 = WorkloadGenerator(cfg)
        gen2 = WorkloadGenerator(cfg)

        items1 = gen1.generate()
        items2 = gen2.generate()

        assert len(items1) == len(items2)
        for a, b in zip(items1, items2, strict=True):
            assert a.request.request_id == b.request.request_id
            assert a.request.data_size_bytes == b.request.data_size_bytes
            assert a.request.priority == b.request.priority
            assert a.arrival_time == b.arrival_time

    def test_different_seeds_different_requests(self) -> None:
        """Different seeds must produce different request sets."""
        cfg42 = _default_config(seed=42, n=50)
        cfg99 = _default_config(seed=99, n=50)

        items42 = WorkloadGenerator(cfg42).generate()
        items99 = WorkloadGenerator(cfg99).generate()

        # Request IDs must differ
        ids42 = {str(r.request.request_id) for r in items42}
        ids99 = {str(r.request.request_id) for r in items99}
        assert ids42 != ids99

    def test_correct_total_requests(self) -> None:
        """Generator must produce exactly the configured number of requests."""
        for n in (1, 10, 50, 200):
            cfg = _default_config(n=n)
            items = WorkloadGenerator(cfg).generate()
            assert len(items) == n

    def test_arrival_times_monotonically_increasing(self) -> None:
        """Arrival times must be strictly increasing (Poisson process)."""
        cfg = _default_config(n=100)
        items = WorkloadGenerator(cfg).generate()
        times = [wr.arrival_time for wr in items]
        for i in range(1, len(times)):
            assert times[i] > times[i - 1], f"Non-monotonic arrival at index {i}"

    def test_request_status_is_queued(self) -> None:
        """All generated requests must start in QUEUED status."""
        cfg = _default_config(n=50)
        items = WorkloadGenerator(cfg).generate()
        for wr in items:
            assert wr.request.status == TransferStatus.QUEUED

    def test_priority_distribution_roughly_correct(self) -> None:
        """Priority distribution must roughly match configured weights."""
        cfg = _default_config(n=1000)
        items = WorkloadGenerator(cfg).generate()

        counts: dict[PriorityLevel, int] = {}
        for wr in items:
            counts[wr.request.priority] = counts.get(wr.request.priority, 0) + 1

        total = len(items)
        # Allow 15% tolerance
        assert abs(counts.get(PriorityLevel.LOW, 0) / total - 0.20) < 0.15
        assert abs(counts.get(PriorityLevel.NORMAL, 0) / total - 0.40) < 0.15
        assert abs(counts.get(PriorityLevel.HIGH, 0) / total - 0.30) < 0.15
        assert abs(counts.get(PriorityLevel.CRITICAL, 0) / total - 0.10) < 0.10

    def test_data_sizes_within_category_bounds(self) -> None:
        """Data sizes must be within profile min/max for each category."""
        cfg = _default_config(n=200)
        items = WorkloadGenerator(cfg).generate()

        for wr in items:
            req = wr.request
            profile = MEDICAL_PROFILES[req.data_category]
            assert req.data_size_bytes >= profile.size_min_bytes, (
                f"Size {req.data_size_bytes} < min {profile.size_min_bytes} for {req.data_category}"
            )
            assert req.data_size_bytes <= profile.size_max_bytes, (
                f"Size {req.data_size_bytes} > max {profile.size_max_bytes} for {req.data_category}"
            )

    def test_deadline_fraction_approximately_correct(self) -> None:
        """Roughly the configured fraction of requests should have deadlines."""
        cfg = _default_config(n=1000)
        items = WorkloadGenerator(cfg).generate()

        with_deadline = sum(1 for wr in items if wr.request.deadline is not None)
        fraction = with_deadline / len(items)
        # 0.6 ± 0.1
        assert abs(fraction - 0.60) < 0.10, f"Deadline fraction {fraction:.2f} far from 0.60"

    def test_global_size_bounds_applied(self) -> None:
        """When global_size_min/max are set, all sizes must be within those bounds."""
        cfg = _default_config(n=200)
        cfg.global_size_min_bytes = 1_000_000
        cfg.global_size_max_bytes = 500_000_000

        items = WorkloadGenerator(cfg).generate()
        for wr in items:
            assert wr.request.data_size_bytes >= 1_000_000
            assert wr.request.data_size_bytes <= 500_000_000

    def test_source_destination_ids_correct(self) -> None:
        """All requests must carry the configured source/destination IDs."""
        cfg = _default_config(n=50)
        items = WorkloadGenerator(cfg).generate()

        for wr in items:
            assert wr.request.source_id == "univ-lab"
            assert wr.request.destination_id == "poly-clinic"

    def test_expected_duration_positive(self) -> None:
        """Expected transfer durations must be positive."""
        cfg = _default_config(n=50)
        items = WorkloadGenerator(cfg).generate()

        for wr in items:
            assert wr.expected_duration_seconds > 0.0

    def test_multiple_generates_are_idempotent(self) -> None:
        """Calling generate() multiple times must reset and reproduce the same workload."""
        cfg = _default_config(seed=42, n=30)
        gen = WorkloadGenerator(cfg)

        items_a = gen.generate()
        items_b = gen.generate()  # Second call must reset RNG

        assert len(items_a) == len(items_b)
        for a, b in zip(items_a, items_b, strict=True):
            assert a.request.request_id == b.request.request_id
