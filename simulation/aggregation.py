"""Experiment result aggregation and statistical analysis (TASK-3.6).

Calculates mean, median, IQR, 95th percentile completion times, deadline
success rates, and relative improvements across simulation runs.

Supports 95% confidence intervals via bootstrapping.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simulation.engine import SimulationResult, TransferRecord

# ---------------------------------------------------------------------------
# Statistical primitives
# ---------------------------------------------------------------------------

@dataclass
class ConfidenceInterval:
    """95% bootstrap confidence interval."""

    lower: float
    upper: float
    point_estimate: float
    n_bootstrap: int = 1000

    @property
    def half_width(self) -> float:
        return (self.upper - self.lower) / 2.0

    def __str__(self) -> str:
        return f"{self.point_estimate:.4f} ± {self.half_width:.4f} (95% CI [{self.lower:.4f}, {self.upper:.4f}])"


def _bootstrap_ci(
    data: list[float],
    statistic: Any = None,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> ConfidenceInterval:
    """Compute a bootstrap confidence interval for the mean.

    Parameters
    ----------
    data:
        List of observed values.
    statistic:
        Function to compute (default: ``statistics.mean``).
    n_bootstrap:
        Number of bootstrap resamples.
    confidence:
        Confidence level (e.g. 0.95 for 95% CI).
    seed:
        Random seed for reproducible bootstrapping.
    """
    if statistic is None:
        statistic = statistics.mean

    n = len(data)
    if n == 0:
        return ConfidenceInterval(lower=0.0, upper=0.0, point_estimate=0.0, n_bootstrap=0)
    if n == 1:
        v = data[0]
        return ConfidenceInterval(lower=v, upper=v, point_estimate=v, n_bootstrap=1)

    rng = random.Random(seed)
    point_est = statistic(data)
    bootstrap_stats: list[float] = []

    for _ in range(n_bootstrap):
        sample = [rng.choice(data) for _ in range(n)]
        bootstrap_stats.append(statistic(sample))

    bootstrap_stats.sort()
    alpha = 1.0 - confidence
    lower_idx = int(math.floor(alpha / 2 * n_bootstrap))
    upper_idx = int(math.ceil((1 - alpha / 2) * n_bootstrap)) - 1
    lower_idx = max(0, lower_idx)
    upper_idx = min(n_bootstrap - 1, upper_idx)

    return ConfidenceInterval(
        lower=bootstrap_stats[lower_idx],
        upper=bootstrap_stats[upper_idx],
        point_estimate=point_est,
        n_bootstrap=n_bootstrap,
    )


# ---------------------------------------------------------------------------
# Aggregation models
# ---------------------------------------------------------------------------

@dataclass
class PolicySummary:
    """Statistical summary for one policy across multiple simulation runs."""

    policy_name: str
    n_runs: int
    total_requests: int
    completed: int

    mean_duration_seconds: float
    median_duration_seconds: float
    p95_duration_seconds: float
    iqr_duration_seconds: float

    mean_queue_wait_seconds: float
    max_queue_wait_seconds: float
    mean_throughput_mbps: float

    deadline_success_rate: float
    deadline_success_rate_ci: ConfidenceInterval

    mean_duration_ci: ConfidenceInterval
    p95_duration_ci: ConfidenceInterval

    # Relative improvement vs baseline (filled in by aggregator)
    relative_improvement_vs_fifo: float | None = None


@dataclass
class AggregationReport:
    """Full aggregated report across all policies and seeds."""

    scenario_name: str
    total_runs: int
    policies: list[str]
    seeds: list[int]
    summaries: dict[str, PolicySummary]
    """Keyed by policy name."""

    relative_improvements: dict[str, dict[str, float]] = field(default_factory=dict)
    """relative_improvements[policy][metric] = improvement_fraction vs FIFO baseline."""

    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main aggregator
# ---------------------------------------------------------------------------

class ResultAggregator:
    """Aggregates SimulationResult objects into statistical summaries.

    Parameters
    ----------
    n_bootstrap:
        Number of bootstrap resamples for confidence intervals.
    ci_seed:
        Random seed for bootstrap resampling reproducibility.
    """

    def __init__(self, n_bootstrap: int = 1000, ci_seed: int = 0) -> None:
        self._n_bootstrap = n_bootstrap
        self._ci_seed = ci_seed

    def aggregate(self, results: list[SimulationResult]) -> AggregationReport:
        """Aggregate a list of simulation results into a full report.

        Parameters
        ----------
        results:
            List of ``SimulationResult`` objects (may span multiple policies/seeds).

        Returns
        -------
        AggregationReport
            Full statistical report with per-policy summaries and CIs.
        """
        if not results:
            return AggregationReport(
                scenario_name="empty",
                total_runs=0,
                policies=[],
                seeds=[],
                summaries={},
            )

        # Group by policy
        by_policy: dict[str, list[SimulationResult]] = {}
        all_seeds: set[int] = set()
        scenario_name = results[0].scenario_name

        for r in results:
            by_policy.setdefault(r.policy_name, []).append(r)
            all_seeds.add(r.seed)
            if r.scenario_name != scenario_name:
                scenario_name = "mixed"

        summaries: dict[str, PolicySummary] = {}
        for policy_name, policy_results in by_policy.items():
            summaries[policy_name] = self._summarize_policy(policy_name, policy_results)

        # Compute relative improvements vs FIFO baseline
        relative_improvements: dict[str, dict[str, float]] = {}
        fifo_summary = summaries.get("FIFO")
        for policy_name, summary in summaries.items():
            if policy_name == "FIFO" or fifo_summary is None:
                continue
            improvements: dict[str, float] = {}
            # Deadline success rate improvement
            if fifo_summary.deadline_success_rate > 0:
                improvements["deadline_success_rate"] = (
                    (summary.deadline_success_rate - fifo_summary.deadline_success_rate)
                    / fifo_summary.deadline_success_rate
                )
            # Duration improvement (lower is better → positive means improvement)
            if fifo_summary.mean_duration_seconds > 0:
                improvements["mean_duration"] = (
                    (fifo_summary.mean_duration_seconds - summary.mean_duration_seconds)
                    / fifo_summary.mean_duration_seconds
                )
            relative_improvements[policy_name] = improvements

            summary.relative_improvement_vs_fifo = improvements.get("deadline_success_rate")

        return AggregationReport(
            scenario_name=scenario_name,
            total_runs=len(results),
            policies=sorted(by_policy.keys()),
            seeds=sorted(all_seeds),
            summaries=summaries,
            relative_improvements=relative_improvements,
        )

    def _summarize_policy(self, policy_name: str, results: list[SimulationResult]) -> PolicySummary:
        """Build a ``PolicySummary`` from a list of same-policy results."""
        all_records: list[TransferRecord] = []
        for r in results:
            all_records.extend(r.records)

        total_requests = sum(r.total_requests for r in results)
        completed = sum(r.completed for r in results)

        if all_records:
            # Duration statistics from fine-grained transfer records
            durations = [rec.total_duration_seconds for rec in all_records]
            sorted_durations = sorted(durations)
            n = len(sorted_durations)
            q1 = sorted_durations[int(0.25 * n)] if n > 1 else sorted_durations[0]
            q3 = sorted_durations[int(0.75 * n)] if n > 1 else sorted_durations[0]
            p95_idx = int(0.95 * n)
            p95_dur = sorted_durations[p95_idx] if p95_idx < n else sorted_durations[-1]

            mean_dur = statistics.mean(durations)
            median_dur = statistics.median(durations)
            iqr_dur = q3 - q1

            queue_waits = [rec.queue_time for rec in all_records]
            mean_queue_wait = statistics.mean(queue_waits)
            max_queue_wait = max(queue_waits)

            throughputs = [rec.actual_throughput_mbps for rec in all_records]
            mean_throughput = statistics.mean(throughputs)

            mean_dur_ci = _bootstrap_ci(durations, seed=self._ci_seed)
            p95_dur_ci = _bootstrap_ci(
                durations,
                statistic=lambda x: sorted(x)[int(0.95 * len(x))] if len(x) > 1 else x[0],
                seed=self._ci_seed + 1,
            )
        else:
            # Fall back to run-level metrics when individual records were omitted
            durations = [r.mean_total_duration_seconds for r in results] or [0.0]
            sorted_durations = sorted(durations)
            n = len(sorted_durations)
            q1 = sorted_durations[int(0.25 * n)] if n > 1 else sorted_durations[0]
            q3 = sorted_durations[int(0.75 * n)] if n > 1 else sorted_durations[0]

            mean_dur = statistics.mean(durations)
            median_dur = statistics.median(durations)
            iqr_dur = q3 - q1
            p95_dur = statistics.mean([r.p95_total_duration_seconds for r in results]) if results else 0.0

            queue_waits = [r.mean_queue_wait_seconds for r in results] or [0.0]
            mean_queue_wait = statistics.mean(queue_waits)
            max_queue_wait = max([r.max_queue_wait_seconds for r in results] or [0.0])

            throughputs = [r.mean_throughput_mbps for r in results] or [0.0]
            mean_throughput = statistics.mean(throughputs)

            mean_dur_ci = _bootstrap_ci(durations, seed=self._ci_seed)
            p95_dur_ci = _bootstrap_ci([r.p95_total_duration_seconds for r in results], seed=self._ci_seed + 1)

        # Deadline statistics (per-run to get run-level success rates for CI)
        deadline_rates = [r.deadline_success_rate for r in results]
        deadline_ci = _bootstrap_ci(deadline_rates, seed=self._ci_seed + 2)

        return PolicySummary(
            policy_name=policy_name,
            n_runs=len(results),
            total_requests=total_requests,
            completed=completed,
            mean_duration_seconds=mean_dur,
            median_duration_seconds=median_dur,
            p95_duration_seconds=p95_dur,
            iqr_duration_seconds=iqr_dur,
            mean_queue_wait_seconds=mean_queue_wait,
            max_queue_wait_seconds=max_queue_wait,
            mean_throughput_mbps=mean_throughput,
            deadline_success_rate=statistics.mean(deadline_rates) if deadline_rates else 1.0,
            deadline_success_rate_ci=deadline_ci,
            mean_duration_ci=mean_dur_ci,
            p95_duration_ci=p95_dur_ci,
        )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _ci_to_dict(ci: ConfidenceInterval) -> dict[str, Any]:
    return {
        "lower": ci.lower,
        "upper": ci.upper,
        "point_estimate": ci.point_estimate,
        "n_bootstrap": ci.n_bootstrap,
    }


def _summary_to_dict(summary: PolicySummary) -> dict[str, Any]:
    return {
        "policy_name": summary.policy_name,
        "n_runs": summary.n_runs,
        "total_requests": summary.total_requests,
        "completed": summary.completed,
        "mean_duration_seconds": summary.mean_duration_seconds,
        "median_duration_seconds": summary.median_duration_seconds,
        "p95_duration_seconds": summary.p95_duration_seconds,
        "iqr_duration_seconds": summary.iqr_duration_seconds,
        "mean_queue_wait_seconds": summary.mean_queue_wait_seconds,
        "max_queue_wait_seconds": summary.max_queue_wait_seconds,
        "mean_throughput_mbps": summary.mean_throughput_mbps,
        "deadline_success_rate": summary.deadline_success_rate,
        "deadline_success_rate_ci": _ci_to_dict(summary.deadline_success_rate_ci),
        "mean_duration_ci": _ci_to_dict(summary.mean_duration_ci),
        "p95_duration_ci": _ci_to_dict(summary.p95_duration_ci),
        "relative_improvement_vs_fifo": summary.relative_improvement_vs_fifo,
    }


def _record_to_dict(rec: TransferRecord) -> dict[str, Any]:
    """Convert a TransferRecord to a serialisable dict."""
    return {
        "request_id": rec.request_id,
        "source_id": rec.source_id,
        "destination_id": rec.destination_id,
        "data_size_bytes": rec.data_size_bytes,
        "priority": rec.priority.value if hasattr(rec.priority, "value") else rec.priority,
        "data_category": rec.data_category.value if hasattr(rec.data_category, "value") else str(rec.data_category),
        "arrival_time": rec.arrival_time,
        "queue_time": rec.queue_time,
        "start_time": rec.start_time,
        "completion_time": rec.completion_time,
        "deadline": rec.deadline.isoformat() if rec.deadline else None,
        "deadline_met": rec.deadline_met,
        "key_wait_time": rec.key_wait_time,
        "policy_name": rec.policy_name,
        "actual_throughput_mbps": rec.actual_throughput_mbps,
        "total_duration_seconds": rec.total_duration_seconds,
        "transfer_duration_seconds": rec.transfer_duration_seconds,
        "success": rec.success,
        "failure_reason": rec.failure_reason,
    }


def _result_to_dict(r: SimulationResult) -> dict[str, Any]:
    """Convert a SimulationResult to a JSON-serialisable dict (with records)."""
    return {
        "scenario_name": r.scenario_name,
        "policy_name": r.policy_name,
        "seed": r.seed,
        "total_requests": r.total_requests,
        "completed": r.completed,
        "failed": r.failed,
        "deadline_met": r.deadline_met,
        "deadline_missed": r.deadline_missed,
        "mean_total_duration_seconds": r.mean_total_duration_seconds,
        "median_total_duration_seconds": r.median_total_duration_seconds,
        "p95_total_duration_seconds": r.p95_total_duration_seconds,
        "mean_queue_wait_seconds": r.mean_queue_wait_seconds,
        "max_queue_wait_seconds": r.max_queue_wait_seconds,
        "mean_throughput_mbps": r.mean_throughput_mbps,
        "deadline_success_rate": r.deadline_success_rate,
        "real_elapsed_seconds": r.real_elapsed_seconds,
        "records": [_record_to_dict(rec) for rec in r.records],
    }


def export_to_json(
    results: list[SimulationResult],
    output_path: str | Path,
    include_aggregate: bool = True,
    n_bootstrap: int = 1000,
) -> Path:
    """Export simulation results to a JSON file.

    Parameters
    ----------
    results:
        List of raw ``SimulationResult`` objects.
    output_path:
        Target JSON file path.
    include_aggregate:
        If True, also include an ``aggregate`` section with policy summaries.
    n_bootstrap:
        Bootstrap resamples for confidence interval calculation.

    Returns
    -------
    Path
        Resolved path of the written file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "runs": [_result_to_dict(r) for r in results],
        "total_runs": len(results),
    }

    if include_aggregate and results:
        aggregator = ResultAggregator(n_bootstrap=n_bootstrap)
        report = aggregator.aggregate(results)
        payload["aggregate"] = {
            "scenario_name": report.scenario_name,
            "total_runs": report.total_runs,
            "policies": report.policies,
            "seeds": report.seeds,
            "summaries": {k: _summary_to_dict(v) for k, v in report.summaries.items()},
            "relative_improvements": report.relative_improvements,
        }

    with output_path.open("w") as fh:
        json.dump(payload, fh, indent=2, default=str)

    return output_path


