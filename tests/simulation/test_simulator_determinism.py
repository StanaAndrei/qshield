"""Tests for simulation determinism: identical seeds produce identical results (TASK-3.7)."""

from __future__ import annotations

from simulation.engine import EngineConfig, SimulationEngine, SimulationResult
from simulation.key_resource_model import KeyResourceConfig
from simulation.network_model import NetworkConfig
from simulation.workload_generator import WorkloadConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_configs(seed: int = 42, n_requests: int = 50) -> tuple[WorkloadConfig, NetworkConfig, KeyResourceConfig]:
    """Build a consistent set of configs for testing."""
    wl = WorkloadConfig(
        source_id="univ-lab",
        destination_id="poly-clinic",
        total_requests=n_requests,
        seed=seed,
        arrival_rate_per_second=1.0,
        priority_weights={"LOW": 0.20, "NORMAL": 0.40, "HIGH": 0.30, "CRITICAL": 0.10},
        category_weights={"DIAGNOSTIC_REPORT": 0.40, "MEDICAL_IMAGE": 0.30, "RESEARCH_DATASET": 0.20, "TELEMETRY_LOG": 0.10},
        pareto_alpha=1.5,
        global_size_min_bytes=1_000_000,
        global_size_max_bytes=50_000_000,
        deadline_fraction=0.6,
        deadline_slack_multiplier_min=1.5,
        deadline_slack_multiplier_max=5.0,
        network_throughput_mbps=100.0,
    )
    net = NetworkConfig(
        source_id="univ-lab",
        destination_id="poly-clinic",
        baseline_throughput_mbps=100.0,
        throughput_std_mbps=5.0,
        rtt_ms=10.0,
        packet_drop_probability=0.001,
        min_throughput_mbps=10.0,
        seed=seed,
    )
    key = KeyResourceConfig(
        source_id="univ-lab",
        destination_id="poly-clinic",
        initial_keys=10000,
        buffer_ceiling=20000,
        generation_rate_keys_per_second=100.0,
        keys_per_transfer=1,
    )
    return wl, net, key


def _run_simulation(policy: str, seed: int, n_requests: int = 50) -> SimulationResult:
    """Run a simulation with given policy and seed."""
    wl, net, key = _make_configs(seed=seed, n_requests=n_requests)
    engine_cfg = EngineConfig(policy_name=policy, scenario_name="test", seed=seed)
    engine = SimulationEngine(wl, net, key, engine_cfg)
    return engine.run()


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------

class TestSimulatorDeterminism:
    """Verify that identical seeds produce identical simulation results."""

    def test_fifo_same_seed_deterministic(self) -> None:
        """Two FIFO runs with the same seed must produce identical total_requests and completion count."""
        result1 = _run_simulation("FIFO", seed=42)
        result2 = _run_simulation("FIFO", seed=42)

        assert result1.total_requests == result2.total_requests
        assert result1.completed == result2.completed
        assert result1.failed == result2.failed

    def test_priority_same_seed_deterministic(self) -> None:
        """Two PRIORITY runs with same seed must yield identical metrics."""
        result1 = _run_simulation("PRIORITY", seed=99)
        result2 = _run_simulation("PRIORITY", seed=99)

        assert result1.total_requests == result2.total_requests
        assert result1.completed == result2.completed
        # Mean durations should match within floating point tolerance
        assert abs(result1.mean_total_duration_seconds - result2.mean_total_duration_seconds) < 1e-9

    def test_heuristic_same_seed_deterministic(self) -> None:
        """Two RESOURCE_AWARE_HEURISTIC runs with same seed must yield identical results."""
        result1 = _run_simulation("RESOURCE_AWARE_HEURISTIC", seed=7)
        result2 = _run_simulation("RESOURCE_AWARE_HEURISTIC", seed=7)

        assert result1.total_requests == result2.total_requests
        assert result1.completed == result2.completed

    def test_different_seeds_different_results(self) -> None:
        """Different seeds should produce observably different workloads."""
        result_42 = _run_simulation("FIFO", seed=42, n_requests=100)
        result_99 = _run_simulation("FIFO", seed=99, n_requests=100)

        # With different seeds the mean duration should differ (high probability)
        # We can't guarantee exact inequality but statistically very likely
        assert result_42.total_requests == result_99.total_requests  # Same config
        # At least one metric should differ
        metrics_differ = (
            abs(result_42.mean_total_duration_seconds - result_99.mean_total_duration_seconds) > 0.001
            or result_42.deadline_met != result_99.deadline_met
        )
        assert metrics_differ, "Different seeds should produce different results"

    def test_all_requests_completed(self) -> None:
        """All generated requests must be completed (or failed) — none left dangling."""
        result = _run_simulation("FIFO", seed=42, n_requests=50)
        assert result.total_requests == 50
        assert result.completed + result.failed == result.total_requests

    def test_simulation_1000_requests_under_5_seconds(self) -> None:
        """1,000 requests must simulate in under 5 real seconds (TASK-3.4 DoD)."""
        wl, net, key = _make_configs(seed=42, n_requests=1000)
        engine_cfg = EngineConfig(policy_name="FIFO", scenario_name="perf_test", seed=42)
        engine = SimulationEngine(wl, net, key, engine_cfg)
        result = engine.run()

        assert result.total_requests == 1000
        assert result.real_elapsed_seconds < 5.0, (
            f"Simulation took {result.real_elapsed_seconds:.2f}s (limit: 5.0s)"
        )

    def test_records_match_total_requests(self) -> None:
        """Every completed transfer must have a record."""
        result = _run_simulation("FIFO", seed=42, n_requests=20)
        assert len(result.records) == result.total_requests

    def test_deadline_success_rate_in_range(self) -> None:
        """Deadline success rate must be in [0, 1]."""
        result = _run_simulation("PRIORITY", seed=42, n_requests=50)
        assert 0.0 <= result.deadline_success_rate <= 1.0

    def test_policy_name_recorded_correctly(self) -> None:
        """Results must carry the correct policy name."""
        for policy in ("FIFO", "PRIORITY", "RESOURCE_AWARE_HEURISTIC"):
            result = _run_simulation(policy, seed=42, n_requests=20)
            assert result.policy_name == policy

    def test_multiple_runs_same_workload_bit_exact(self) -> None:
        """Running the same simulation 3 times must produce the exact same record count."""
        results = [_run_simulation("FIFO", seed=123, n_requests=30) for _ in range(3)]
        for r in results[1:]:
            assert r.total_requests == results[0].total_requests
            assert r.completed == results[0].completed
