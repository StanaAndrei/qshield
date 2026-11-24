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


def _render_experiments() -> None:
    """Renders the Research Experiment Analytics page (TASK-6.2)."""
    from dashboard.pages.experiments import render_experiments_page

    render_experiments_page()


def _render_model_evaluation() -> None:
    """Renders the Model Evaluation & Feature Importance page (TASK-6.3)."""
    from dashboard.pages.model_evaluation import render_model_evaluation_page

    render_model_evaluation_page()


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
        _render_experiments,
        title="Experiment Analytics",
        icon="🔬",
    )
    models_page = st.Page(
        _render_model_evaluation,
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
