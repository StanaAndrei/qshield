"""Tests for parallel experiment runner and multi-core scaling (TASK-3.7)."""

from __future__ import annotations

from simulation.engine import SimulationResult
from simulation.key_resource_model import KeyResourceConfig
from simulation.network_model import NetworkConfig
from simulation.parallel_runner import ParallelExperimentRunner, SimulationJob, _run_job
from simulation.workload_generator import WorkloadConfig


def _make_dummy_job(policy: str = "FIFO", seed: int = 42, total_requests: int = 20) -> SimulationJob:
    wl = WorkloadConfig(
        source_id="univ-lab",
        destination_id="poly-clinic",
        total_requests=total_requests,
        seed=seed,
        arrival_rate_per_second=2.0,
        global_size_min_bytes=100_000,
        global_size_max_bytes=1_000_000,
    )
    net = NetworkConfig(
        source_id="univ-lab",
        destination_id="poly-clinic",
        baseline_throughput_mbps=100.0,
        throughput_std_mbps=5.0,
        rtt_ms=5.0,
        seed=seed,
    )
    key = KeyResourceConfig(
        source_id="univ-lab",
        destination_id="poly-clinic",
        initial_keys=1000,
        buffer_ceiling=2000,
        generation_rate_keys_per_second=50.0,
        seed=seed,
    )
    return SimulationJob(
        job_id=f"test/{policy}/seed_{seed}",
        policy_name=policy,
        seed=seed,
        scenario_name="test_scenario",
        workload_config=wl,
        network_config=net,
        key_config=key,
    )


class TestParallelExperimentRunner:
    """Verify parallel runner execution, job matrix generation, and state isolation."""

    def test_run_single_job_directly(self) -> None:
        """Worker function _run_job can execute a job and return SimulationResult."""
        job = _make_dummy_job()
        result = _run_job(job)
        assert isinstance(result, SimulationResult)
        assert result.total_requests == 20
        assert result.completed == 20
        assert result.policy_name == "FIFO"

    def test_build_jobs_matrix(self) -> None:
        """build_jobs creates the full cartesian product of policies and seeds."""
        wl = WorkloadConfig(total_requests=10)
        net = NetworkConfig(source_id="A", destination_id="B")
        key = KeyResourceConfig(source_id="A", destination_id="B")
        policies = ["FIFO", "PRIORITY"]
        seeds = [1, 2, 3]

        jobs = ParallelExperimentRunner.build_jobs(
            scenario_name="scen1",
            workload_config=wl,
            network_config=net,
            key_config=key,
            policies=policies,
            seeds=seeds,
        )

        assert len(jobs) == 6
        job_ids = {j.job_id for j in jobs}
        assert "scen1/FIFO/seed_1" in job_ids
        assert "scen1/PRIORITY/seed_3" in job_ids

    def test_runner_empty_jobs(self) -> None:
        """Runner handles empty job list gracefully."""
        runner = ParallelExperimentRunner(n_workers=2)
        res = runner.run([])
        assert res.total_jobs == 0
        assert res.completed_jobs == 0
        assert len(res.results) == 0

    def test_parallel_run_executes_all_jobs(self) -> None:
        """Parallel run completes all jobs without errors and preserves outcomes."""
        jobs = [
            _make_dummy_job(policy="FIFO", seed=10, total_requests=15),
            _make_dummy_job(policy="PRIORITY", seed=11, total_requests=15),
            _make_dummy_job(policy="RESOURCE_AWARE_HEURISTIC", seed=12, total_requests=15),
            _make_dummy_job(policy="FIFO", seed=13, total_requests=15),
        ]
        runner = ParallelExperimentRunner(n_workers=2)
        res = runner.run(jobs)

        assert res.total_jobs == 4
        assert res.completed_jobs == 4
        assert res.failed_jobs == 0
        assert len(res.results) == 4
        assert res.total_real_elapsed_seconds > 0

    def test_sequential_run(self) -> None:
        """run_sequential executes all jobs in single-process mode."""
        jobs = [
            _make_dummy_job(policy="FIFO", seed=1, total_requests=10),
            _make_dummy_job(policy="PRIORITY", seed=2, total_requests=10),
        ]
        runner = ParallelExperimentRunner(n_workers=1)
        res = runner.run_sequential(jobs)

        assert res.completed_jobs == 2
        assert res.failed_jobs == 0
        assert res.speedup_vs_sequential == 1.0

    def test_no_state_leakage_across_parallel_jobs(self) -> None:
        """Parallel jobs running simultaneously do not mutate each other's seeds or records."""
        # Run two identical jobs in parallel
        job1 = _make_dummy_job(policy="FIFO", seed=77, total_requests=25)
        job2 = _make_dummy_job(policy="FIFO", seed=77, total_requests=25)
        runner = ParallelExperimentRunner(n_workers=2)
        res = runner.run([job1, job2])

        assert res.completed_jobs == 2
        r1, r2 = res.results[0], res.results[1]
        assert r1.total_requests == r2.total_requests
        assert r1.completed == r2.completed
        assert abs(r1.mean_total_duration_seconds - r2.mean_total_duration_seconds) < 1e-6
