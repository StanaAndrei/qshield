"""Integration and smoke tests for Research Experiment Analytics Page and Charts (TASK-6.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from dashboard.components.charts import (
    plot_deadline_success_bars,
    plot_duration_boxplots,
    plot_starvation_histograms,
    plot_throughput_over_time,
)
from dashboard.pages.experiments import (
    _extract_records_dataframe,
    _load_experiment_data,
    _reconstruct_report,
    render_experiments_page,
)


@pytest.fixture
def sample_experiment_data() -> dict[str, Any]:
    """Provides sample experiment JSON structure with fine-grained records."""
    return {
        "scenario_name": "medical_mixed_workload",
        "total_runs": 2,
        "runs": [
            {
                "scenario_name": "medical_mixed_workload",
                "policy_name": "FIFO",
                "seed": 0,
                "total_requests": 3,
                "completed": 3,
                "failed": 0,
                "deadline_met": 3,
                "deadline_missed": 0,
                "mean_total_duration_seconds": 1.2,
                "median_total_duration_seconds": 1.1,
                "p95_total_duration_seconds": 1.5,
                "mean_queue_wait_seconds": 0.4,
                "max_queue_wait_seconds": 0.8,
                "mean_throughput_mbps": 95.0,
                "deadline_success_rate": 1.0,
                "real_elapsed_seconds": 0.05,
                "records": [
                    {
                        "request_id": "req-1",
                        "source_id": "univ-lab",
                        "destination_id": "poly-clinic",
                        "data_size_bytes": 1024 * 1024,
                        "priority": 10,
                        "data_category": "DIAGNOSTIC_REPORT",
                        "arrival_time": 1.0,
                        "queue_time": 0.2,
                        "start_time": 1.2,
                        "completion_time": 2.2,
                        "deadline_met": True,
                        "actual_throughput_mbps": 92.5,
                        "total_duration_seconds": 1.2,
                        "transfer_duration_seconds": 1.0,
                    },
                    {
                        "request_id": "req-2",
                        "source_id": "univ-lab",
                        "destination_id": "poly-clinic",
                        "data_size_bytes": 2 * 1024 * 1024,
                        "priority": 20,
                        "data_category": "RESEARCH_DATASET",
                        "arrival_time": 2.0,
                        "queue_time": 0.5,
                        "start_time": 2.5,
                        "completion_time": 4.0,
                        "deadline_met": True,
                        "actual_throughput_mbps": 98.0,
                        "total_duration_seconds": 2.0,
                        "transfer_duration_seconds": 1.5,
                    },
                ],
            },
            {
                "scenario_name": "medical_mixed_workload",
                "policy_name": "PRIORITY",
                "seed": 0,
                "total_requests": 2,
                "completed": 2,
                "failed": 0,
                "deadline_met": 2,
                "deadline_missed": 0,
                "mean_total_duration_seconds": 0.9,
                "median_total_duration_seconds": 0.9,
                "p95_total_duration_seconds": 1.1,
                "mean_queue_wait_seconds": 0.2,
                "max_queue_wait_seconds": 0.3,
                "mean_throughput_mbps": 96.0,
                "deadline_success_rate": 1.0,
                "real_elapsed_seconds": 0.04,
                "records": [
                    {
                        "request_id": "req-3",
                        "source_id": "univ-lab",
                        "destination_id": "poly-clinic",
                        "data_size_bytes": 1024 * 1024,
                        "priority": 30,
                        "data_category": "DIAGNOSTIC_REPORT",
                        "arrival_time": 1.0,
                        "queue_time": 0.1,
                        "start_time": 1.1,
                        "completion_time": 1.9,
                        "deadline_met": True,
                        "actual_throughput_mbps": 95.0,
                        "total_duration_seconds": 0.9,
                        "transfer_duration_seconds": 0.8,
                    }
                ],
            },
        ],
    }


def test_charts_with_sample_records(sample_experiment_data: dict[str, Any]) -> None:
    """Verifies that all 4 Plotly chart generators produce valid figures from record DataFrames."""
    df = _extract_records_dataframe(sample_experiment_data)
    assert not df.empty
    assert len(df) == 3
    assert set(df["policy_name"].unique()) == {"FIFO", "PRIORITY"}

    # 1. Box plot
    fig_box = plot_duration_boxplots(df)
    assert fig_box is not None
    assert len(fig_box.data) >= 1

    # 2. Deadline success bars
    fig_bars = plot_deadline_success_bars(df)
    assert fig_bars is not None
    assert len(fig_bars.data) >= 1

    # 3. Throughput timeline
    fig_tput = plot_throughput_over_time(df)
    assert fig_tput is not None
    assert len(fig_tput.data) >= 1

    # 4. Starvation histogram
    fig_hist = plot_starvation_histograms(df)
    assert fig_hist is not None
    assert len(fig_hist.data) >= 1


def test_charts_empty_dataframe_graceful_handling() -> None:
    """Verifies that chart functions handle empty or malformed inputs without exceptions."""
    empty_df = pd.DataFrame()

    fig_box = plot_duration_boxplots(empty_df)
    assert fig_box is not None

    fig_bars = plot_deadline_success_bars(empty_df)
    assert fig_bars is not None

    fig_tput = plot_throughput_over_time(empty_df)
    assert fig_tput is not None

    fig_hist = plot_starvation_histograms(empty_df)
    assert fig_hist is not None


def test_reconstruct_report_and_extract_dataframe(sample_experiment_data: dict[str, Any]) -> None:
    """Verifies statistical report reconstruction from runs and records."""
    report = _reconstruct_report(sample_experiment_data)
    assert report is not None
    assert report.total_runs == 2
    assert "FIFO" in report.policies
    assert "PRIORITY" in report.policies
    assert report.summaries["FIFO"].completed == 3
    assert report.summaries["PRIORITY"].completed == 2


def test_render_experiments_page_headless(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Smoke test verifying render_experiments_page executes cleanly headlessly."""
    # Run against existing repo file if present
    baseline_path = Path("results/experiments/baseline_matrix.json")
    if baseline_path.exists():
        data = _load_experiment_data(baseline_path)
        assert data is not None
        df = _extract_records_dataframe(data)
        assert len(df) > 0
        report = _reconstruct_report(data)
        assert report is not None

    # Call render_experiments_page headlessly
    render_experiments_page()
