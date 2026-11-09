"""Feature extraction and normalization pipeline for AI prediction (TASK-4.2).

Transforms domain TransferRequest and TelemetrySnapshot models into normalized
PredictionFeatures. Handles missing optional QKD metrics explicitly with sentinel
values and imputation flags to guarantee deterministic numeric vector representations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import numpy as np

from app.core.enums import QKDLinkStatus
from app.models.scheduling import PredictionFeatures
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest

DEFAULT_DEADLINE_SENTINEL_SECONDS: float = 86400.0
DEFAULT_THROUGHPUT_MBPS: float = 100.0
DEFAULT_THROUGHPUT_STD_MBPS: float = 15.0
DEFAULT_RTT_MS: float = 10.0
DEFAULT_FAILURE_RATE: float = 0.0

QKD_SENTINEL_VALUE: float = -1.0


class FeatureExtractor:
    """Extracts, normalizes, and serializes prediction feature vectors."""

    FEATURE_NAMES: ClassVar[list[str]] = [
        "data_size_bytes",
        "priority_level",
        "queue_length",
        "active_transfer_count",
        "recent_throughput_mbps",
        "throughput_mean",
        "throughput_std",
        "deadline_slack_seconds",
        "link_status_connected",
        "recent_failure_rate",
        "estimated_available_bandwidth_mbps",
        "key_service_latency_ms",
        "key_resource_level",
        "key_replenishment_rate_bps",
        "has_qkd_metrics",
    ]

    @classmethod
    def get_feature_names(cls) -> list[str]:
        """Return the immutable ordered list of feature column names."""
        return list(cls.FEATURE_NAMES)

    @classmethod
    def extract_features(
        cls,
        request: TransferRequest,
        telemetry: TelemetrySnapshot,
        now: datetime | None = None,
    ) -> PredictionFeatures:
        """Extract PredictionFeatures from a TransferRequest and TelemetrySnapshot.

        Missing or omitted QKD telemetry fields remain None on the schema, but are
        handled cleanly with explicit defaults and sentinel mappings during vectorization.

        Parameters
        ----------
        request:
            The transfer request under evaluation.
        telemetry:
            Real-time telemetry snapshot containing network and QKD observations.
        now:
            Current reference UTC datetime (defaults to datetime.now(UTC)).

        Returns
        -------
        PredictionFeatures
            Normalized schema holding raw and normalized prediction inputs.
        """
        ref_time = now or datetime.now(UTC)

        # 1. Operational priority as numeric integer
        priority_val = (
            request.priority.value
            if hasattr(request.priority, "value")
            else int(request.priority)
        )

        # 2. Queue & system load
        queue_len = telemetry.queue_length
        active_transfers = telemetry.active_transfers_count

        # 3. Network telemetry lookup for route
        link_key = f"{request.source_id}->{request.destination_id}"
        net_telemetry = telemetry.network_metrics.get(link_key)

        if net_telemetry is None and telemetry.network_metrics:
            # Fallback to any active network link telemetry if specific pair not found
            net_telemetry = next(iter(telemetry.network_metrics.values()))

        if net_telemetry is not None:
            recent_tp = float(net_telemetry.observed_throughput_mbps)
            tp_mean = recent_tp
            tp_std = DEFAULT_THROUGHPUT_STD_MBPS
            loss_rate = float(net_telemetry.packet_loss_rate)
        else:
            recent_tp = DEFAULT_THROUGHPUT_MBPS
            tp_mean = DEFAULT_THROUGHPUT_MBPS
            tp_std = DEFAULT_THROUGHPUT_STD_MBPS
            loss_rate = DEFAULT_FAILURE_RATE

        # 4. Deadline slack
        if request.deadline is not None:
            deadline_dt = request.deadline
            if deadline_dt.tzinfo is None:
                deadline_dt = deadline_dt.replace(tzinfo=UTC)
            slack = (deadline_dt - ref_time).total_seconds()
            slack = max(0.0, slack)
        else:
            slack = DEFAULT_DEADLINE_SENTINEL_SECONDS

        # 5. QKD telemetry lookup
        qkd_telemetry = telemetry.qkd_metrics.get(link_key)
        if qkd_telemetry is None and telemetry.qkd_metrics:
            qkd_telemetry = next(iter(telemetry.qkd_metrics.values()))

        link_connected = True
        key_latency: float | None = None
        key_level: int | None = None
        key_replenish: float | None = None

        if qkd_telemetry is not None:
            link_connected = qkd_telemetry.link_status == QKDLinkStatus.CONNECTED
            key_latency = float(qkd_telemetry.key_service_latency_ms)
            key_level = int(qkd_telemetry.key_buffer_available_keys)
            key_replenish = float(qkd_telemetry.key_generation_rate_bps)

        # 6. Estimated available bandwidth
        concurrency_factor = max(1, active_transfers + 1)
        est_bw = recent_tp / concurrency_factor

        return PredictionFeatures(
            data_size_bytes=int(request.data_size_bytes),
            priority_level=priority_val,
            queue_length=queue_len,
            active_transfer_count=active_transfers,
            recent_throughput_mbps=recent_tp,
            throughput_mean=tp_mean,
            throughput_std=tp_std,
            deadline_slack_seconds=slack,
            link_status_connected=link_connected,
            recent_failure_rate=loss_rate,
            estimated_available_bandwidth_mbps=est_bw,
            key_service_latency_ms=key_latency,
            key_resource_level=key_level,
            key_replenishment_rate_bps=key_replenish,
        )

    @classmethod
    def to_vector(cls, features: PredictionFeatures) -> np.ndarray:
        """Convert a PredictionFeatures object into a 1D float64 numpy array.

        Missing QKD metrics (None) are explicitly mapped to sentinel values (-1.0)
        and an imputation/indicator flag 'has_qkd_metrics' is appended.
        Guarantees consistent output shape (15,) and never raises exceptions on None.
        """
        has_qkd = 1.0 if (
            features.key_service_latency_ms is not None
            or features.key_resource_level is not None
            or features.key_replenishment_rate_bps is not None
        ) else 0.0

        key_latency = (
            float(features.key_service_latency_ms)
            if features.key_service_latency_ms is not None
            else QKD_SENTINEL_VALUE
        )
        key_level = (
            float(features.key_resource_level)
            if features.key_resource_level is not None
            else QKD_SENTINEL_VALUE
        )
        key_replenish = (
            float(features.key_replenishment_rate_bps)
            if features.key_replenishment_rate_bps is not None
            else QKD_SENTINEL_VALUE
        )

        vector = [
            float(features.data_size_bytes),
            float(features.priority_level),
            float(features.queue_length),
            float(features.active_transfer_count),
            float(features.recent_throughput_mbps),
            float(features.throughput_mean),
            float(features.throughput_std),
            float(features.deadline_slack_seconds),
            1.0 if features.link_status_connected else 0.0,
            float(features.recent_failure_rate),
            float(features.estimated_available_bandwidth_mbps),
            key_latency,
            key_level,
            key_replenish,
            has_qkd,
        ]
        return np.asarray(vector, dtype=np.float64)

    @classmethod
    def to_dict(cls, features: PredictionFeatures) -> dict[str, float]:
        """Convert a PredictionFeatures object to a dictionary of numeric features."""
        vector = cls.to_vector(features)
        return dict(zip(cls.FEATURE_NAMES, vector, strict=True))

    @classmethod
    def batch_to_vectors(cls, features_list: list[PredictionFeatures]) -> np.ndarray:
        """Convert a list of PredictionFeatures into a 2D numpy matrix of shape (M, N)."""
        if not features_list:
            return np.empty((0, len(cls.FEATURE_NAMES)), dtype=np.float64)
        return np.vstack([cls.to_vector(f) for f in features_list])


def extract_features(
    request: TransferRequest,
    telemetry: TelemetrySnapshot,
    now: datetime | None = None,
) -> PredictionFeatures:
    """Convenience functional wrapper around FeatureExtractor.extract_features."""
    return FeatureExtractor.extract_features(request, telemetry, now=now)


__all__ = [
    "DEFAULT_DEADLINE_SENTINEL_SECONDS",
    "DEFAULT_FAILURE_RATE",
    "DEFAULT_RTT_MS",
    "DEFAULT_THROUGHPUT_MBPS",
    "DEFAULT_THROUGHPUT_STD_MBPS",
    "FeatureExtractor",
    "QKD_SENTINEL_VALUE",
    "extract_features",
]
