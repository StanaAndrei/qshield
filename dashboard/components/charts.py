"""Plotly Chart Components for Q-SHIELD Dashboard Research Analytics (TASK-6.2).

Provides high-performance, interactive visualizations for cross-policy simulation benchmarks:
- Transfer duration distribution box plots
- Deadline success rate bar charts with confidence intervals
- Throughput timeline scatter/line dynamics over virtual simulation time
- Queue wait time and starvation mitigation histograms
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Consistent color palette for policies across charts
POLICY_COLOR_MAP: dict[str, str] = {
    "FIFO": "#636EFA",
    "PRIORITY": "#EF553B",
    "RESOURCE_AWARE_HEURISTIC": "#00CC96",
    "AI_ASSISTED": "#AB63FA",
}

DEFAULT_PALETTE = ["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A", "#19D3F3", "#FF6692"]


def apply_chart_styling(fig: go.Figure, title: str | None = None) -> go.Figure:
    """Applies clean, responsive theme and typography to a Plotly figure."""
    fig.update_layout(
        template="plotly_white",
        title={
            "text": title or fig.layout.title.text or "",
            "font": {"size": 16, "family": "Inter, Roboto, sans-serif"},
            "x": 0.02,
            "xanchor": "left",
        },
        font={"family": "Inter, Roboto, sans-serif", "size": 12},
        margin={"l": 40, "r": 40, "t": 60, "b": 40},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1.0,
            "title": {"text": ""},
        },
        hoverlabel={"font_size": 12, "font_family": "Inter, Roboto, sans-serif"},
    )
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="rgba(200, 200, 200, 0.2)")
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor="rgba(200, 200, 200, 0.2)")
    return fig


def _ensure_dataframe(data: pd.DataFrame | list[dict[str, Any]]) -> pd.DataFrame:
    """Ensures input is a pandas DataFrame."""
    if isinstance(data, pd.DataFrame):
        return data
    if isinstance(data, list):
        return pd.DataFrame(data)
    return pd.DataFrame()


def plot_duration_boxplots(data: pd.DataFrame | list[dict[str, Any]]) -> go.Figure:
    """Generates transfer duration distribution box plots grouped by scheduling policy.

    Args:
        data: DataFrame or list of dicts containing either fine-grained transfer records
            (`policy_name`, `total_duration_seconds`) or run-level metrics.

    Returns:
        Plotly Figure displaying comparative duration box plots.
    """
    df = _ensure_dataframe(data)
    if df.empty or "policy_name" not in df.columns:
        fig = go.Figure()
        fig.add_annotation(
            text="No duration records available for visualization",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font={"size": 14, "color": "gray"},
        )
        return apply_chart_styling(fig, "Transfer Duration Distribution")

    # Check whether fine-grained record column or run-level summary column is available
    duration_col = None
    for candidate in ("total_duration_seconds", "transfer_duration_seconds", "mean_total_duration_seconds", "mean_duration_seconds"):
        if candidate in df.columns:
            duration_col = candidate
            break

    if duration_col is None:
        fig = go.Figure()
        fig.add_annotation(
            text="Duration metric column not found in dataset",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return apply_chart_styling(fig, "Transfer Duration Distribution")

    color_discrete_map = {k: v for k, v in POLICY_COLOR_MAP.items() if k in df["policy_name"].unique()}

    fig = px.box(
        df,
        x="policy_name",
        y=duration_col,
        color="policy_name",
        color_discrete_map=color_discrete_map or None,
        points="outliers",
        labels={
            "policy_name": "Scheduling Policy",
            duration_col: "Total Duration (seconds)",
        },
        title="Transfer Duration Distribution Across Scheduling Policies",
    )
    fig.update_traces(boxmean=True)  # Display dashed mean line inside boxes
    return apply_chart_styling(fig)


def plot_deadline_success_bars(
    data: pd.DataFrame | list[dict[str, Any]],
) -> go.Figure:
    """Generates bar chart comparing deadline success rates with optional CI error bounds.

    Args:
        data: DataFrame or list of dicts with policy summaries or run records.

    Returns:
        Plotly Figure displaying deadline success rates.
    """
    df = _ensure_dataframe(data)
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(
            text="No deadline success records available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return apply_chart_styling(fig, "Deadline Success Rate")

    # If df has run records with deadline_met bool, aggregate by policy
    if "deadline_success_rate" not in df.columns and "deadline_met" in df.columns and "policy_name" in df.columns:
        summary_df = (
            df.groupby("policy_name")["deadline_met"]
            .agg(deadline_success_rate="mean", total_requests="count")
            .reset_index()
        )
    elif "deadline_success_rate" in df.columns and "policy_name" in df.columns:
        # If multiple runs per policy exist, group by policy
        if df.duplicated(subset=["policy_name"]).any():
            summary_df = (
                df.groupby("policy_name")["deadline_success_rate"]
                .agg(deadline_success_rate="mean", ci_error="std")
                .reset_index()
            )
            summary_df["ci_error"] = summary_df["ci_error"].fillna(0.0)
        else:
            summary_df = df.copy()
            if "ci_half_width" in summary_df.columns:
                summary_df["ci_error"] = summary_df["ci_half_width"]
            else:
                summary_df["ci_error"] = 0.0
    else:
        fig = go.Figure()
        fig.add_annotation(text="Deadline success rate data missing", showarrow=False)
        return apply_chart_styling(fig, "Deadline Success Rate")

    # Convert to percentage
    summary_df["success_rate_pct"] = summary_df["deadline_success_rate"] * 100.0
    if "ci_error" in summary_df.columns:
        summary_df["ci_error_pct"] = summary_df["ci_error"] * 100.0
    else:
        summary_df["ci_error_pct"] = 0.0

    color_discrete_map = {k: v for k, v in POLICY_COLOR_MAP.items() if k in summary_df["policy_name"].unique()}

    error_y = "ci_error_pct" if summary_df["ci_error_pct"].sum() > 0 else None

    fig = px.bar(
        summary_df,
        x="policy_name",
        y="success_rate_pct",
        color="policy_name",
        color_discrete_map=color_discrete_map or None,
        error_y=error_y,
        text=summary_df["success_rate_pct"].apply(lambda v: f"{v:.1f}%"),
        labels={
            "policy_name": "Scheduling Policy",
            "success_rate_pct": "Deadline Success Rate (%)",
        },
        title="Deadline Satisfaction Rate Across Policies (95% CI Bounds)",
    )
    fig.update_traces(textposition="outside")
    fig.update_yaxes(range=[0, 115])  # Room for labels
    return apply_chart_styling(fig)


def plot_throughput_over_time(data: pd.DataFrame | list[dict[str, Any]]) -> go.Figure:
    """Generates scatter / timeline chart showing effective throughput across virtual simulation time.

    Args:
        data: DataFrame or list of dicts with fine-grained records
            (`actual_throughput_mbps`, `completion_time` or `start_time`, `policy_name`).

    Returns:
        Plotly Figure showing throughput dynamics.
    """
    df = _ensure_dataframe(data)
    if df.empty or "policy_name" not in df.columns:
        fig = go.Figure()
        fig.add_annotation(
            text="No throughput timeline records available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return apply_chart_styling(fig, "Throughput Over Time")

    time_col = None
    for candidate in ("completion_time", "start_time", "arrival_time"):
        if candidate in df.columns:
            time_col = candidate
            break

    throughput_col = None
    for candidate in ("actual_throughput_mbps", "mean_throughput_mbps"):
        if candidate in df.columns:
            throughput_col = candidate
            break

    if time_col is None or throughput_col is None:
        fig = go.Figure()
        fig.add_annotation(text="Time or throughput metrics missing", showarrow=False)
        return apply_chart_styling(fig, "Throughput Over Time")

    hover_cols = [c for c in ("request_id", "data_category", "data_size_bytes", "priority") if c in df.columns]

    color_discrete_map = {k: v for k, v in POLICY_COLOR_MAP.items() if k in df["policy_name"].unique()}

    fig = px.scatter(
        df,
        x=time_col,
        y=throughput_col,
        color="policy_name",
        color_discrete_map=color_discrete_map or None,
        hover_data=hover_cols or None,
        opacity=0.75,
        labels={
            time_col: "Virtual Simulation Time (seconds)",
            throughput_col: "Effective Throughput (Mbps)",
            "policy_name": "Scheduling Policy",
        },
        title="Effective Network Throughput Dynamics Over Virtual Time (Mbps)",
    )
    fig.update_traces(marker={"size": 6})
    return apply_chart_styling(fig)


def plot_starvation_histograms(
    data: pd.DataFrame | list[dict[str, Any]],
) -> go.Figure:
    """Generates histogram comparing queue wait times and starvation bounds across policies.

    Args:
        data: DataFrame or list of dicts with records containing `queue_time` and `policy_name`.

    Returns:
        Plotly Figure displaying wait time distribution histograms.
    """
    df = _ensure_dataframe(data)
    if df.empty or "policy_name" not in df.columns:
        fig = go.Figure()
        fig.add_annotation(
            text="No queue wait records available for starvation analysis",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
        return apply_chart_styling(fig, "Starvation Wait Time Distribution")

    wait_col = None
    for candidate in ("queue_time", "mean_queue_wait_seconds", "max_queue_wait_seconds"):
        if candidate in df.columns:
            wait_col = candidate
            break

    if wait_col is None:
        fig = go.Figure()
        fig.add_annotation(text="Queue wait time metric not found", showarrow=False)
        return apply_chart_styling(fig, "Starvation Wait Time Distribution")

    color_discrete_map = {k: v for k, v in POLICY_COLOR_MAP.items() if k in df["policy_name"].unique()}

    fig = px.histogram(
        df,
        x=wait_col,
        color="policy_name",
        color_discrete_map=color_discrete_map or None,
        barmode="overlay",
        marginal="box",
        opacity=0.65,
        nbins=35,
        labels={
            wait_col: "Queue Wait Time (seconds)",
            "policy_name": "Scheduling Policy",
            "count": "Transfer Request Count",
        },
        title="Queue Wait Time & Starvation Analysis Across Scheduling Policies",
    )
    return apply_chart_styling(fig)


def plot_actual_vs_predicted_scatter(
    y_true: list[float] | np.ndarray,
    y_pred: list[float] | np.ndarray,
    categories: list[str] | None = None,
) -> go.Figure:
    """Generates duration prediction scatter plot with ideal calibration reference line.

    Args:
        y_true: Ground-truth measured transfer durations.
        y_pred: Predicted transfer durations from ML model.
        categories: Optional medical data category labels for color-coding.

    Returns:
        Plotly Figure showing actual vs. predicted durations.
    """
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)

    if len(yt) == 0 or len(yp) == 0:
        fig = go.Figure()
        fig.add_annotation(text="No prediction samples available for scatter visualization", showarrow=False)
        return apply_chart_styling(fig, "Actual vs. Predicted Transfer Duration")

    df_plot = pd.DataFrame({
        "actual": yt,
        "predicted": yp,
        "absolute_error": np.abs(yt - yp),
    })

    color_col = None
    if categories is not None and len(categories) == len(yt):
        df_plot["category"] = categories
        color_col = "category"

    fig = px.scatter(
        df_plot,
        x="actual",
        y="predicted",
        color=color_col,
        hover_data=["absolute_error"],
        opacity=0.75,
        labels={
            "actual": "Actual Transfer Duration (seconds)",
            "predicted": "Predicted Duration (seconds)",
            "category": "Medical Category",
            "absolute_error": "Absolute Error (s)",
        },
        title="Actual vs. Predicted Transfer Duration (Calibration Curve)",
    )

    # Reference 45-degree line y = x
    min_val = min(float(np.min(yt)), float(np.min(yp)))
    max_val = max(float(np.max(yt)), float(np.max(yp)))
    padding = (max_val - min_val) * 0.05
    line_min = max(0.0, min_val - padding)
    line_max = max_val + padding

    fig.add_trace(
        go.Scatter(
            x=[line_min, line_max],
            y=[line_min, line_max],
            mode="lines",
            line={"color": "rgba(220, 50, 50, 0.8)", "dash": "dash", "width": 2},
            name="Ideal Prediction (y = x)",
        )
    )

    return apply_chart_styling(fig)


def plot_feature_importances(
    feature_names: list[str],
    importances: list[float] | np.ndarray,
    title: str | None = None,
) -> go.Figure:
    """Generates horizontal bar chart of feature importances sorted in descending order.

    Args:
        feature_names: List of input feature names.
        importances: Gini impurity / MDI scores or normalized coefficients.
        title: Optional custom chart title.

    Returns:
        Plotly Figure displaying feature importance ranking.
    """
    if len(feature_names) == 0 or len(importances) == 0:
        fig = go.Figure()
        fig.add_annotation(text="No feature importance metrics available", showarrow=False)
        return apply_chart_styling(fig, "Predictor Feature Importance Ranking")

    imp_arr = np.asarray(importances, dtype=np.float64)
    # Normalize if values don't sum to ~1
    total_imp = float(np.sum(np.abs(imp_arr)))
    if total_imp > 0 and not np.isclose(total_imp, 1.0, atol=0.05):
        norm_imp = np.abs(imp_arr) / total_imp
    else:
        norm_imp = imp_arr

    df_imp = pd.DataFrame({
        "feature": [f.replace("_", " ").title() for f in feature_names],
        "importance": norm_imp,
    })
    # Sort ascending for horizontal bar chart (top at top)
    df_imp = df_imp.sort_values(by="importance", ascending=True)

    fig = px.bar(
        df_imp,
        x="importance",
        y="feature",
        orientation="h",
        text=df_imp["importance"].apply(lambda v: f"{v * 100:.1f}%" if v <= 1.0 else f"{v:.2f}"),
        color="importance",
        color_continuous_scale="Blues",
        labels={
            "importance": "Relative Importance (MDI / Gini)",
            "feature": "Input Feature",
        },
        title=title or "Predictor Feature Importance Ranking (Gini Impurity / MDI)",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(coloraxis_showscale=False)
    return apply_chart_styling(fig)


def plot_residual_distribution(
    y_true: list[float] | np.ndarray,
    y_pred: list[float] | np.ndarray,
) -> go.Figure:
    """Generates histogram of prediction residuals (y - y_hat) centered around zero.

    Args:
        y_true: Ground-truth measured transfer durations.
        y_pred: Predicted transfer durations from ML model.

    Returns:
        Plotly Figure showing residual error distribution.
    """
    yt = np.asarray(y_true, dtype=np.float64)
    yp = np.asarray(y_pred, dtype=np.float64)

    if len(yt) == 0 or len(yp) == 0:
        fig = go.Figure()
        fig.add_annotation(text="No prediction residuals available", showarrow=False)
        return apply_chart_styling(fig, "Residual Error Distribution")

    residuals = yt - yp
    df_res = pd.DataFrame({"residual": residuals})

    fig = px.histogram(
        df_res,
        x="residual",
        marginal="box",
        nbins=40,
        opacity=0.75,
        color_discrete_sequence=["#AB63FA"],
        labels={"residual": "Residual Error (seconds, Actual - Predicted)"},
        title="Duration Prediction Residual Error Distribution (s)",
    )

    # Vertical line at zero error
    fig.add_vline(x=0.0, line_dash="dash", line_color="red", annotation_text="Zero Error")
    return apply_chart_styling(fig)


__all__ = [
    "POLICY_COLOR_MAP",
    "apply_chart_styling",
    "plot_actual_vs_predicted_scatter",
    "plot_deadline_success_bars",
    "plot_duration_boxplots",
    "plot_feature_importances",
    "plot_residual_distribution",
    "plot_starvation_histograms",
    "plot_throughput_over_time",
]
