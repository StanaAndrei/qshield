"""Tests for simulation result aggregation and export (TASK-3.6)."""

from __future__ import annotations

import json
from pathlib import Path

from simulation.aggregation import (
    ResultAggregator,
    export_markdown_table,
    export_to_csv,
    export_to_json,
)
from simulation.engine import SimulationResult


def _make_dummy_result(policy: str, seed: int, mean_dur: float, dsr: float) -> SimulationResult:
    return SimulationResult(
        scenario_name="scen_test",
        policy_name=policy,
        seed=seed,
        total_requests=10,
        completed=10,
        failed=0,
        deadline_met=int(10 * dsr),
        deadline_missed=int(10 * (1 - dsr)),
        mean_total_duration_seconds=mean_dur,
        median_total_duration_seconds=mean_dur,
        p95_total_duration_seconds=mean_dur * 1.5,
        mean_queue_wait_seconds=1.0,
        max_queue_wait_seconds=2.0,
        mean_throughput_mbps=100.0,
        deadline_success_rate=dsr,
        real_elapsed_seconds=0.1,
        records=[],
    )


class TestAggregation:
    def test_aggregate_summaries_and_relative_improvement(self) -> None:
        results = [
            _make_dummy_result("FIFO", seed=1, mean_dur=10.0, dsr=0.6),
            _make_dummy_result("FIFO", seed=2, mean_dur=12.0, dsr=0.6),
            _make_dummy_result("PRIORITY", seed=1, mean_dur=8.0, dsr=0.8),
            _make_dummy_result("PRIORITY", seed=2, mean_dur=8.0, dsr=0.8),
        ]
        aggregator = ResultAggregator(n_bootstrap=100)
        report = aggregator.aggregate(results)

        assert report.total_runs == 4
        assert "FIFO" in report.summaries
        assert "PRIORITY" in report.summaries

        prio_summary = report.summaries["PRIORITY"]
        assert prio_summary.deadline_success_rate == 0.8
        assert prio_summary.relative_improvement_vs_fifo is not None
        # (0.8 - 0.6) / 0.6 = +0.3333
        assert prio_summary.relative_improvement_vs_fifo > 0

    def test_export_json_csv_markdown(self, tmp_path: Path) -> None:
        results = [
            _make_dummy_result("FIFO", seed=1, mean_dur=10.0, dsr=0.7),
            _make_dummy_result("PRIORITY", seed=1, mean_dur=9.0, dsr=0.9),
        ]
        json_file = tmp_path / "results.json"
        export_to_json(results, json_file, include_aggregate=True, n_bootstrap=50)
        assert json_file.exists()
        with json_file.open() as fh:
            data = json.load(fh)
        assert "runs" in data
        assert "aggregate" in data

        csv_file = tmp_path / "results.csv"
        export_to_csv(results, csv_file)
        assert csv_file.exists()

        aggregator = ResultAggregator(n_bootstrap=50)
        report = aggregator.aggregate(results)
        md_file = tmp_path / "report.md"
        md_content = export_markdown_table(report, output_path=md_file)
        assert md_file.exists()
        assert "FIFO" in md_content
        assert "PRIORITY" in md_content
