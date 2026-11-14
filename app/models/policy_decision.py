"""Policy decision and audit event models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from app.core.enums import AuditEventType, SecurityProfile


def utc_now() -> datetime:
    return datetime.now(UTC)


class PolicyDecision(BaseModel):
    """Deterministic security gate evaluation result."""

    decision_id: UUID = Field(default_factory=uuid4)
    request_id: UUID | None = None
    allowed: bool
    reason_code: str = Field(..., description="Machine-readable code (e.g., AUTH_SUCCESS, DEST_UNAUTHORIZED)")
    explanation: str = Field(..., description="Human-readable decision rationale")
    required_security_profile: SecurityProfile
    evaluated_at: datetime = Field(default_factory=utc_now)
    policy_version: str = Field(default="1.0.0")


class AuditEvent(BaseModel):
    """Append-only audit record. Guarantees zero sensitive data logging."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: AuditEventType
    request_id: UUID | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    component: str = Field(..., description="Originating service: API, Policy, Scheduler, QKD, Executor")
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def sanitize_audit_details(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Strict guard against accidental cryptographic key or PHI leakage."""
        sanitized: dict[str, Any] = {}
        allowed_metadata = {"key_id", "key_length_bits", "key_length", "key_generation_rate_bps", "key_service_latency_ms", "keys_consumed"}
        disallowed = {"key_bytes", "secret", "token", "password", "payload", "plaintext", "raw_key", "private_key"}
        for key, val in v.items():
            k_lower = key.lower()
            if k_lower in allowed_metadata:
                sanitized[key] = val
            elif k_lower == "key" or any(term in k_lower for term in disallowed):
                sanitized[key] = "[REDACTED_BY_AUDIT_POLICY]"
            else:
                sanitized[key] = val
        return sanitized


__all__ = ["AuditEvent", "PolicyDecision", "utc_now"]
