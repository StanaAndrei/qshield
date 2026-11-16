"""Q-SHIELD Main Dashboard Application (TASK-6.1).

Multi-page Streamlit application providing live operational monitoring,
QKD link buffer visualization, research experiment analytics, and model evaluation.
"""

from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger(__name__)

# Configure page configuration at the entrypoint
st.set_page_config(
    page_title="Q-SHIELD | Quantum-Secured Medical Orchestration",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _render_operations() -> None:
    """Renders the Operations Monitor page."""
    from dashboard.pages.operations import render_operations_page

    render_operations_page()


def _render_experiments_placeholder() -> None:
    """Renders the Experiment Analytics page or placeholder if not yet implemented."""
    try:
        import importlib

        mod = importlib.import_module("dashboard.pages.experiments")
        render_fn = mod.render_experiments_page
        render_fn()
    except (ImportError, AttributeError):
        st.markdown("## 🔬 Research Experiment Analytics")
        st.caption("Cross-policy comparative benchmarking and statistical evaluation")
        st.info(
            "The Research Experiment Analytics page will be available under **Milestone 6 (TASK-6.2)**. "
            "It visualizes benchmark runs across FIFO, Priority, Heuristic, and AI-assisted schedulers.",
            icon="ℹ️",
        )


def _render_model_evaluation_placeholder() -> None:
    """Renders the Model Evaluation page or placeholder if not yet implemented."""
    try:
        import importlib

        mod = importlib.import_module("dashboard.pages.model_evaluation")
        render_fn = mod.render_model_evaluation_page
        render_fn()
    except (ImportError, AttributeError):
        st.markdown("## 🤖 Predictor Performance & Feature Importance")
        st.caption("Gradient Boosting duration estimator and residual error metrics")
        st.info(
            "The Predictor Performance view will be available under **Milestone 6 (TASK-6.3)**. "
            "It renders actual-vs-predicted scatter plots, MAE/RMSE, and Gini feature importance curves.",
            icon="ℹ️",
        )


def main() -> None:
    """Configures multi-page navigation and renders the active view."""
    # Sidebar header & branding
    with st.sidebar:
        st.markdown("### 🛡️ **Q-SHIELD Orchestrator**")
        st.caption("AI-Driven Quantum-Secured Medical Data Exchange")
        st.divider()

    # Define navigation pages
    operations_page = st.Page(
        _render_operations,
        title="Operations Monitor",
        icon="🛡️",
        default=True,
    )
    experiments_page = st.Page(
        _render_experiments_placeholder,
        title="Experiment Analytics",
        icon="🔬",
    )
    models_page = st.Page(
        _render_model_evaluation_placeholder,
        title="Model Evaluation",
        icon="🤖",
    )

    nav = st.navigation(
        {
            "Real-Time Operations": [operations_page],
            "Research & Analytics": [experiments_page, models_page],
        }
    )

    # Sidebar footer with strict provenance notice (Spec §16.3)
    with st.sidebar:
        st.markdown("---")
        st.caption("**System Mode**: `Simulation & Evaluation`")
        st.caption("**Provenance**: Synthetic Medical Workloads & Discrete-Event QKD Buffers [SIM]")

    nav.run()


if __name__ == "__main__":
    main()
