"""ML Training Pipeline for transfer duration prediction (TASK-4.4).

Trains Ridge Regression, Random Forest, or Gradient Boosting models on simulation
telemetry. Implements seed-based train/test splitting to prevent data leakage,
cross-validation, and metrics tracking (MAE, RMSE, R^2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, cross_validate
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.predictor.evaluate import (
    EvaluationMetrics,
    compute_metrics,
    extract_features_from_record,
    load_dataset_records,
)
from app.predictor.features import FeatureExtractor

logger = logging.getLogger(__name__)


@dataclass
class TrainingResult:
    """Artifacts and metrics produced by model training."""

    model: Any
    model_type: str
    model_version: str
    feature_names: list[str]
    train_metrics: EvaluationMetrics
    test_metrics: EvaluationMetrics
    cv_mean_r2: float
    cv_mean_rmse: float
    test_seed: int
    train_seeds: list[int]
    hyperparameters: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """Convert training result to serializable metadata dict."""
        return {
            "model_type": self.model_type,
            "model_version": self.model_version,
            "feature_names": self.feature_names,
            "train_metrics": self.train_metrics.to_dict(),
            "test_metrics": self.test_metrics.to_dict(),
            "cv_mean_r2": round(self.cv_mean_r2, 4),
            "cv_mean_rmse": round(self.cv_mean_rmse, 4),
            "test_seed": self.test_seed,
            "train_seeds": self.train_seeds,
            "hyperparameters": self.hyperparameters,
        }


def build_estimator(model_type: str) -> tuple[Any, dict[str, Any]]:
    """Build scikit-learn regressor pipeline and parameter dictionary."""
    model_type_clean = model_type.lower().strip()

    if model_type_clean == "ridge":
        params = {"alpha": 10.0, "random_state": 42}
        estimator = Pipeline([
            ("scaler", StandardScaler()),
            ("regressor", Ridge(**params)),
        ])
        return estimator, params

    if model_type_clean in ("random_forest", "rf"):
        params = {
            "n_estimators": 100,
            "max_depth": 10,
            "min_samples_split": 4,
            "random_state": 42,
            "n_jobs": -1,
        }
        return RandomForestRegressor(**params), params

    if model_type_clean in ("gradient_boosting", "gbr"):
        params = {
            "n_estimators": 120,
            "learning_rate": 0.08,
            "max_depth": 4,
            "min_samples_split": 4,
            "random_state": 42,
        }
        return GradientBoostingRegressor(**params), params

    raise ValueError(
        f"Unsupported model_type: '{model_type}'. "
        "Supported types: 'ridge', 'random_forest', 'gradient_boosting'."
    )


def prepare_matrices(
    records: list[dict[str, Any]],
    target_field: str = "transfer_duration_seconds",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert raw simulation records into (X, y, seeds) numpy arrays."""
    feature_vectors: list[np.ndarray] = []
    targets: list[float] = []
    seeds: list[int] = []

    for r in records:
        target_val = float(r.get(target_field, r.get("total_duration_seconds", 0.0)))
        features = extract_features_from_record(r)
        vec = FeatureExtractor.to_vector(features)

        feature_vectors.append(vec)
        targets.append(target_val)
        seeds.append(int(r.get("_run_seed", 0)))

    X = np.asarray(feature_vectors, dtype=np.float64)
    y = np.asarray(targets, dtype=np.float64)
    seed_arr = np.asarray(seeds, dtype=np.int64)

    return X, y, seed_arr


def train_duration_model(
    data_path: str | Path,
    model_type: str = "gradient_boosting",
    test_seed: int | None = None,
    target_field: str = "transfer_duration_seconds",
    cv_folds: int = 5,
    version_tag: str = "1.0.0",
) -> TrainingResult:
    """Execute complete training pipeline with seed-based isolation and CV.

    Parameters
    ----------
    data_path:
        Path to JSON file containing simulation records.
    model_type:
        'ridge', 'random_forest', or 'gradient_boosting'.
    test_seed:
        Seed to hold out for evaluation. If None, the largest seed in dataset is held out.
    target_field:
        Target column ('transfer_duration_seconds' or 'total_duration_seconds').
    cv_folds:
        Number of cross-validation splits.
    version_tag:
        Semantic version string for model metadata.
    """
    records = load_dataset_records(data_path)
    if not records:
        raise ValueError(f"No transfer records found in dataset {data_path}")

    X, y, seeds = prepare_matrices(records, target_field=target_field)
    unique_seeds = sorted(set(seeds.tolist()))

    if len(unique_seeds) > 1:
        chosen_test_seed = test_seed if test_seed is not None else unique_seeds[-1]
        train_mask = seeds != chosen_test_seed
        test_mask = seeds == chosen_test_seed
    else:
        # Single seed fallback (random split)
        chosen_test_seed = unique_seeds[0]
        n_samples = len(X)
        split_idx = int(n_samples * 0.8)
        indices = np.random.RandomState(42).permutation(n_samples)
        train_idx, _test_idx = indices[:split_idx], indices[split_idx:]
        train_mask = np.zeros(n_samples, dtype=bool)
        train_mask[train_idx] = True
        test_mask = ~train_mask

    X_train, y_train = X[train_mask], y[train_mask]
    X_test, y_test = X[test_mask], y[test_mask]
    train_seeds = [s for s in unique_seeds if s != chosen_test_seed] or unique_seeds

    logger.info(
        "Train set: %d samples (seeds %s) | Test set: %d samples (seed %d)",
        len(X_train), train_seeds, len(X_test), chosen_test_seed,
    )

    estimator, hyperparameters = build_estimator(model_type)

    # Cross-validation on training partition
    n_splits = min(cv_folds, max(2, len(X_train) // 10))
    cv = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    cv_scores = cross_validate(
        estimator,
        X_train,
        y_train,
        cv=cv,
        scoring={"r2": "r2", "neg_rmse": "neg_root_mean_squared_error"},
        n_jobs=-1,
    )
    cv_mean_r2 = float(np.mean(cv_scores["test_r2"]))
    cv_mean_rmse = float(-np.mean(cv_scores["test_neg_rmse"]))

    # Final fit on entire train partition
    estimator.fit(X_train, y_train)

    # Evaluate predictions
    y_pred_train = estimator.predict(X_train)
    y_pred_test = estimator.predict(X_test)

    train_metrics = compute_metrics(y_train, y_pred_train)
    test_metrics = compute_metrics(y_test, y_pred_test)

    model_version = f"{model_type}-v{version_tag}"

    logger.info(
        "Training complete: test R^2=%.4f, test RMSE=%.4f (target=%s)",
        test_metrics.r2, test_metrics.rmse, target_field,
    )

    return TrainingResult(
        model=estimator,
        model_type=model_type,
        model_version=model_version,
        feature_names=FeatureExtractor.get_feature_names(),
        train_metrics=train_metrics,
        test_metrics=test_metrics,
        cv_mean_r2=cv_mean_r2,
        cv_mean_rmse=cv_mean_rmse,
        test_seed=chosen_test_seed,
        train_seeds=train_seeds,
        hyperparameters=hyperparameters,
    )


__all__ = [
    "TrainingResult",
    "build_estimator",
    "prepare_matrices",
    "train_duration_model",
]
