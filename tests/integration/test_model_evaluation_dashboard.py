"""Integration and smoke tests for Model Evaluation Page and Charts (TASK-6.3)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.predictor.model_registry import ModelRegistry
from dashboard.components.charts import (
    plot_actual_vs_predicted_scatter,
    plot_feature_importances,
    plot_residual_distribution,
)
from dashboard.pages.model_evaluation import (
    _extract_model_feature_importances,
    _find_evaluation_datasets,
    _find_model_artifacts,
    render_model_evaluation_page,
)


def test_model_evaluation_charts_with_sample_data() -> None:
    """Verifies that all 3 model evaluation Plotly chart generators produce valid figures."""
    y_true = [0.5, 1.0, 1.5, 2.0, 2.5]
    y_pred = [0.52, 0.98, 1.49, 2.05, 2.48]
    categories = ["DIAGNOSTIC_REPORT", "DIAGNOSTIC_REPORT", "MEDICAL_IMAGE", "RESEARCH_DATASET", "RESEARCH_DATASET"]

    # 1. Scatter plot
    fig_scatter = plot_actual_vs_predicted_scatter(y_true, y_pred, categories=categories)
    assert fig_scatter is not None
    assert len(fig_scatter.data) >= 1

    # 2. Feature importances bar chart
    feature_names = ["data_size_bytes", "priority_level", "bandwidth"]
    importances = [0.75, 0.15, 0.10]
    fig_features = plot_feature_importances(feature_names, importances)
    assert fig_features is not None
    assert len(fig_features.data) >= 1

    # 3. Residual distribution histogram
    fig_res = plot_residual_distribution(y_true, y_pred)
    assert fig_res is not None
    assert len(fig_res.data) >= 1


def test_model_evaluation_charts_empty_graceful_handling() -> None:
    """Verifies that evaluation chart functions handle empty inputs without raising exceptions."""
    fig_scatter = plot_actual_vs_predicted_scatter([], [])
    assert fig_scatter is not None

    fig_features = plot_feature_importances([], [])
    assert fig_features is not None

    fig_res = plot_residual_distribution([], [])
    assert fig_res is not None


def test_extract_feature_importances_from_joblib() -> None:
    """Verifies feature importance extraction from the trained joblib artifact."""
    model_path = Path("models/duration_predictor_v1.joblib")
    assert model_path.exists(), "Joblib model artifact must exist for evaluation"

    predictor = ModelRegistry.load(model_path)
    feat_names, importances = _extract_model_feature_importances(predictor)

    assert len(feat_names) == 15
    assert len(importances) == 15
    assert np.isclose(sum(importances), 1.0, atol=0.01)
    assert "data_size_bytes" in feat_names


def test_find_model_artifacts_and_datasets() -> None:
    """Verifies model artifacts and evaluation datasets are discoverable on disk."""
    models = _find_model_artifacts()
    assert len(models) >= 1
    assert any(m.name == "duration_predictor_v1.joblib" for m in models)

    datasets = _find_evaluation_datasets()
    assert len(datasets) >= 1
    assert any(d.name == "baseline_matrix.json" for d in datasets)


def test_render_model_evaluation_page_headless() -> None:
    """Smoke test verifying render_model_evaluation_page loads joblib artifact and executes headlessly."""
    render_model_evaluation_page()
