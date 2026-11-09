"""Evaluation and benchmarking module for transfer performance predictors (TASK-4.4).

Computes MAE, RMSE, and R^2 evaluation metrics globally and broken down
by data category and network condition. Supports CLI invocation:
    python -m app.predictor.evaluate --model models/... --test-data results/...
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import mean_absolute_error, r2_score

from app.models.scheduling import PredictionFeatures
from app.predictor.baseline import AnalyticalPredictor
from app.scheduler.base import IPredictor


@dataclass
class EvaluationMetrics:
    """Statistical summary of prediction accuracy."""

    mae: float
    rmse: float
    r2: float
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mae": round(self.mae, 4),
            "rmse": round(self.rmse, 4),
            "r2": round(self.r2, 4),
            "count": self.count,
        }


def compute_metrics(
    y_true: np.ndarray | list[float],
    y_pred: np.ndarray | list[float],
) -> EvaluationMetrics:
    """Calculate MAE, RMSE, and R^2 across true and predicted values."""
    y_t = np.asarray(y_true, dtype=np.float64)
    y_p = np.asarray(y_pred, dtype=np.float64)

    if len(y_t) == 0:
        return EvaluationMetrics(mae=0.0, rmse=0.0, r2=0.0, count=0)

    mae = float(mean_absolute_error(y_t, y_p))
    mse = float(np.mean((y_t - y_p) ** 2))
    rmse = math.sqrt(mse)

    # R2 can be undefined if variance of y_true is 0
    if len(y_t) > 1 and np.var(y_t) > 1e-9:
        r2 = float(r2_score(y_t, y_p))
    else:
        r2 = 1.0 if np.allclose(y_t, y_p) else 0.0

    return EvaluationMetrics(mae=mae, rmse=rmse, r2=r2, count=len(y_t))


def extract_features_from_record(record: dict[str, Any]) -> PredictionFeatures:
    """Reconstruct PredictionFeatures from a serialized simulation record."""
    size_bytes = int(record["data_size_bytes"])
    priority_val = int(record.get("priority", 20))

    # Reconstruct or infer throughput
    actual_tp = float(record.get("actual_throughput_mbps", 100.0))
    if actual_tp <= 0.0:
        actual_tp = 100.0

    queue_wait = float(record.get("queue_time", 0.0))
    queue_len = max(0, int(queue_wait / 2.0))

    # Deadline slack
    deadline_slack = 86400.0
    if record.get("deadline") is not None:
        # Total duration + slack margin
        tot_dur = float(record.get("total_duration_seconds", 10.0))
        deadline_slack = max(0.1, tot_dur * 1.5)

    return PredictionFeatures(
        data_size_bytes=size_bytes,
        priority_level=priority_val,
        queue_length=queue_len,
        active_transfer_count=1,
        recent_throughput_mbps=actual_tp,
        throughput_mean=100.0,
        throughput_std=15.0,
        deadline_slack_seconds=deadline_slack,
        link_status_connected=True,
        recent_failure_rate=0.001,
        estimated_available_bandwidth_mbps=actual_tp,
        key_service_latency_ms=10.0,
        key_resource_level=1000,
        key_replenishment_rate_bps=2560.0,
    )


def load_dataset_records(data_path: str | Path) -> list[dict[str, Any]]:
    """Load flat list of transfer records from a simulation results JSON."""
    data_path = Path(data_path)
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset not found: {data_path}")

    with data_path.open() as f:
        data = json.load(f)

    records: list[dict[str, Any]] = []
    if "runs" in data:
        for run in data["runs"]:
            seed = run.get("seed", 0)
            policy = run.get("policy_name", "UNKNOWN")
            for rec in run.get("records", []):
                rec_copy = dict(rec)
                rec_copy["_run_seed"] = seed
                rec_copy["_run_policy"] = policy
                records.append(rec_copy)
    elif "records" in data:
        records.extend(data["records"])
    elif isinstance(data, list):
        records.extend(data)

    return records


def evaluate_predictor(
    predictor: IPredictor,
    records: list[dict[str, Any]],
    target_field: str = "transfer_duration_seconds",
) -> tuple[EvaluationMetrics, dict[str, EvaluationMetrics]]:
    """Evaluate a predictor against transfer records.

    Returns:
        (global_metrics, category_breakdown)
    """
    y_true: list[float] = []
    y_pred: list[float] = []
    categories: list[str] = []

    for r in records:
        target_val = float(r.get(target_field, r.get("total_duration_seconds", 0.0)))
        features = extract_features_from_record(r)
        pred_out = predictor.predict(features)

        y_true.append(target_val)
        y_pred.append(pred_out.predicted_duration_seconds)
        categories.append(r.get("data_category", "UNKNOWN"))

    global_metrics = compute_metrics(y_true, y_pred)

    # Category breakdown
    category_breakdown: dict[str, EvaluationMetrics] = {}
    unique_cats = sorted(set(categories))
    y_true_arr = np.array(y_true)
    y_pred_arr = np.array(y_pred)
    cat_arr = np.array(categories)

    for cat in unique_cats:
        mask = cat_arr == cat
        category_breakdown[cat] = compute_metrics(y_true_arr[mask], y_pred_arr[mask])

    return global_metrics, category_breakdown


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Q-SHIELD predictor accuracy.")
    parser.add_argument("--model", required=True, help="Path to joblib model artifact.")
    parser.add_argument("--test-data", required=True, help="Path to simulation JSON dataset.")
    parser.add_argument(
        "--target",
        default="transfer_duration_seconds",
        choices=["transfer_duration_seconds", "total_duration_seconds"],
        help="Target duration metric to evaluate against.",
    )

    args = parser.parse_args()

    # Load model
    from app.predictor.model_registry import ModelRegistry
    model_path = Path(args.model)
    if not model_path.exists():
        print(f"ERROR: Model file not found: {model_path}", file=sys.stderr)
        return 1

    predictor = ModelRegistry.load(model_path)
    print(f"Loaded predictor: {predictor.get_model_version()} from {model_path}")

    # Load test dataset
    records = load_dataset_records(args.test_data)
    if not records:
        print(f"ERROR: No transfer records found in {args.test_data}", file=sys.stderr)
        return 1
    print(f"Loaded {len(records)} test transfer records from {args.test_data}")

    # Evaluate ML Predictor
    ml_metrics, ml_breakdown = evaluate_predictor(predictor, records, target_field=args.target)

    # Evaluate Analytical Baseline for comparison
    analytical = AnalyticalPredictor()
    base_metrics, base_breakdown = evaluate_predictor(analytical, records, target_field=args.target)

    # Print Report Table
    print("\n" + "=" * 70)
    print("PREDICTOR EVALUATION REPORT")
    print("=" * 70)
    print(f"{'Metric':<25} {'ML Predictor':<20} {'Analytical Baseline':<20}")
    print("-" * 70)
    print(f"{'MAE (seconds)':<25} {ml_metrics.mae:<20.4f} {base_metrics.mae:<20.4f}")
    print(f"{'RMSE (seconds)':<25} {ml_metrics.rmse:<20.4f} {base_metrics.rmse:<20.4f}")
    print(f"{'R² Score':<25} {ml_metrics.r2:<20.4f} {base_metrics.r2:<20.4f}")
    print(f"{'Sample Count':<25} {ml_metrics.count:<20} {base_metrics.count:<20}")
    print("-" * 70)

    print("\nBREAKDOWN BY DATA CATEGORY (ML Predictor):")
    print(f"{'Category':<25} {'MAE (s)':<12} {'RMSE (s)':<12} {'R²':<12} {'Count':<8}")
    print("-" * 70)
    for cat, m in ml_breakdown.items():
        print(f"{cat:<25} {m.mae:<12.4f} {m.rmse:<12.4f} {m.r2:<12.4f} {m.count:<8}")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
