"""Unit tests for feature engineering and normalization pipeline (TASK-4.2 / TASK-4.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest

from app.core.enums import (
    DataCategory,
    PriorityLevel,
    QKDLinkStatus,
    SecurityProfile,
    TransferStatus,
)
from app.models.scheduling import PredictionFeatures
from app.models.telemetry import NetworkTelemetry, QKDResourceMetrics, TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.predictor.features import (
    DEFAULT_DEADLINE_SENTINEL_SECONDS,
    DEFAULT_THROUGHPUT_MBPS,
    QKD_SENTINEL_VALUE,
    FeatureExtractor,
    extract_features,
)


@pytest.fixture
def base_request() -> TransferRequest:
    return TransferRequest(
        request_id=uuid4(),
        source_id="univ-lab",
        destination_id="poly-clinic",
        data_size_bytes=10_000_000,  # 10 MB
        priority=PriorityLevel.HIGH,
        submitted_at=datetime.now(UTC),
        deadline=datetime.now(UTC) + timedelta(seconds=60),
        security_profile=SecurityProfile.QKD_AES_256_GCM,
        data_category=DataCategory.DIAGNOSTIC_REPORT,
        status=TransferStatus.QUEUED,
    )


@pytest.fixture
def full_telemetry() -> TelemetrySnapshot:
    link_key = "univ-lab->poly-clinic"
    return TelemetrySnapshot(
        active_transfers_count=2,
        queue_length=5,
        network_metrics={
            link_key: NetworkTelemetry(
                source_id="univ-lab",
                destination_id="poly-clinic",
                observed_throughput_mbps=85.0,
                packet_loss_rate=0.002,
                round_trip_time_ms=12.5,
            )
        },
        qkd_metrics={
            link_key: QKDResourceMetrics(
                source_id="univ-lab",
                destination_id="poly-clinic",
                link_status=QKDLinkStatus.CONNECTED,
                key_buffer_available_keys=1500,
                key_generation_rate_bps=45000.0,
                key_service_latency_ms=8.0,
            )
        },
        system_load_factor=0.3,
    )


class TestFeatureExtraction:
    def test_extract_full_features(
        self, base_request: TransferRequest, full_telemetry: TelemetrySnapshot
    ) -> None:
        features = extract_features(base_request, full_telemetry)

        assert isinstance(features, PredictionFeatures)
        assert features.data_size_bytes == 10_000_000
        assert features.priority_level == int(PriorityLevel.HIGH)
        assert features.queue_length == 5
        assert features.active_transfer_count == 2
        assert features.recent_throughput_mbps == 85.0
        assert features.link_status_connected is True
        assert features.recent_failure_rate == 0.002
        assert features.key_resource_level == 1500
        assert features.key_service_latency_ms == 8.0
        assert features.key_replenishment_rate_bps == 45000.0
        assert 0.0 < features.deadline_slack_seconds <= 60.0

    def test_extract_features_with_missing_qkd_metrics(
        self, base_request: TransferRequest
    ) -> None:
        """DoD: Handles None QKD fields without throwing exceptions."""
        empty_telemetry = TelemetrySnapshot(
            active_transfers_count=0,
            queue_length=1,
            network_metrics={},
            qkd_metrics={},
        )

        features = extract_features(base_request, empty_telemetry)

        assert features.key_service_latency_ms is None
        assert features.key_resource_level is None
        assert features.key_replenishment_rate_bps is None
        assert features.recent_throughput_mbps == DEFAULT_THROUGHPUT_MBPS

        # Vector conversion must not fail on None values
        vector = FeatureExtractor.to_vector(features)
        assert isinstance(vector, np.ndarray)
        assert len(vector) == len(FeatureExtractor.get_feature_names())

        # Check QKD sentinel values in vector
        feat_dict = FeatureExtractor.to_dict(features)
        assert feat_dict["key_service_latency_ms"] == QKD_SENTINEL_VALUE
        assert feat_dict["key_resource_level"] == QKD_SENTINEL_VALUE
        assert feat_dict["key_replenishment_rate_bps"] == QKD_SENTINEL_VALUE
        assert feat_dict["has_qkd_metrics"] == 0.0

    def test_consistent_vector_dimensions(
        self, base_request: TransferRequest, full_telemetry: TelemetrySnapshot
    ) -> None:
        """DoD: Unit test confirms consistent vector dimensions."""
        features = extract_features(base_request, full_telemetry)
        vector = FeatureExtractor.to_vector(features)

        expected_dim = len(FeatureExtractor.FEATURE_NAMES)
        assert vector.shape == (expected_dim,)
        assert vector.dtype == np.float64

        # Check has_qkd_metrics flag is 1.0 when QKD fields are present
        feat_dict = FeatureExtractor.to_dict(features)
        assert feat_dict["has_qkd_metrics"] == 1.0
        assert feat_dict["key_resource_level"] == 1500.0

    def test_batch_to_vectors(
        self, base_request: TransferRequest, full_telemetry: TelemetrySnapshot
    ) -> None:
        features1 = extract_features(base_request, full_telemetry)
        features2 = extract_features(base_request, TelemetrySnapshot())

        matrix = FeatureExtractor.batch_to_vectors([features1, features2])
        assert matrix.shape == (2, len(FeatureExtractor.FEATURE_NAMES))

        empty_matrix = FeatureExtractor.batch_to_vectors([])
        assert empty_matrix.shape == (0, len(FeatureExtractor.FEATURE_NAMES))

    def test_deadline_slack_sentinel_when_none(
        self, base_request: TransferRequest, full_telemetry: TelemetrySnapshot
    ) -> None:
        base_request.deadline = None
        features = extract_features(base_request, full_telemetry)
        assert features.deadline_slack_seconds == DEFAULT_DEADLINE_SENTINEL_SECONDS
