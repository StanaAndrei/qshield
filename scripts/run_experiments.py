#!/usr/bin/env python3
"""Multi-core parallel experiment runner CLI for Q-SHIELD (TASK-3.7).

Runs a matrix of (policy × seed) simulation jobs and exports results to JSON.

Usage:
    python scripts/run_experiments.py \\
        --scenario configs/scenarios/medical_mixed_workload.yaml \\
        --workers 4 \\
        --seeds 5 \\
        --policies FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC \\
        --output results/experiments/baseline_matrix.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from simulation.aggregation import ResultAggregator, export_to_json
from simulation.key_resource_model import KeyResourceConfig
from simulation.network_model import NetworkConfig
from simulation.parallel_runner import ParallelExperimentRunner
from simulation.scenarios import load_scenario
from simulation.workload_generator import WorkloadConfig


def _build_configs_from_scenario(scenario_path: Path) -> tuple[WorkloadConfig, NetworkConfig, KeyResourceConfig, dict]:
    """Load scenario YAML and extract component configs."""
    scenario = load_scenario(scenario_path)
    wl_config = scenario.workload
    net_config = scenario.network
    key_config = KeyResourceConfig(
        source_id=scenario.key_resource.source_id,
        destination_id=scenario.key_resource.destination_id,
        initial_keys=scenario.key_resource.initial_keys,
        buffer_ceiling=scenario.key_resource.buffer_ceiling,
        generation_rate_keys_per_second=scenario.key_resource.generation_rate_keys_per_second,
        keys_per_transfer=scenario.key_resource.keys_per_transfer,
    )
    heuristic_overrides = scenario.extra.get("heuristic_overrides", {})
    return wl_config, net_config, key_config, heuristic_overrides


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Q-SHIELD simulation experiments in parallel.",
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario YAML configuration file.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel worker processes (default: 4).",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        default=5,
        help="Number of random seeds to use (seeds 0..N-1). Or comma-separated list.",
    )
    parser.add_argument(
        "--policies",
        default="FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC",
        help="Comma-separated list of scheduling policies (default: FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC).",
    )
    parser.add_argument(
        "--output",
        default="results/experiments/baseline_matrix.json",
        help="Output JSON results file path.",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Run jobs sequentially instead of in parallel (for debugging).",
    )
    parser.add_argument(
        "--benchmark-speedup",
        action="store_true",
        help="Run both sequential and parallel to measure speedup.",
    )

    args = parser.parse_args()

    scenario_path = Path(args.scenario)
    if not scenario_path.exists():
        print(f"ERROR: Scenario file not found: {scenario_path}", file=sys.stderr)
        return 1

    # Parse seeds
    try:
        seeds_input = str(args.seeds)
        if "," in seeds_input:
            seeds = [int(s.strip()) for s in seeds_input.split(",")]
        else:
            seeds = list(range(int(seeds_input)))
    except ValueError:
        print(f"ERROR: Invalid seeds specification: {args.seeds}", file=sys.stderr)
        return 1

    policies = [p.strip() for p in args.policies.split(",")]

    print(f"Loading scenario from: {scenario_path}")
    wl_config, net_config, key_config, heuristic_overrides = _build_configs_from_scenario(scenario_path)

    print(f"Building job matrix: {len(policies)} policies × {len(seeds)} seeds = {len(policies) * len(seeds)} jobs")

    jobs = ParallelExperimentRunner.build_jobs(
        scenario_name=scenario_path.stem,
        workload_config=wl_config,
        network_config=net_config,
        key_config=key_config,
        policies=policies,
        seeds=seeds,
        heuristic_overrides=heuristic_overrides,
    )

    runner = ParallelExperimentRunner(n_workers=args.workers)

    if args.benchmark_speedup:
        print(f"Running {len(jobs)} jobs SEQUENTIALLY for baseline...")
        seq_result = runner.run_sequential(jobs)
        print(f"Sequential: {seq_result.total_real_elapsed_seconds:.2f}s for {seq_result.completed_jobs} jobs")

        print(f"Running {len(jobs)} jobs in PARALLEL with {args.workers} workers...")
        par_result = runner.run(jobs)
        speedup = seq_result.total_real_elapsed_seconds / max(0.001, par_result.total_real_elapsed_seconds)
        print(f"Parallel:   {par_result.total_real_elapsed_seconds:.2f}s (speedup={speedup:.2f}x)")

        all_results = seq_result.results + par_result.results
        print(f"\nTotal results: {len(all_results)}")
    elif args.sequential:
        print(f"Running {len(jobs)} jobs sequentially...")
        result = runner.run_sequential(jobs)
        all_results = result.results
        print(f"Completed {result.completed_jobs}/{result.total_jobs} in {result.total_real_elapsed_seconds:.2f}s")
    else:
        print(f"Running {len(jobs)} jobs in parallel with {args.workers} workers...")
        result = runner.run(jobs)
        all_results = result.results
        est_speedup = result.speedup_vs_sequential
        print(
            f"Completed {result.completed_jobs}/{result.total_jobs} in "
            f"{result.total_real_elapsed_seconds:.2f}s "
            f"(est. speedup={est_speedup:.2f}x)" if est_speedup else
            f"Completed {result.completed_jobs}/{result.total_jobs} in "
            f"{result.total_real_elapsed_seconds:.2f}s"
        )
        if result.errors:
            print(f"WARNING: {result.failed_jobs} jobs failed:")
            for err in result.errors:
                print(f"  - {err}")

    if not all_results:
        print("ERROR: No results produced.", file=sys.stderr)
        return 1

    # Export results
    output_path = Path(args.output)
    print(f"\nExporting {len(all_results)} results to: {output_path}")
    export_to_json(all_results, output_path, include_aggregate=True)
    print(f"Results written to: {output_path}")

    # Print summary
    aggregator = ResultAggregator()
    report = aggregator.aggregate(all_results)

    print()
    print("=" * 60)
    print("EXPERIMENT SUMMARY")
    print("=" * 60)
    for policy_name in report.policies:
        summary = report.summaries.get(policy_name)
        if summary is None:
            continue
        print(
            f"  {policy_name:35s} "
            f"deadline_success={summary.deadline_success_rate:.1%}  "
            f"mean_dur={summary.mean_duration_seconds:.1f}s  "
            f"p95_dur={summary.p95_duration_seconds:.1f}s"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
