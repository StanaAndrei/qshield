#!/usr/bin/env python3
"""Comparative Policy Evaluation CLI Tool for Q-SHIELD (TASK-4.8).

Runs identical multi-seed workloads through Heuristic vs AI-Assisted scheduling policies
and outputs comparative deadline success rate, mean duration, throughput improvements,
and predictor MAE.

Usage:
    python scripts/evaluate_policy_gain.py \
        --model models/duration_predictor_v1.joblib \
        --scenario configs/scenarios/medical_mixed_workload.yaml \
        --seeds 5 \
        --output results/experiments/ai_vs_heuristic.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.predictor.evaluate import compute_metrics, extract_features_from_record
from app.predictor.model_registry import ModelRegistry
from simulation.aggregation import ResultAggregator, _result_to_dict, _summary_to_dict
from simulation.engine import EngineConfig, SimulationEngine, SimulationResult
from simulation.key_resource_model import KeyResourceConfig
from simulation.network_model import NetworkConfig
from simulation.scenarios import load_scenario
from simulation.workload_generator import WorkloadConfig


def _build_configs_from_scenario(
    scenario_path: Path,
) -> tuple[WorkloadConfig, NetworkConfig, KeyResourceConfig, dict[str, Any]]:
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
        description="Compare Heuristic vs AI-Assisted scheduling policies across seeds.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Path to trained joblib model artifact.",
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario YAML configuration file.",
    )
    parser.add_argument(
        "--seeds",
        default="5",
        help="Number of random seeds (e.g. 5) or comma-separated list of seed integers.",
    )
    parser.add_argument(
        "--output",
        default="results/experiments/ai_vs_heuristic.json",
        help="Output path for comparative results JSON.",
    )
    parser.add_argument(
        "--output-markdown",
        default=None,
        help="Optional path to output markdown summary report.",
    )

    args = parser.parse_args()

    # Load predictor
    model_path = Path(args.model)
    if not model_path.exists():
        print(f"ERROR: Model artifact not found: {model_path}", file=sys.stderr)
        return 1

    predictor = ModelRegistry.load(model_path)
    print(f"Loaded predictor: {predictor.get_model_version()}")

    # Load scenario
    scenario_path = Path(args.scenario)
    if not scenario_path.exists():
        print(f"ERROR: Scenario file not found: {scenario_path}", file=sys.stderr)
        return 1

    wl_cfg, net_cfg, key_cfg, heuristic_overrides = _build_configs_from_scenario(scenario_path)

    # Parse seeds
    try:
        seeds_str = str(args.seeds)
        if "," in seeds_str:
            seed_list = [int(s.strip()) for s in seeds_str.split(",")]
        else:
            seed_list = list(range(int(seeds_str)))
    except ValueError:
        print(f"ERROR: Invalid seeds argument: {args.seeds}", file=sys.stderr)
        return 1

    print(f"Running comparative evaluation on scenario '{scenario_path.stem}' across seeds: {seed_list}")

    heuristic_results: list[SimulationResult] = []
    ai_results: list[SimulationResult] = []

    # Prediction tracking across AI runs
    y_true_all: list[float] = []
    y_pred_all: list[float] = []

    for seed in seed_list:
        # 1. Run Resource-Aware Heuristic
        wl_copy_heur = WorkloadConfig(**{**wl_cfg.__dict__, "seed": seed})
        net_copy_heur = NetworkConfig(**{**net_cfg.__dict__, "seed": seed})
        key_copy_heur = KeyResourceConfig(**key_cfg.__dict__)
        eng_cfg_heur = EngineConfig(
            policy_name="RESOURCE_AWARE_HEURISTIC",
            scenario_name=scenario_path.stem,
            seed=seed,
            heuristic_overrides=heuristic_overrides,
        )
        sim_heur = SimulationEngine(wl_copy_heur, net_copy_heur, key_copy_heur, eng_cfg_heur)
        res_heur = sim_heur.run()
        heuristic_results.append(res_heur)

        # 2. Run AI-Assisted Scheduler
        wl_copy_ai = WorkloadConfig(**{**wl_cfg.__dict__, "seed": seed})
        net_copy_ai = NetworkConfig(**{**net_cfg.__dict__, "seed": seed})
        key_copy_ai = KeyResourceConfig(**key_cfg.__dict__)
        eng_cfg_ai = EngineConfig(
            policy_name="AI_ASSISTED",
            scenario_name=scenario_path.stem,
            seed=seed,
            heuristic_overrides=heuristic_overrides,
            predictor=predictor,
        )
        sim_ai = SimulationEngine(wl_copy_ai, net_copy_ai, key_copy_ai, eng_cfg_ai)
        res_ai = sim_ai.run()
        ai_results.append(res_ai)

        # Track predictions for MAE
        for rec in res_ai.records:
            actual_dur = rec.transfer_duration_seconds
            rec_dict = {
                "data_size_bytes": rec.data_size_bytes,
                "priority": rec.priority.value if hasattr(rec.priority, "value") else rec.priority,
                "actual_throughput_mbps": rec.actual_throughput_mbps,
                "queue_time": rec.queue_time,
                "total_duration_seconds": rec.total_duration_seconds,
            }
            feat = extract_features_from_record(rec_dict)
            pred_out = predictor.predict(feat)
            y_true_all.append(actual_dur)
            y_pred_all.append(pred_out.predicted_duration_seconds)

    # Compute statistical aggregates
    aggregator = ResultAggregator()
    report_heur = aggregator.aggregate(heuristic_results)
    report_ai = aggregator.aggregate(ai_results)

    sum_heur = report_heur.summaries.get("RESOURCE_AWARE_HEURISTIC")
    sum_ai = report_ai.summaries.get("AI_ASSISTED")

    if sum_heur is None or sum_ai is None:
        print("ERROR: Incomplete simulation summaries produced.", file=sys.stderr)
        return 1

    # Relative improvements (AI vs Heuristic)
    dsr_gain = sum_ai.deadline_success_rate - sum_heur.deadline_success_rate
    dur_diff = (
        (sum_ai.mean_duration_seconds - sum_heur.mean_duration_seconds)
        / max(0.001, sum_heur.mean_duration_seconds)
    )
    tp_diff = (
        (sum_ai.mean_throughput_mbps - sum_heur.mean_throughput_mbps)
        / max(0.001, sum_heur.mean_throughput_mbps)
    )

    pred_metrics = compute_metrics(y_true_all, y_pred_all)

    # Build payload
    payload = {
        "scenario_name": scenario_path.stem,
        "seeds": seed_list,
        "total_runs": len(heuristic_results) + len(ai_results),
        "predictor_version": predictor.get_model_version(),
        "predictor_evaluation": pred_metrics.to_dict(),
        # Keep each policy's run and transfer-level observations so the
        # experiment dashboard can render the same distributions as the
        # baseline matrix export.
        "runs": [_result_to_dict(result) for result in heuristic_results + ai_results],
        "aggregate": {
            "scenario_name": scenario_path.stem,
            "total_runs": len(heuristic_results) + len(ai_results),
            "policies": ["RESOURCE_AWARE_HEURISTIC", "AI_ASSISTED"],
            "seeds": seed_list,
            "summaries": {
                **{name: _summary_to_dict(summary) for name, summary in report_heur.summaries.items()},
                **{name: _summary_to_dict(summary) for name, summary in report_ai.summaries.items()},
            },
            "relative_improvements": {},
        },
        "comparison": {
            "deadline_success_rate": {
                "heuristic": round(sum_heur.deadline_success_rate, 4),
                "ai_assisted": round(sum_ai.deadline_success_rate, 4),
                "absolute_gain": round(dsr_gain, 4),
            },
            "mean_duration_seconds": {
                "heuristic": round(sum_heur.mean_duration_seconds, 4),
                "ai_assisted": round(sum_ai.mean_duration_seconds, 4),
                "relative_change": round(dur_diff, 4),
            },
            "p95_duration_seconds": {
                "heuristic": round(sum_heur.p95_duration_seconds, 4),
                "ai_assisted": round(sum_ai.p95_duration_seconds, 4),
            },
            "mean_throughput_mbps": {
                "heuristic": round(sum_heur.mean_throughput_mbps, 4),
                "ai_assisted": round(sum_ai.mean_throughput_mbps, 4),
                "relative_change": round(tp_diff, 4),
            },
            "mean_queue_wait_seconds": {
                "heuristic": round(sum_heur.mean_queue_wait_seconds, 4),
                "ai_assisted": round(sum_ai.mean_queue_wait_seconds, 4),
            },
        },
    }

    out_file = Path(args.output)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w") as fh:
        json.dump(payload, fh, indent=2)

    # Print summary table
    print("\n" + "=" * 75)
    print("COMPARATIVE POLICY EVALUATION: HEURISTIC vs AI-ASSISTED")
    print("=" * 75)
    print(f"Scenario:             {scenario_path.stem}")
    print(f"Seeds:                {seed_list}")
    print(f"Predictor Version:    {predictor.get_model_version()}")
    print(f"Predictor Test MAE:   {pred_metrics.mae:.4f}s  (RMSE: {pred_metrics.rmse:.4f}s, R²: {pred_metrics.r2:.4f})")
    print("-" * 75)
    print(f"{'Metric':<30} {'Heuristic':<18} {'AI-Assisted':<18} {'Delta':<12}")
    print("-" * 75)
    print(f"{'Deadline Success Rate':<30} {sum_heur.deadline_success_rate:<18.1%} {sum_ai.deadline_success_rate:<18.1%} {dsr_gain:+6.1%}")
    print(f"{'Mean Total Duration (s)':<30} {sum_heur.mean_duration_seconds:<18.2f} {sum_ai.mean_duration_seconds:<18.2f} {dur_diff:+6.1%}")
    print(f"{'P95 Total Duration (s)':<30} {sum_heur.p95_duration_seconds:<18.2f} {sum_ai.p95_duration_seconds:<18.2f}")
    print(f"{'Mean Throughput (Mbps)':<30} {sum_heur.mean_throughput_mbps:<18.2f} {sum_ai.mean_throughput_mbps:<18.2f} {tp_diff:+6.1%}")
    print(f"{'Mean Queue Wait (s)':<30} {sum_heur.mean_queue_wait_seconds:<18.2f} {sum_ai.mean_queue_wait_seconds:<18.2f}")
    print("=" * 75)
    print(f"Comparative report successfully exported to: {out_file}")

    if args.output_markdown:
        md_path = Path(args.output_markdown)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_text = f"""# Comparative Policy Evaluation: Heuristic vs AI-Assisted

- **Scenario**: `{scenario_path.stem}`
- **Seeds**: `{seed_list}`
- **Predictor**: `{predictor.get_model_version()}` (MAE: {pred_metrics.mae:.4f}s, R²: {pred_metrics.r2:.4f})

| Metric | Heuristic | AI-Assisted | Delta |
|---|---|---|---|
| Deadline Success Rate | {sum_heur.deadline_success_rate:.1%} | {sum_ai.deadline_success_rate:.1%} | {dsr_gain:+.1%} |
| Mean Duration | {sum_heur.mean_duration_seconds:.2f}s | {sum_ai.mean_duration_seconds:.2f}s | {dur_diff:+.1%} |
| P95 Duration | {sum_heur.p95_duration_seconds:.2f}s | {sum_ai.p95_duration_seconds:.2f}s | - |
| Throughput | {sum_heur.mean_throughput_mbps:.2f} Mbps | {sum_ai.mean_throughput_mbps:.2f} Mbps | {tp_diff:+.1%} |
| Mean Queue Wait | {sum_heur.mean_queue_wait_seconds:.2f}s | {sum_ai.mean_queue_wait_seconds:.2f}s | - |
"""
        md_path.write_text(md_text)
        print(f"Markdown report written to: {md_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
