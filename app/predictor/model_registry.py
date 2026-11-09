"""Model Registry and artifact serialization for Q-SHIELD predictors (TASK-4.5).

Provides serialization and deserialization via joblib alongside metadata sidecar
JSON documents containing training metrics, hyperparameter configurations, and
feature schemas. Exposes MLPredictor conforming to the IPredictor interface.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib

from app.models.scheduling import PredictionFeatures, PredictionOutput
from app.predictor.baseline import AnalyticalPredictor
from app.predictor.features import FeatureExtractor
from app.predictor.train import TrainingResult
from app.scheduler.base import IPredictor

logger = logging.getLogger(__name__)


class MLPredictor(IPredictor):
    """Predictor wrapping a trained scikit-learn regressor model."""

    def __init__(
        self,
        model: Any,
        model_version: str,
        feature_names: list[str],
        metadata: dict[str, Any] | None = None,
        fallback: IPredictor | None = None,
    ) -> None:
        self._model = model
        self._model_version = model_version
        self._feature_names = feature_names
        self._metadata = metadata or {}
        self._fallback = fallback or AnalyticalPredictor()

        # Extract RMSE from test metrics for 95% confidence intervals
        test_m = self._metadata.get("test_metrics", {})
        self._rmse = float(test_m.get("rmse", 1.0))

    def get_model_version(self) -> str:
        return self._model_version

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    def predict(self, features: PredictionFeatures) -> PredictionOutput:
        """Estimate transfer duration and deadline risk using the ML regressor."""
        try:
            vector = FeatureExtractor.to_vector(features).reshape(1, -1)
            raw_pred = float(self._model.predict(vector)[0])
            pred_duration = max(0.001, raw_pred)

            # Deadline-miss risk computation
            slack = features.deadline_slack_seconds
            if slack <= 0.0 or pred_duration >= slack:
                miss_prob = 1.0
            else:
                ratio = pred_duration / slack
                miss_prob = min(0.99, max(0.0, (ratio ** 2) * 0.5))

            # 95% Confidence interval
            ci_spread = 1.96 * max(0.05, self._rmse)
            ci_lower = max(0.001, pred_duration - ci_spread)
            ci_upper = pred_duration + ci_spread

            return PredictionOutput(
                predicted_duration_seconds=round(pred_duration, 4),
                deadline_miss_probability=round(miss_prob, 4),
                confidence_interval_95=(round(ci_lower, 4), round(ci_upper, 4)),
                model_version=self._model_version,
                is_fallback_analytical=False,
            )
        except Exception:
            logger.warning(
                "ML prediction failed; falling back to analytical predictor.",
                exc_info=True,
            )
            out = self._fallback.predict(features)
            return PredictionOutput(
                predicted_duration_seconds=out.predicted_duration_seconds,
                deadline_miss_probability=out.deadline_miss_probability,
                confidence_interval_95=out.confidence_interval_95,
                model_version=f"{self._model_version}-fallback",
                is_fallback_analytical=True,
            )


class ModelRegistry:
    """Manages serialization, loading, and metadata tracking for models."""

    @classmethod
    def save(
        cls,
        training_result: TrainingResult,
        output_path: str | Path,
    ) -> Path:
        """Persist model bundle and JSON metadata sidecar to disk.

        Parameters
        ----------
        training_result:
            Result object from train_duration_model.
        output_path:
            Target path (e.g., 'models/duration_predictor_v1.joblib').
        """
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        meta = training_result.to_metadata()
        meta["saved_at"] = datetime.now(UTC).isoformat()

        bundle = {
            "model": training_result.model,
            "model_version": training_result.model_version,
            "feature_names": training_result.feature_names,
            "metadata": meta,
        }

        # 1. Save binary joblib bundle
        joblib.dump(bundle, out_path)

        # 2. Save metadata sidecar JSON
        sidecar_path = out_path.with_suffix(".json")
        with sidecar_path.open("w") as fh:
            json.dump(meta, fh, indent=2, default=str)

        logger.info("Model saved: %s (sidecar: %s)", out_path, sidecar_path)
        return out_path

    @classmethod
    def load(cls, model_path: str | Path) -> MLPredictor:
        """Load a serialized model and return an MLPredictor instance.

        Parameters
        ----------
        model_path:
            Path to joblib file.
        """
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"Model file not found: {path}")

        loaded = joblib.load(path)

        if isinstance(loaded, dict) and "model" in loaded:
            model = loaded["model"]
            version = loaded.get("model_version", path.stem)
            feat_names = loaded.get("feature_names", FeatureExtractor.get_feature_names())
            metadata = loaded.get("metadata", {})
        else:
            # Plain estimator loaded
            model = loaded
            version = path.stem
            feat_names = FeatureExtractor.get_feature_names()
            metadata = {}
            # Attempt sidecar read
            sidecar = path.with_suffix(".json")
            if sidecar.exists():
                try:
                    with sidecar.open() as fh:
                        metadata = json.load(fh)
                except Exception:
                    pass

        return MLPredictor(
            model=model,
            model_version=version,
            feature_names=feat_names,
            metadata=metadata,
        )


__all__ = [
    "MLPredictor",
    "ModelRegistry",
]