def export_to_csv(
    results: list[SimulationResult],
    output_path: str | Path,
) -> Path:
    """Export raw per-run results to a CSV file.

    Parameters
    ----------
    results:
        List of raw ``SimulationResult`` objects.
    output_path:
        Target CSV file path.

    Returns
    -------
    Path
        Resolved path of the written file.
    """
    import csv

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "scenario_name", "policy_name", "seed", "total_requests", "completed",
        "failed", "deadline_met", "deadline_missed", "mean_total_duration_seconds",
        "median_total_duration_seconds", "p95_total_duration_seconds",
        "mean_queue_wait_seconds", "max_queue_wait_seconds",
        "mean_throughput_mbps", "deadline_success_rate", "real_elapsed_seconds",
    ]

    with output_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(_result_to_dict(r))

    return output_path


def export_markdown_table(
    report: AggregationReport,
    output_path: str | Path | None = None,
    format_ci: bool = True,
) -> str:
    """Generate a Markdown summary table with optional 95% CI bounds.

    Parameters
    ----------
    report:
        Aggregated report from ``ResultAggregator.aggregate()``.
    output_path:
        Optional file path to write the markdown; if None returns as string.
    format_ci:
        If True, include ± 95% CI bounds in the table cells.

    Returns
    -------
    str
        Markdown table string.
    """
    lines: list[str] = [
        f"## Q-SHIELD Experiment Results: {report.scenario_name}",
        "",
        f"**Total runs**: {report.total_runs}  "
        f"**Policies**: {', '.join(report.policies)}  "
        f"**Seeds**: {report.seeds}",
        "",
    ]

    # Build table
    headers = ["Policy", "Mean Duration (s)", "P95 Duration (s)", "Deadline Success Rate", "Mean Queue Wait (s)", "Throughput (Mbps)"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

    for policy_name in report.policies:
        summary = report.summaries.get(policy_name)
        if summary is None:
            continue

        def _fmt(value: float, ci: ConfidenceInterval) -> str:
            if format_ci:
                return f"{value:.2f} ±{ci.half_width:.2f}"
            return f"{value:.2f}"

        row = [
            policy_name,
            _fmt(summary.mean_duration_seconds, summary.mean_duration_ci),
            _fmt(summary.p95_duration_seconds, summary.p95_duration_ci),
            _fmt(summary.deadline_success_rate, summary.deadline_success_rate_ci),
            f"{summary.mean_queue_wait_seconds:.2f}",
            f"{summary.mean_throughput_mbps:.2f}",
        ]
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")

    # Relative improvements section
    if report.relative_improvements:
        lines.append("### Relative Improvements vs FIFO Baseline")
        lines.append("")
        lines.append("| Policy | Deadline Success Rate Δ | Mean Duration Δ |")
        lines.append("| --- | --- | --- |")
        for policy_name, improvements in report.relative_improvements.items():
            dsr = improvements.get("deadline_success_rate", 0.0)
            dur = improvements.get("mean_duration", 0.0)
            lines.append(f"| {policy_name} | {dsr:+.1%} | {dur:+.1%} |")
        lines.append("")

    markdown = "\n".join(lines)

    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown)

    return markdown


__all__ = [
    "AggregationReport",
    "ConfidenceInterval",
    "PolicySummary",
    "ResultAggregator",
    "export_markdown_table",
    "export_to_csv",
    "export_to_json",
]
