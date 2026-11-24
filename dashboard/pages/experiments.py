"""Research Experiment Analytics Page for Q-SHIELD Dashboard (TASK-6.2).

Visualizes cross-policy comparative benchmark results from `results/experiments/*.json`:
- Transfer duration distribution box plots
- Deadline success rate bar charts with confidence intervals
- Effective throughput dynamics over virtual simulation time
- Queue wait time and starvation mitigation histograms
- Statistical summary tables with LaTeX and Markdown export
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from app.core.enums import DataCategory, PriorityLevel
from dashboard.components.charts import (
    plot_deadline_success_bars,
    plot_duration_boxplots,
    plot_starvation_histograms,
    plot_throughput_over_time,
)
from simulation.aggregation import (
    AggregationReport,
    ConfidenceInterval,
    PolicySummary,
    ResultAggregator,
    export_latex_table,
    export_markdown_table,
)
from simulation.engine import SimulationResult, TransferRecord

logger = logging.getLogger(__name__)

EXPERIMENTS_DIR = Path("results/experiments")


def _find_experiment_files() -> list[Path]:
    """Scans the experiments directory for available JSON results."""
    if not EXPERIMENTS_DIR.exists():
        return []
    return sorted(EXPERIMENTS_DIR.glob("*.json"))


def _load_experiment_data(file_path: Path) -> dict[str, Any] | None:
    """Loads and parses raw JSON experiment results from disk."""
    try:
        with file_path.open() as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
            return None
    except Exception as exc:
        logger.error("Failed to load experiment file %s: %s", file_path, exc)
        return None


def _extract_records_dataframe(data: dict[str, Any]) -> pd.DataFrame:
    """Extracts fine-grained transfer records from loaded experiment runs into a DataFrame."""
    runs = data.get("runs", [])
    records_list: list[dict[str, Any]] = []

    for run in runs:
        policy_name = run.get("policy_name", "UNKNOWN")
        seed = run.get("seed", 0)
        scenario_name = run.get("scenario_name", "unknown")

        for rec in run.get("records", []):
            rec_dict = dict(rec)
            rec_dict["policy_name"] = policy_name
            rec_dict["seed"] = seed
            rec_dict["scenario_name"] = scenario_name
            records_list.append(rec_dict)

    if not records_list and runs:
        # If runs omit individual records, use run-level metrics as pseudo-records
        for run in runs:
            records_list.append({
                "policy_name": run.get("policy_name", "UNKNOWN"),
                "seed": run.get("seed", 0),
                "scenario_name": run.get("scenario_name", "unknown"),
                "total_duration_seconds": run.get("mean_total_duration_seconds", 0.0),
                "transfer_duration_seconds": run.get("mean_total_duration_seconds", 0.0),
                "mean_total_duration_seconds": run.get("mean_total_duration_seconds", 0.0),
                "queue_time": run.get("mean_queue_wait_seconds", 0.0),
                "actual_throughput_mbps": run.get("mean_throughput_mbps", 0.0),
                "deadline_met": run.get("deadline_success_rate", 1.0) >= 0.5,
                "deadline_success_rate": run.get("deadline_success_rate", 1.0),
                "completion_time": 10.0,
            })

    # AI-vs-heuristic reports intentionally contain aggregates rather than the
    # individual simulation records written by run_experiments.py.
    if not records_list and isinstance(data.get("comparison"), dict):
        comparison = data["comparison"]
        for policy_key, policy_name in (("heuristic", "RESOURCE_AWARE_HEURISTIC"), ("ai_assisted", "AI_ASSISTED")):
            records_list.append({
                "policy_name": policy_name,
                "seed": "aggregate",
                "scenario_name": data.get("scenario_name", "unknown"),
                "total_duration_seconds": comparison.get("mean_duration_seconds", {}).get(policy_key, 0.0),
                "transfer_duration_seconds": comparison.get("mean_duration_seconds", {}).get(policy_key, 0.0),
                "p95_duration_seconds": comparison.get("p95_duration_seconds", {}).get(policy_key, 0.0),
                "queue_time": comparison.get("mean_queue_wait_seconds", {}).get(policy_key, 0.0),
                "actual_throughput_mbps": comparison.get("mean_throughput_mbps", {}).get(policy_key, 0.0),
                "deadline_success_rate": comparison.get("deadline_success_rate", {}).get(policy_key, 0.0),
                "deadline_met": comparison.get("deadline_success_rate", {}).get(policy_key, 0.0) >= 0.5,
                "completion_time": 1.0,
            })

    return pd.DataFrame(records_list)


def _reconstruct_report(data: dict[str, Any]) -> AggregationReport | None:
    """Reconstructs or computes an AggregationReport from loaded JSON experiment data."""
    agg = data.get("aggregate")
    if isinstance(agg, dict) and "summaries" in agg:
        try:
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
            return AggregationReport(
                scenario_name=agg.get("scenario_name", "unknown"),
                total_runs=agg.get("total_runs", len(data.get("runs", []))),
                policies=agg.get("policies", list(summaries.keys())),
                seeds=agg.get("seeds", []),
                summaries=summaries,
                relative_improvements=agg.get("relative_improvements", {}),
            )
        except Exception as exc:
            logger.warning("Could not reconstruct precomputed report: %s. Re-aggregating...", exc)

    runs = data.get("runs", [])
    if not runs:
        return None

    results: list[SimulationResult] = []
    for run in runs:
        records: list[TransferRecord] = []
        for rec in run.get("records", []):
            prio_raw = rec.get("priority", PriorityLevel.NORMAL)
            if isinstance(prio_raw, int):
                try:
                    prio = PriorityLevel(prio_raw)
                except ValueError:
                    prio = PriorityLevel.NORMAL
            elif isinstance(prio_raw, str):
                try:
                    prio = PriorityLevel[prio_raw]
                except KeyError:
                    prio = PriorityLevel.NORMAL
            else:
                prio = prio_raw

            cat_raw = rec.get("data_category", DataCategory.DIAGNOSTIC_REPORT)
            if isinstance(cat_raw, str):
                try:
                    cat = DataCategory(cat_raw)
                except ValueError:
                    cat = DataCategory.DIAGNOSTIC_REPORT
            else:
                cat = cat_raw

            records.append(
                TransferRecord(
                    request_id=rec.get("request_id", ""),
                    source_id=rec.get("source_id", "univ-lab"),
                    destination_id=rec.get("destination_id", "poly-clinic"),
                    data_size_bytes=rec.get("data_size_bytes", 0),
                    priority=prio,
                    data_category=cat,
                    arrival_time=rec.get("arrival_time", 0.0),
                    queue_time=rec.get("queue_time", 0.0),
                    start_time=rec.get("start_time", 0.0),
                    completion_time=rec.get("completion_time", 0.0),
                    deadline=None,
                    deadline_met=rec.get("deadline_met", True),
                    key_wait_time=rec.get("key_wait_time", 0.0),
                    policy_name=rec.get("policy_name", run.get("policy_name", "FIFO")),
                    actual_throughput_mbps=rec.get("actual_throughput_mbps", 0.0),
                    success=rec.get("success", True),
                    failure_reason=rec.get("failure_reason"),
                )
            )
        results.append(
            SimulationResult(
                scenario_name=run.get("scenario_name", "medical_mixed_workload"),
                policy_name=run.get("policy_name", "FIFO"),
                seed=run.get("seed", 0),
                total_requests=run.get("total_requests", len(records)),
                completed=run.get("completed", len(records)),
                failed=run.get("failed", 0),
                deadline_met=run.get("deadline_met", sum(1 for r in records if r.deadline_met)),
                deadline_missed=run.get("deadline_missed", sum(1 for r in records if not r.deadline_met)),
                mean_total_duration_seconds=run.get("mean_total_duration_seconds", 0.0),
                median_total_duration_seconds=run.get("median_total_duration_seconds", 0.0),
                p95_total_duration_seconds=run.get("p95_total_duration_seconds", 0.0),
                mean_queue_wait_seconds=run.get("mean_queue_wait_seconds", 0.0),
                max_queue_wait_seconds=run.get("max_queue_wait_seconds", 0.0),
                mean_throughput_mbps=run.get("mean_throughput_mbps", 0.0),
                deadline_success_rate=run.get("deadline_success_rate", 1.0),
                real_elapsed_seconds=run.get("real_elapsed_seconds", 0.0),
                records=records,
            )
        )

    aggregator = ResultAggregator(n_bootstrap=500)
    return aggregator.aggregate(results)


def _comparison_rows(data: dict[str, Any]) -> pd.DataFrame:
    """Normalize the compact AI-vs-heuristic comparison payload for display."""
    comparison = data.get("comparison")
    if not isinstance(comparison, dict):
        return pd.DataFrame()

    rows = []
    for key, label in (("heuristic", "RESOURCE_AWARE_HEURISTIC"), ("ai_assisted", "AI_ASSISTED")):
        rows.append({
            "Policy": label,
            "Deadline Success Rate": comparison.get("deadline_success_rate", {}).get(key),
            "Mean Duration (s)": comparison.get("mean_duration_seconds", {}).get(key),
            "P95 Duration (s)": comparison.get("p95_duration_seconds", {}).get(key),
            "Mean Throughput (Mbps)": comparison.get("mean_throughput_mbps", {}).get(key),
            "Mean Queue Wait (s)": comparison.get("mean_queue_wait_seconds", {}).get(key),
        })
    return pd.DataFrame(rows)


def render_experiments_page() -> None:
    """Renders the comprehensive Research Experiment Analytics page."""
    # -------------------------------------------------------------------------
    # Header & Strict Provenance Banner (Spec §16.3)
    # -------------------------------------------------------------------------
    st.markdown("## 🔬 Research Experiment Analytics")
    st.caption("Cross-Policy Comparative Benchmarking, Statistical Analysis & LaTeX/Markdown Reporting")

    st.info(
        "**Strict Provenance Notice (Spec §16.3)**: Experiment metrics, network traces, and QKD buffer states "
        "derive from discrete-event simulation models (SimPy/Q-SHIELD Engine). Provenance: `[PROVENANCE: SIMULATED]`. "
        "No live patient records or production keys are utilized.",
        icon="ℹ️",
    )

    # -------------------------------------------------------------------------
    # File Selection & Ingestion
    # -------------------------------------------------------------------------
    exp_files = _find_experiment_files()

    file_col1, file_col2 = st.columns([3, 1])

    with file_col1:
        selected_file: Path | None = None
        if exp_files:
            # Default to baseline_matrix.json if present
            default_idx = 0
            for idx, p in enumerate(exp_files):
                if p.name == "baseline_matrix.json":
                    default_idx = idx
                    break
            selected_file = st.selectbox(
                "📁 Select Experiment Benchmark File",
                options=exp_files,
                index=default_idx,
                format_func=lambda p: f"{p.name} ({p.stat().st_size / 1024:.1f} KB)",
            )
        else:
            st.warning("No experiment files found in `results/experiments/`. Please run `scripts/run_experiments.py`.")

    with file_col2:
        uploaded_file = st.file_uploader("Or upload JSON file", type=["json"], label_visibility="collapsed")

    raw_data: dict[str, Any] | None = None
    active_source_name = "None"

    if uploaded_file is not None:
        try:
            raw_data = json.load(uploaded_file)
            active_source_name = uploaded_file.name
        except Exception as exc:
            st.error(f"Failed to read uploaded file: {exc}")
    elif selected_file is not None:
        raw_data = _load_experiment_data(selected_file)
        active_source_name = selected_file.name

    if raw_data is None:
        st.info("ℹ️ Select or upload an experiment JSON file above to view benchmark analytics.")
        return

    # Extract records and statistical report
    df_records = _extract_records_dataframe(raw_data)
    report = _reconstruct_report(raw_data)
    comparison_df = _comparison_rows(raw_data)

    # -------------------------------------------------------------------------
    # KPI Metric Cards
    # -------------------------------------------------------------------------
    st.divider()
    c1, c2, c3, c4, c5 = st.columns(5)

    scenario_label = report.scenario_name if report else raw_data.get("scenario_name", "medical_mixed_workload")
    total_runs = report.total_runs if report else len(raw_data.get("runs", [])) or len(raw_data.get("seeds", []))
    policies_list = report.policies if report else list(df_records["policy_name"].unique()) if not df_records.empty else []
    total_records = len(df_records) if not df_records.empty else 0
    seeds_count = len(report.seeds) if (report and report.seeds) else len(raw_data.get("seeds", [1]))

    with c1:
        st.metric(label="📋 Scenario", value=scenario_label.replace("_", " ").title())
    with c2:
        st.metric(label="🔄 Total Simulation Runs", value=f"{total_runs}")
    with c3:
        st.metric(label="⚖️ Evaluated Policies", value=f"{len(policies_list)}")
    with c4:
        st.metric(label="📊 Simulated Requests", value=f"{total_records:,}")
    with c5:
        st.metric(label="🎲 Independent Seeds", value=f"{seeds_count}")

    if not comparison_df.empty:
        st.subheader("🤖 AI-Assisted vs Heuristic Comparison")
        st.caption(
            f"Predictor: {raw_data.get('predictor_version', 'N/A')} · "
            f"Seeds: {', '.join(map(str, raw_data.get('seeds', []))) or 'N/A'}"
        )
        st.dataframe(comparison_df, use_container_width=True, hide_index=True)
        predictor_eval = raw_data.get("predictor_evaluation")
        if isinstance(predictor_eval, dict):
            st.markdown("**Predictor evaluation**")
            eval_cols = st.columns(4)
            for col, (label, key) in zip(eval_cols, (("MAE (s)", "mae"), ("RMSE (s)", "rmse"), ("R²", "r2"), ("Samples", "count"))):
                value = predictor_eval.get(key)
                with col:
                    st.metric(label, f"{value:,}" if key == "count" and value is not None else f"{value:.4f}" if isinstance(value, (int, float)) else "N/A")

    # -------------------------------------------------------------------------
    # Filtering Controls
    # -------------------------------------------------------------------------
    st.divider()
    filter_col1, filter_col2 = st.columns(2)

    with filter_col1:
        selected_policies = st.multiselect(
            "Filter Active Policies",
            options=policies_list,
            default=policies_list,
        )

    filtered_df = df_records.copy()
    if selected_policies and not df_records.empty:
        filtered_df = filtered_df[filtered_df["policy_name"].isin(selected_policies)]

    with filter_col2:
        if not filtered_df.empty and "seed" in filtered_df.columns:
            available_seeds = sorted(filtered_df["seed"].unique())
            selected_seeds = st.multiselect(
                "Filter Simulation Seeds",
                options=available_seeds,
                default=available_seeds,
            )
            if selected_seeds:
                filtered_df = filtered_df[filtered_df["seed"].isin(selected_seeds)]

    # -------------------------------------------------------------------------
    # Statistical Summary Table
    # -------------------------------------------------------------------------
    st.subheader("📊 Cross-Policy Statistical Performance Matrix")
    if report and report.summaries:
        summary_rows: list[dict[str, Any]] = []
        for p_name in selected_policies:
            s = report.summaries.get(p_name)
            if s is None:
                continue
            dsr_ci = s.deadline_success_rate_ci
            dur_ci = s.mean_duration_ci
            p95_ci = s.p95_duration_ci

            rel_str = f"{s.relative_improvement_vs_fifo:+.1%}" if s.relative_improvement_vs_fifo is not None else "Baseline"

            summary_rows.append({
                "Policy": s.policy_name,
                "Runs": s.n_runs,
                "Total Requests": s.total_requests,
                "Mean Duration": f"{s.mean_duration_seconds:.2f} s ± {dur_ci.half_width:.2f} s",
                "P95 Duration": f"{s.p95_duration_seconds:.2f} s ± {p95_ci.half_width:.2f} s",
                "Deadline Success Rate": f"{s.deadline_success_rate:.1%} ± {dsr_ci.half_width:.1%}",
                "Mean Queue Wait": f"{s.mean_queue_wait_seconds:.2f} s",
                "Max Queue Wait": f"{s.max_queue_wait_seconds:.2f} s",
                "Throughput": f"{s.mean_throughput_mbps:.2f} Mbps",
                "DSR Gain vs FIFO": rel_str,
            })

        df_summary = pd.DataFrame(summary_rows)
        st.dataframe(df_summary, use_container_width=True, hide_index=True)

        if report.relative_improvements:
            with st.expander("📈 Relative Gains Breakdown vs FIFO Baseline", expanded=False):
                rel_rows: list[dict[str, Any]] = []
                for p_name, imps in report.relative_improvements.items():
                    if p_name in selected_policies:
                        rel_rows.append({
                            "Policy": p_name,
                            "Deadline Success Rate Δ": f"{imps.get('deadline_success_rate', 0.0):+.1%}",
                            "Mean Duration Δ (Lower is better)": f"{imps.get('mean_duration', 0.0):+.1%}",
                        })
                if rel_rows:
                    st.dataframe(pd.DataFrame(rel_rows), use_container_width=True, hide_index=True)
    elif not comparison_df.empty:
        st.info("This file contains aggregate AI-vs-heuristic results. The comparison table above shows the available policy metrics; run-level confidence intervals are not included in this export.")
    else:
        st.info("This file does not contain run-level records or aggregate summaries to build the statistical matrix.")

    # -------------------------------------------------------------------------
    # Visual Analytics Tabs
    # -------------------------------------------------------------------------
    st.divider()
    st.subheader("📈 Comparative Visual Analytics")

    tab_dur, tab_dsr, tab_tput, tab_starv, tab_export = st.tabs([
        "⏱️ Transfer Duration (Box Plots)",
        "🎯 Deadline Success Rate",
        "🌐 Throughput Timeline",
        "⏳ Starvation & Queue Wait",
        "📄 LaTeX & Markdown Export",
    ])

    with tab_dur:
        st.markdown("#### Transfer Duration Distribution by Policy")
        st.caption("Distribution box plots showing median, IQR, 95th percentile, and duration outliers.")
        fig_dur = plot_duration_boxplots(filtered_df)
        st.plotly_chart(fig_dur, use_container_width=True)

    with tab_dsr:
        st.markdown("#### Deadline Satisfaction Rate (%) with 95% Confidence Intervals")
        st.caption("Proportion of medical data transfers completing strictly prior to their SLA deadline.")
        fig_dsr = plot_deadline_success_bars(filtered_df)
        st.plotly_chart(fig_dsr, use_container_width=True)

    with tab_tput:
        st.markdown("#### Effective Network Throughput Dynamics Over Virtual Time")
        st.caption("Real-time transmission throughput (Mbps) across simulated quantum channel events.")
        fig_tput = plot_throughput_over_time(filtered_df)
        st.plotly_chart(fig_tput, use_container_width=True)

    with tab_starv:
        st.markdown("#### Queue Wait Time & Starvation Mitigation Analysis")
        st.caption("Histograms showing transfer request queue delay distributions across scheduling policies.")
        fig_starv = plot_starvation_histograms(filtered_df)
        st.plotly_chart(fig_starv, use_container_width=True)

    with tab_export:
        st.markdown("#### Publication-Ready Formats")
        st.caption("Export publication-quality LaTeX tables with `booktabs` and Markdown tables with 95% CI bounds.")

        if report:
            md_text = export_markdown_table(report, format_ci=True)
            tex_text = export_latex_table(report, format_ci=True)

            sub_col1, sub_col2 = st.columns(2)
            with sub_col1:
                st.markdown("**Markdown Table**")
                st.code(md_text, language="markdown")
                st.download_button(
                    label="⬇️ Download Markdown Report",
                    data=md_text,
                    file_name=f"{Path(active_source_name).stem}_report.md",
                    mime="text/markdown",
                    use_container_width=True,
                )

            with sub_col2:
                st.markdown("**LaTeX Table (Booktabs)**")
                st.code(tex_text, language="latex")
                st.download_button(
                    label="⬇️ Download LaTeX (.tex) Table",
                    data=tex_text,
                    file_name=f"{Path(active_source_name).stem}_table.tex",
                    mime="text/x-tex",
                    use_container_width=True,
                )
        else:
            st.info("Load an experiment file with aggregated summaries to generate export tables.")


__all__ = ["render_experiments_page"]
