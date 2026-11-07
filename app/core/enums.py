"""Core domain enums and value types for Q-SHIELD."""

from __future__ import annotations

from enum import Enum, StrEnum


class PriorityLevel(int, Enum):
    """Operational priority for transfer scheduling.

    Higher integer value corresponds to higher scheduling precedence.
    Priority is an operational scheduling hint, NOT an authorization permission.
    """

    LOW = 10
    NORMAL = 20
    HIGH = 30
    CRITICAL = 40


class TransferStatus(StrEnum):
    """Deterministic lifecycle states for a transfer request."""

    SUBMITTED = "SUBMITTED"
    VALIDATING = "VALIDATING"
    REJECTED = "REJECTED"
    QUEUED = "QUEUED"
    SCHEDULED = "SCHEDULED"
    TRANSFERRING = "TRANSFERRING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class DataCategory(StrEnum):
    """Classification of medical/research data payload."""

    DIAGNOSTIC_REPORT = "DIAGNOSTIC_REPORT"  # Small, tight deadline (e.g., 5 MB, 30s)
    MEDICAL_IMAGE = "MEDICAL_IMAGE"  # Moderate to large, medium deadline (e.g., 2 GB, 10m)
    RESEARCH_DATASET = "RESEARCH_DATASET"  # Bulk, relaxed deadline (e.g., 500 MB - 100 GB, hours)
    TELEMETRY_LOG = "TELEMETRY_LOG"  # System and monitoring records


class SecurityProfile(StrEnum):
    """Cryptographic requirement profiles enforced by policy engine."""

    QKD_AES_256_GCM = "QKD_AES_256_GCM"  # AES-256-GCM session key seeded via QKD
    QKD_OTP_SIM = "QKD_OTP_SIM"  # Information-theoretic OTP simulation
    POST_QUANTUM_HYBRID = "POST_QUANTUM_HYBRID"  # QKD key mixed with Classical/PQC KEM
    STANDARD_TLS_FALLBACK = "STANDARD_TLS"  # Fallback profile (if policy permits non-QKD)


class SchedulingPolicyName(StrEnum):
    """Identifier for pluggable scheduling policies."""

    FIFO = "FIFO"
    PRIORITY = "PRIORITY"
    RESOURCE_AWARE_HEURISTIC = "RESOURCE_AWARE_HEURISTIC"
    AI_ASSISTED = "AI_ASSISTED"


class AuditEventType(StrEnum):
    """Categorization for immutable security and system audit events."""

    REQUEST_SUBMITTED = "REQUEST_SUBMITTED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    STATUS_TRANSITION = "STATUS_TRANSITION"
    SCHEDULING_DECISION = "SCHEDULING_DECISION"
    KEY_REQUESTED = "KEY_REQUESTED"
    TRANSFER_ATTEMPT = "TRANSFER_ATTEMPT"
    INTEGRITY_VERIFIED = "INTEGRITY_VERIFIED"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"
    HARDWARE_FAULT = "HARDWARE_FAULT"


class QKDLinkStatus(StrEnum):
    """Link status states for QKD links."""

    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    UNAVAILABLE = "UNAVAILABLE"


__all__ = [
    "AuditEventType",
    "DataCategory",
    "PriorityLevel",
    "QKDLinkStatus",
    "SchedulingPolicyName",
    "SecurityProfile",
    "TransferStatus",
]
