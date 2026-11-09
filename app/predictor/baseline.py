"""Analytical and historical baseline performance predictors (TASK-4.3).

Implements deterministic baseline predictors conforming to the IPredictor contract:
  1. AnalyticalPredictor: size * 8 / throughput estimator with RTT and QKD buffer accounting.
  2. HistoricalAveragePredictor: moving-average estimator over observed completed transfers.
  3. MockPredictor: alias matching docs/ARCHITECTURE_CONTRACTS.md §4.4.
"""

from __future__ import annotations

import math
from collections import deque

from app.models.scheduling import PredictionFeatures, PredictionOutput
from app.scheduler.base import IPredictor


class AnalyticalPredictor(IPredictor):
    """Deterministic analytical estimator based on payload size and throughput.

    Formula:
        duration = (data_size_bytes * 8) / (throughput_mbps * 1e6) + (rtt_ms / 1000)
    Accounts for key replenishment wait if local key buffer is depleted.
    """

    def __init__(
        self,
        default_throughput_mbps: float = 100.0,
        default_rtt_ms: float = 10.0,
        model_version: str = "analytical-v1.0.0",
    ) -> None:
        self._default_throughput = default_throughput_mbps
        self._default_rtt_ms = default_rtt_ms
        self._model_version = model_version

    def get_model_version(self) -> str:
        return self._model_version

    def predict(self, features: PredictionFeatures) -> PredictionOutput:
        """Compute analytical duration estimate and deadline-miss probability."""
        tp = features.estimated_available_bandwidth_mbps
        if tp <= 0.0:
            tp = features.recent_throughput_mbps
        if tp <= 0.0:
            tp = self._default_throughput

        throughput_bps = max(1.0, tp) * 1_000_000.0
        data_bits = float(features.data_size_bytes) * 8.0
        network_duration = data_bits / throughput_bps

        # Latency / propagation overhead
        latency_s = (features.key_service_latency_ms or self._default_rtt_ms) / 1000.0

        # Key resource exhaustion delay (if QKD buffer is empty)
        key_wait_s = 0.0
        if (
            features.key_resource_level is not None
            and features.key_resource_level <= 0
            and features.key_replenishment_rate_bps is not None
            and features.key_replenishment_rate_bps > 0
        ):
            # 256-bit key needed
            key_wait_s = 256.0 / features.key_replenishment_rate_bps

        total_duration = max(0.001, network_duration + latency_s + key_wait_s)

        # Deadline miss risk
        miss_prob = self._estimate_miss_probability(total_duration, features.deadline_slack_seconds)

        # 95% Confidence interval (analytical margin based on throughput std)
        ci_spread = total_duration * 0.10
        ci_lower = max(0.001, total_duration - ci_spread)
        ci_upper = total_duration + ci_spread

        return PredictionOutput(
            predicted_duration_seconds=round(total_duration, 4),
            deadline_miss_probability=round(miss_prob, 4),
            confidence_interval_95=(round(ci_lower, 4), round(ci_upper, 4)),
            model_version=self._model_version,
            is_fallback_analytical=True,
        )

    @staticmethod
    def _estimate_miss_probability(duration: float, slack: float) -> float:
        """Derive deadline-miss probability from predicted duration and slack."""
        if slack <= 0.0:
            return 1.0
        if duration >= slack:
            return 1.0
        # Smooth logistic-like curve approaching 1 as duration approaches slack
        margin_ratio = duration / slack
        if margin_ratio < 0.5:
            return max(0.0, margin_ratio * 0.2)
        return min(0.99, (margin_ratio ** 3) * 0.95)


class HistoricalAveragePredictor(IPredictor):
    """Predictor using a sliding window moving average of observed transfer rates.

    Falls back to AnalyticalPredictor when insufficient history exists (< 3 observations).
    """

    def __init__(
        self,
        window_size: int = 100,
        model_version: str = "historical-moving-avg-v1.0.0",
        fallback: IPredictor | None = None,
    ) -> None:
        self._window_size = window_size
        self._model_version = model_version
        self._fallback = fallback or AnalyticalPredictor()
        self._history: deque[tuple[int, float]] = deque(maxlen=window_size)  # (size_bytes, duration_s)

    def get_model_version(self) -> str:
        return self._model_version

    def record_observation(self, data_size_bytes: int, actual_duration_seconds: float) -> None:
        """Record an observed completed transfer duration."""
        if actual_duration_seconds > 0.0 and data_size_bytes > 0:
            self._history.append((data_size_bytes, actual_duration_seconds))

    def predict(self, features: PredictionFeatures) -> PredictionOutput:
        """Predict duration using historical average effective throughput."""
        if len(self._history) < 3:
            # Insufficient history, delegate to fallback
            output = self._fallback.predict(features)
            return PredictionOutput(
                predicted_duration_seconds=output.predicted_duration_seconds,
                deadline_miss_probability=output.deadline_miss_probability,
                confidence_interval_95=output.confidence_interval_95,
                model_version=self._model_version,
                is_fallback_analytical=True,
            )

        # Compute average throughput across historical observations
        throughputs_bps = [(size * 8.0) / dur for size, dur in self._history]
        mean_tp_bps = sum(throughputs_bps) / len(throughputs_bps)

        if mean_tp_bps <= 0.0:
            return self._fallback.predict(features)

        data_bits = float(features.data_size_bytes) * 8.0
        pred_duration = max(0.001, data_bits / mean_tp_bps)

        # Standard deviation for confidence bounds
        variance = sum((tp - mean_tp_bps) ** 2 for tp in throughputs_bps) / len(throughputs_bps)
        std_tp_bps = math.sqrt(variance)

        if mean_tp_bps > std_tp_bps:
            fast_dur = max(0.001, data_bits / (mean_tp_bps + std_tp_bps))
            slow_dur = max(0.001, data_bits / max(1.0, mean_tp_bps - std_tp_bps))
            ci_lower = min(fast_dur, pred_duration)
            ci_upper = max(slow_dur, pred_duration)
        else:
            ci_lower = max(0.001, pred_duration * 0.8)
            ci_upper = pred_duration * 1.5

        # Miss probability based on slack
        slack = features.deadline_slack_seconds
        if slack <= 0.0 or pred_duration >= slack:
            miss_prob = 1.0
        else:
            miss_prob = min(0.99, max(0.0, (pred_duration / slack) ** 2 * 0.5))

        return PredictionOutput(
            predicted_duration_seconds=round(pred_duration, 4),
            deadline_miss_probability=round(miss_prob, 4),
            confidence_interval_95=(round(ci_lower, 4), round(ci_upper, 4)),
            model_version=self._model_version,
            is_fallback_analytical=False,
        )


# Explicit MockPredictor contract matching ARCHITECTURE_CONTRACTS.md §4.4
MockPredictor = AnalyticalPredictor

__all__ = [
    "AnalyticalPredictor",
    "HistoricalAveragePredictor",
    "MockPredictor",
]
