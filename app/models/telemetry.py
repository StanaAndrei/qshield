"""Telemetry models for network and QKD status."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.core.enums import QKDLinkStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


class QKDResourceMetrics(BaseModel):
    """Telemetry describing quantum key service status."""

    source_id: str
    destination_id: str
    link_status: QKDLinkStatus
    key_buffer_available_keys: int = Field(..., ge=0, description="Available 256-bit key count in local buffer")
    key_generation_rate_bps: float = Field(..., ge=0.0, description="Sifted/secret key rate in bits per second")
    key_service_latency_ms: float = Field(..., ge=0.0, description="Roundtrip query latency to key management service")
    is_simulated: bool = Field(default=True, description="Strict provenance tag distinguishing real vs synthetic")
    measured_at: datetime = Field(default_factory=utc_now)


class NetworkTelemetry(BaseModel):
    """Network transport metrics between nodes."""

    source_id: str
    destination_id: str
    observed_throughput_mbps: float = Field(..., ge=0.0)
    packet_loss_rate: float = Field(..., ge=0.0, le=1.0)
    round_trip_time_ms: float = Field(..., ge=0.0)
    is_simulated: bool = Field(default=True)
    measured_at: datetime = Field(default_factory=utc_now)


class TelemetrySnapshot(BaseModel):
    """Aggregated real-time system state consumed by schedulers and predictors."""

    snapshot_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=utc_now)
    active_transfers_count: int = Field(default=0, ge=0)
    queue_length: int = Field(default=0, ge=0)
    network_metrics: dict[str, NetworkTelemetry] = Field(default_factory=dict)  # key: f"{src}->{dst}"
    qkd_metrics: dict[str, QKDResourceMetrics] = Field(default_factory=dict)  # key: f"{src}->{dst}"
    system_load_factor: float = Field(default=0.0, ge=0.0, le=1.0)


class TransferObservation(BaseModel):
    """Historical observation of an executed transfer recorded for telemetry and model training."""

    observation_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    source_id: str
    destination_id: str
    data_size_bytes: int = Field(..., ge=0)
    priority_level: int = Field(default=20)
    data_category: str = Field(default="DIAGNOSTIC_REPORT")
    actual_duration_seconds: float = Field(..., ge=0.0)
    effective_throughput_mbps: float = Field(..., ge=0.0)
    success: bool = True
    verification_success: bool = True
    payload_checksum_sha256: str | None = None
    error_message: str | None = None
    queue_wait_seconds: float = Field(default=0.0, ge=0.0)
    observed_at: datetime = Field(default_factory=utc_now)
    features_snapshot: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "NetworkTelemetry",
    "QKDResourceMetrics",
    "TelemetrySnapshot",
    "TransferObservation",
    "utc_now",
]
