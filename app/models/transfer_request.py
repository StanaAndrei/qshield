"""Transfer request domain entities and schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from app.core.enums import DataCategory, PriorityLevel, SecurityProfile, TransferStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


class TransferRequestCreate(BaseModel):
    """Client API payload for initiating a new transfer."""

    source_id: str = Field(..., min_length=3, max_length=64, description="Logical source node identifier")
    destination_id: str = Field(..., min_length=3, max_length=64, description="Authorized destination identifier")
    data_size_bytes: int = Field(..., gt=0, le=500_000_000_000, description="Payload size in bytes (max 500 GB)")
    priority: PriorityLevel = Field(default=PriorityLevel.NORMAL, description="Operational priority")
    deadline: datetime | None = Field(default=None, description="UTC completion deadline; None indicates best effort")
    security_profile: SecurityProfile = Field(default=SecurityProfile.QKD_AES_256_GCM)
    data_category: DataCategory = Field(default=DataCategory.DIAGNOSTIC_REPORT)
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary non-sensitive metadata (no PHI)")

    @field_validator("metadata")
    @classmethod
    def validate_metadata_no_phi(cls, v: dict[str, Any]) -> dict[str, Any]:
        forbidden_keys = {"patient_name", "ssn", "mrn", "raw_payload", "key_bytes", "secret"}
        for k in v.keys():
            if k.lower() in forbidden_keys:
                raise ValueError(f"Forbidden sensitive key detected in metadata: {k}")
        return v


class TransferRequest(BaseModel):
    """Internal canonical transfer request state object."""

    request_id: UUID = Field(default_factory=uuid4)
    source_id: str
    destination_id: str
    data_size_bytes: int
    priority: PriorityLevel
    submitted_at: datetime = Field(default_factory=utc_now)
    deadline: datetime | None = None
    security_profile: SecurityProfile
    data_category: DataCategory
    status: TransferStatus = Field(default=TransferStatus.SUBMITTED)
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Internal operational checkpoints
    scheduled_at: datetime | None = None
    transfer_started_at: datetime | None = None
    transfer_completed_at: datetime | None = None
    verification_completed_at: datetime | None = None
    failure_reason: str | None = None
    payload_checksum_sha256: str | None = None


class StateTransitionRecord(BaseModel):
    """Immutable log of a lifecycle state change."""

    transition_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    from_status: TransferStatus
    to_status: TransferStatus
    timestamp: datetime = Field(default_factory=utc_now)
    actor: str = Field(..., description="Subsystem or agent triggering transition")
    reason: str | None = None
