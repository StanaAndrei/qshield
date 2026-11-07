"""Scheduling, prediction, and execution models."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.core.enums import SchedulingPolicyName, SecurityProfile


def utc_now() -> datetime:
    return datetime.now(UTC)


class PredictionFeatures(BaseModel):
    """Normalized feature vector passed to ML and baseline predictors."""

    data_size_bytes: int
    priority_level: int
    queue_length: int
    active_transfer_count: int
    recent_throughput_mbps: float
    throughput_mean: float
    throughput_std: float
    deadline_slack_seconds: float = Field(..., description="Seconds until deadline (or large sentinel if no deadline)")
    link_status_connected: bool
    recent_failure_rate: float
    estimated_available_bandwidth_mbps: float
    key_service_latency_ms: float | None = None
    key_resource_level: int | None = None
    key_replenishment_rate_bps: float | None = None


class PredictionOutput(BaseModel):
    """Estimated transfer performance output."""

    predicted_duration_seconds: float = Field(..., ge=0.0)
    deadline_miss_probability: float = Field(..., ge=0.0, le=1.0)
    confidence_interval_95: tuple[float, float] = Field(default=(0.0, 0.0))
    model_version: str
    is_fallback_analytical: bool = Field(default=False)


class SchedulingDecision(BaseModel):
    """Result of a scheduling evaluation cycle."""

    decision_id: UUID = Field(default_factory=uuid4)
    selected_request_id: UUID
    policy_name: SchedulingPolicyName
    decision_reason: str
    estimated_start_time: datetime
    predicted_completion_time: datetime
    predicted_deadline_miss_probability: float
    evaluated_at: datetime = Field(default_factory=utc_now)
    queue_length_at_decision: int
    starvation_age_seconds: float = 0.0


class KeyHandle(BaseModel):
    """Opaque reference to QKD key material.

    Contains NO secret key bytes. Mediates cryptographic lookup inside TransferExecutor.
    """

    key_id: str
    key_length_bits: int
    source_id: str
    destination_id: str
    acquired_at: datetime = Field(default_factory=utc_now)


class TransferExecutionPlan(BaseModel):
    """Execution directives given to TransferExecutor."""

    request_id: UUID
    source_id: str
    destination_id: str
    data_size_bytes: int
    security_profile: SecurityProfile
    key_handle: KeyHandle | None = None
    allocated_bandwidth_mbps: float
    simulate_failures: bool = False


class TransferResult(BaseModel):
    """Final output from TransferExecutor."""

    request_id: UUID
    success: bool
    actual_duration_seconds: float
    bytes_transferred: int
    effective_throughput_mbps: float
    payload_checksum_sha256: str
    verification_success: bool = True
    error_message: str | None = None
    is_retryable: bool = False
