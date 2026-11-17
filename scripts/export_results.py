#!/usr/bin/env python3
"""Export and aggregate Q-SHIELD simulation results (TASK-3.6).

Usage:
    python scripts/export_results.py \\
        --input results/experiments/baseline_matrix.json \\
        --output-dir results/reports/ \\
        [--format markdown|csv|json] \\
        [--bootstrap 1000]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from simulation.aggregation import (
    AggregationReport,
    ConfidenceInterval,
    PolicySummary,
    ResultAggregator,
    export_latex_table,
    export_markdown_table,
    export_to_csv,
    export_to_json,
)
from simulation.engine import SimulationResult


def _load_results_and_report_from_json(
    input_path: Path,
) -> tuple[list[SimulationResult], AggregationReport | None]:
    """Load SimulationResult objects and optional precomputed report from a JSON export file."""
    with input_path.open() as fh:
        data = json.load(fh)

    runs = data.get("runs", [])
    results: list[SimulationResult] = []

    for run in runs:
        result = SimulationResult(
            scenario_name=run["scenario_name"],
            policy_name=run["policy_name"],
            seed=run["seed"],
            total_requests=run["total_requests"],
            completed=run["completed"],
            failed=run["failed"],
            deadline_met=run["deadline_met"],
            deadline_missed=run["deadline_missed"],
            mean_total_duration_seconds=run["mean_total_duration_seconds"],
            median_total_duration_seconds=run["median_total_duration_seconds"],
            p95_total_duration_seconds=run["p95_total_duration_seconds"],
            mean_queue_wait_seconds=run["mean_queue_wait_seconds"],
            max_queue_wait_seconds=run["max_queue_wait_seconds"],
            mean_throughput_mbps=run["mean_throughput_mbps"],
            deadline_success_rate=run["deadline_success_rate"],
            real_elapsed_seconds=run["real_elapsed_seconds"],
            records=[],
        )
        results.append(result)

    agg = data.get("aggregate")
    report: AggregationReport | None = None
    if isinstance(agg, dict):
        summaries: dict[str, PolicySummary] = {}
        for p_name, s_data in agg.get("summaries", {}).items():
            dsr_ci = ConfidenceInterval(**s_data["deadline_success_rate_ci"])
            dur_ci = ConfidenceInterval(**s_data["mean_duration_ci"])
            p95_ci = ConfidenceInterval(**s_data["p95_duration_ci"])
            summaries[p_name] = PolicySummary(
                policy_name=s_data["policy_name"],
                n_runs=s_data["n_runs"],
                total_requests=s_data["total_requests"],
                completed=s_data["completed"],
                mean_duration_seconds=s_data["mean_duration_seconds"],
                median_duration_seconds=s_data["median_duration_seconds"],
                p95_duration_seconds=s_data["p95_duration_seconds"],
                iqr_duration_seconds=s_data["iqr_duration_seconds"],
                mean_queue_wait_seconds=s_data["mean_queue_wait_seconds"],
                max_queue_wait_seconds=s_data["max_queue_wait_seconds"],
                mean_throughput_mbps=s_data["mean_throughput_mbps"],
                deadline_success_rate=s_data["deadline_success_rate"],
                deadline_success_rate_ci=dsr_ci,
                mean_duration_ci=dur_ci,
                p95_duration_ci=p95_ci,
                relative_improvement_vs_fifo=s_data.get("relative_improvement_vs_fifo"),
            )
        report = AggregationReport(
            scenario_name=agg.get("scenario_name", "unknown"),
            total_runs=agg.get("total_runs", len(results)),
            policies=agg.get("policies", []),
            seeds=agg.get("seeds", []),
            summaries=summaries,
            relative_improvements=agg.get("relative_improvements", {}),
        )

    return results, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export and aggregate Q-SHIELD simulation experiment results.",
    )
    parser.add_argument(
        "--input",
        default="results/experiments/baseline_matrix.json",
        help="Path to input JSON results file (default: results/experiments/baseline_matrix.json).",
    )
    parser.add_argument(
        "--output-dir",
        default="results/reports/",
        help="Output directory for reports (default: results/reports/).",
    )
    parser.add_argument(
        "--format",
        choices=["all", "markdown", "csv", "json", "latex"],
        default="all",
        help="Output format(s) to generate (default: all).",
    )
    parser.add_argument(
        "--bootstrap",
        type=int,
        default=1000,
        help="Number of bootstrap resamples for 95%% CI calculation (default: 1000).",
    )
    parser.add_argument(
        "--no-ci",
        action="store_true",
        help="Disable confidence interval formatting in Markdown/LaTeX output.",
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading results from: {input_path}")
    results, report = _load_results_and_report_from_json(input_path)
    print(f"Loaded {len(results)} simulation run(s).")

    if not results:
        print("No results to export.", file=sys.stderr)
        return 1

    # Aggregate if no precomputed report exists
    if report is None:
        aggregator = ResultAggregator(n_bootstrap=args.bootstrap)
        report = aggregator.aggregate(results)

    stem = input_path.stem
    fmt = args.format

    # Markdown
    if fmt in ("all", "markdown"):
        md_path = output_dir / f"{stem}_report.md"
        markdown = export_markdown_table(report, output_path=md_path, format_ci=not args.no_ci)
        print(f"Markdown report written to: {md_path}")
        print()
        print(markdown)

    # LaTeX
    if fmt in ("all", "latex"):
        tex_path = output_dir / f"{stem}_table.tex"
        latex = export_latex_table(report, output_path=tex_path, format_ci=not args.no_ci)
        print(f"LaTeX report written to: {tex_path}")
        if fmt == "latex":
            print()
            print(latex)

    # CSV
    if fmt in ("all", "csv"):
        csv_path = output_dir / f"{stem}_raw.csv"
        export_to_csv(results, csv_path)
        print(f"CSV export written to: {csv_path}")

    # JSON aggregate
    if fmt in ("all", "json"):
        json_path = output_dir / f"{stem}_aggregate.json"
        export_to_json(results, json_path, include_aggregate=True, n_bootstrap=args.bootstrap)
        print(f"JSON aggregate written to: {json_path}")

    # Print summary table to stdout
    print()
    print("=" * 60)
    print(f"AGGREGATION SUMMARY: {report.scenario_name}")
    print("=" * 60)
    for policy_name in report.policies:
        summary = report.summaries.get(policy_name)
        if summary is None:
            continue
        ci = summary.deadline_success_rate_ci
        print(
            f"  {policy_name:35s} "
            f"deadline_success={summary.deadline_success_rate:.1%} "
            f"±{ci.half_width:.1%}  "
            f"mean_dur={summary.mean_duration_seconds:.1f}s"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
