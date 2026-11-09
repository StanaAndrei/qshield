"""Unit tests for baseline and machine learning performance predictors (TASK-4.3 / 4.4 / 4.5 / 4.7)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from app.models.scheduling import PredictionFeatures, PredictionOutput
from app.predictor.baseline import AnalyticalPredictor, HistoricalAveragePredictor, MockPredictor
from app.predictor.model_registry import MLPredictor, ModelRegistry
from app.predictor.train import train_duration_model


@pytest.fixture
def sample_features() -> PredictionFeatures:
    return PredictionFeatures(
        data_size_bytes=100_000_000,  # 100 MB = 800 Mbits
        priority_level=20,
        queue_length=3,
        active_transfer_count=0,
        recent_throughput_mbps=100.0,
        throughput_mean=100.0,
        throughput_std=10.0,
        deadline_slack_seconds=60.0,
        link_status_connected=True,
        recent_failure_rate=0.0,
        estimated_available_bandwidth_mbps=100.0,
        key_service_latency_ms=10.0,
        key_resource_level=1000,
        key_replenishment_rate_bps=2560.0,
    )


@pytest.fixture
def synthetic_simulation_json(tmp_path: Path) -> Path:
    """Generate a multi-seed simulation dataset for training pipeline verification."""
    runs = []
    # Generate 4 training seeds (0, 1, 2, 3) and 1 test seed (4)
    for seed in range(5):
        rng = np.random.RandomState(seed)
        records = []
        for i in range(40):
            # Sizes between 5 MB and 200 MB
            size_mb = float(rng.uniform(5.0, 200.0))
            size_bytes = int(size_mb * 1_000_000)
            # Simulated throughput with slight noise around 100 Mbps
            tp = float(rng.normal(100.0, 5.0))
            tp = max(20.0, tp)
            transfer_dur = (size_bytes * 8.0) / (tp * 1_000_000.0) + 0.01

            records.append({
                "request_id": str(uuid4()),
                "source_id": "univ-lab",
                "destination_id": "poly-clinic",
                "data_size_bytes": size_bytes,
                "priority": int(rng.choice([10, 20, 30, 40])),
                "data_category": rng.choice(["DIAGNOSTIC_REPORT", "MEDICAL_IMAGE", "RESEARCH_DATASET"]),
                "arrival_time": float(i * 2.0),
                "queue_time": float(rng.uniform(0.1, 2.0)),
                "start_time": float(i * 2.0 + 1.0),
                "completion_time": float(i * 2.0 + 1.0 + transfer_dur),
                "deadline": None,
                "deadline_met": True,
                "key_wait_time": 0.0,
                "policy_name": "FIFO",
                "actual_throughput_mbps": tp,
                "transfer_duration_seconds": transfer_dur,
                "total_duration_seconds": transfer_dur + 1.0,
                "success": True,
                "failure_reason": None,
            })

        runs.append({
            "scenario_name": "test_scenario",
            "policy_name": "FIFO",
            "seed": seed,
            "total_requests": len(records),
            "completed": len(records),
            "records": records,
        })

    json_path = tmp_path / "test_baseline_matrix.json"
    with json_path.open("w") as fh:
        json.dump({"runs": runs, "total_runs": len(runs)}, fh)

    return json_path


class TestBaselinePredictors:
    def test_analytical_predictor_calculation(self, sample_features: PredictionFeatures) -> None:
        """DoD: Analytical predictor computes correct transfer durations for known sizes and bandwidths."""
        predictor = AnalyticalPredictor(default_rtt_ms=10.0)
        output = predictor.predict(sample_features)

        assert isinstance(output, PredictionOutput)
        # 100 MB * 8 / (100 Mbps * 1e6) = 8.0 seconds + 0.01s latency = 8.01s
        assert abs(output.predicted_duration_seconds - 8.01) < 0.05
        assert output.is_fallback_analytical is True
        assert output.deadline_miss_probability < 0.20
        assert output.confidence_interval_95[0] < output.predicted_duration_seconds < output.confidence_interval_95[1]

    def test_analytical_predictor_tight_deadline(self, sample_features: PredictionFeatures) -> None:
        sample_features.deadline_slack_seconds = 5.0  # Required duration is ~8.01s
        predictor = AnalyticalPredictor()
        output = predictor.predict(sample_features)
        assert output.deadline_miss_probability == 1.0

    def test_mock_predictor_alias(self, sample_features: PredictionFeatures) -> None:
        """Verify MockPredictor conforms to ARCHITECTURE_CONTRACTS.md §4.4."""
        predictor = MockPredictor()
        out = predictor.predict(sample_features)
        assert out.predicted_duration_seconds > 0.0
        assert predictor.get_model_version().startswith("analytical")

    def test_historical_average_predictor(self, sample_features: PredictionFeatures) -> None:
        predictor = HistoricalAveragePredictor(window_size=10)

        # Before history, falls back to analytical
        out_init = predictor.predict(sample_features)
        assert out_init.is_fallback_analytical is True

        # Record observations: 100 MB in 8.0s (100 Mbps)
        for _ in range(5):
            predictor.record_observation(100_000_000, 8.0)

        out_hist = predictor.predict(sample_features)
        assert out_hist.is_fallback_analytical is False
        assert abs(out_hist.predicted_duration_seconds - 8.0) < 0.1


class TestMLTrainingAndRegistry:
    def test_train_gradient_boosting_model(self, synthetic_simulation_json: Path) -> None:
        """DoD: Pipeline trains on JSON simulation dataset and achieves R^2 > 0.85 on test set."""
        result = train_duration_model(
            data_path=synthetic_simulation_json,
            model_type="gradient_boosting",
            test_seed=4,
            cv_folds=3,
        )

        assert result.model is not None
        assert result.model_version.startswith("gradient_boosting-v")
        assert result.test_metrics.r2 > 0.85
        assert result.test_metrics.rmse < 1.0

    def test_model_registry_save_and_load(
        self,
        synthetic_simulation_json: Path,
        tmp_path: Path,
        sample_features: PredictionFeatures,
    ) -> None:
        """DoD: CLI script trains and serializes model; registry loads model and successfully predicts."""
        result = train_duration_model(
            data_path=synthetic_simulation_json,
            model_type="ridge",
            test_seed=4,
        )

        save_path = tmp_path / "models" / "duration_predictor.joblib"
        saved = ModelRegistry.save(result, save_path)
        assert saved.exists()
        assert save_path.with_suffix(".json").exists()

        # Load back
        predictor = ModelRegistry.load(save_path)
        assert isinstance(predictor, MLPredictor)
        assert predictor.get_model_version() == result.model_version

        # Predict
        out = predictor.predict(sample_features)
        assert isinstance(out, PredictionOutput)
        assert out.predicted_duration_seconds > 0.0
        assert out.is_fallback_analytical is False

    def test_inference_latency_under_10ms(
        self,
        synthetic_simulation_json: Path,
        tmp_path: Path,
        sample_features: PredictionFeatures,
    ) -> None:
        """Task-4.7 DoD: Model inference speed (<10 ms per decision)."""
        result = train_duration_model(
            data_path=synthetic_simulation_json,
            model_type="gradient_boosting",
            test_seed=4,
        )
        model_path = tmp_path / "gbr_test.joblib"
        ModelRegistry.save(result, model_path)
        predictor = ModelRegistry.load(model_path)

        # Warm-up
        predictor.predict(sample_features)

        # Measure 50 consecutive predictions
        latencies_ms = []
        for _ in range(50):
            t0 = time.perf_counter_ns()
            predictor.predict(sample_features)
            t1 = time.perf_counter_ns()
            latencies_ms.append((t1 - t0) / 1_000_000.0)

        mean_latency = float(np.mean(latencies_ms))
        p95_latency = float(np.percentile(latencies_ms, 95))

        assert mean_latency < 10.0
        assert p95_latency < 10.0
