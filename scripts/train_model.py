#!/usr/bin/env python3
"""CLI script to train and serialize Q-SHIELD transfer duration predictors (TASK-4.5).

Usage:
    python scripts/train_model.py \
        --data results/experiments/baseline_matrix.json \
        --model-type gradient_boosting \
        --output models/duration_predictor_v1.joblib
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.predictor.model_registry import ModelRegistry
from app.predictor.train import train_duration_model


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train and register a machine learning predictor for transfer durations.",
    )
    parser.add_argument(
        "--data",
        required=True,
        help="Path to JSON simulation dataset (e.g. results/experiments/baseline_matrix.json).",
    )
    parser.add_argument(
        "--model-type",
        default="gradient_boosting",
        choices=["ridge", "random_forest", "gradient_boosting"],
        help="Regressor algorithm to train (default: gradient_boosting).",
    )
    parser.add_argument(
        "--output",
        default="models/duration_predictor_v1.joblib",
        help="Path where the trained joblib model artifact will be saved.",
    )
    parser.add_argument(
        "--target",
        default="transfer_duration_seconds",
        choices=["transfer_duration_seconds", "total_duration_seconds"],
        help="Target metric to fit (default: transfer_duration_seconds).",
    )
    parser.add_argument(
        "--test-seed",
        type=int,
        default=None,
        help="Seed held out for isolated test evaluation (default: max seed).",
    )
    parser.add_argument(
        "--cv-folds",
        type=int,
        default=5,
        help="Number of cross-validation folds on training partition (default: 5).",
    )

    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"ERROR: Dataset not found: {data_path}", file=sys.stderr)
        return 1

    print(f"Training {args.model_type} regressor on {data_path}...")
    print(f"Target metric: {args.target}")

    try:
        result = train_duration_model(
            data_path=data_path,
            model_type=args.model_type,
            test_seed=args.test_seed,
            target_field=args.target,
            cv_folds=args.cv_folds,
        )
    except Exception as exc:
        print(f"ERROR: Training failed: {exc}", file=sys.stderr)
        return 1

    # Persist model and metadata sidecar
    out_path = Path(args.output)
    saved_path = ModelRegistry.save(result, out_path)

    # Print summary
    print("\n" + "=" * 60)
    print("TRAINING AND VALIDATION RESULTS")
    print("=" * 60)
    print(f"Model Algorithm:    {result.model_type}")
    print(f"Model Version:      {result.model_version}")
    print(f"Artifact Saved To:  {saved_path}")
    print(f"Sidecar Metadata:   {saved_path.with_suffix('.json')}")
    print("-" * 60)
    print(f"Train Seeds:        {result.train_seeds}")
    print(f"Holdout Test Seed:  {result.test_seed}")
    print("-" * 60)
    print(f"CV Mean R²:         {result.cv_mean_r2:.4f}")
    print(f"CV Mean RMSE:       {result.cv_mean_rmse:.4f} seconds")
    print(f"Test MAE:           {result.test_metrics.mae:.4f} seconds")
    print(f"Test RMSE:          {result.test_metrics.rmse:.4f} seconds")
    print(f"Test R² Score:      {result.test_metrics.r2:.4f}")
    print(f"Test Sample Count:  {result.test_metrics.count}")
    print("=" * 60)

    # Verify Definition of Done: R^2 > 0.85
    if result.test_metrics.r2 < 0.85:
        print(
            f"WARNING: Test R² ({result.test_metrics.r2:.4f}) is below target threshold of 0.85.",
            file=sys.stderr,
        )
    else:
        print(f"SUCCESS: Model achieved R² = {result.test_metrics.r2:.4f} > 0.85 DoD threshold.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
