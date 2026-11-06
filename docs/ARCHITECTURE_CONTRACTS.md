# Q-SHIELD Architectural Contracts & Interface Specifications

This document defines the core data schemas, interface protocols, API contracts, and isolation mock boundaries for the **Q-SHIELD** (Quantum-Secured Medical Data Exchange Orchestrator) platform as specified in [spec.md](file:///home/stade/repos/qshield/docs/spec.md).

All agents and implementation modules MUST adhere strictly to the contracts defined herein.

---

## 1. Core Domain Entities & Schemas

All schemas are defined using **Pydantic v2** (`pydantic.BaseModel`). Immutability and strict field validation are enforced across operational and state boundaries.

### 1.1 Enums & Primitive Value Objects

```python
from enum import Enum

class PriorityLevel(int, Enum):
    """Operational priority for transfer scheduling.
    
    Higher integer value corresponds to higher scheduling precedence.
    Priority is an operational scheduling hint, NOT an authorization permission.
    """
    LOW = 10
    NORMAL = 20
    HIGH = 30
    CRITICAL = 40

class TransferStatus(str, Enum):
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

class DataCategory(str, Enum):
    """Classification of medical/research data payload."""
    DIAGNOSTIC_REPORT = "DIAGNOSTIC_REPORT"      # Small, tight deadline (e.g., 5 MB, 30s)
    MEDICAL_IMAGE = "MEDICAL_IMAGE"              # Moderate to large, medium deadline (e.g., 2 GB, 10m)
    RESEARCH_DATASET = "RESEARCH_DATASET"        # Bulk, relaxed deadline (e.g., 500 MB - 100 GB, hours)
    TELEMETRY_LOG = "TELEMETRY_LOG"              # System and monitoring records

class SecurityProfile(str, Enum):
    """Cryptographic requirement profiles enforced by policy engine."""
    QKD_AES_256_GCM = "QKD_AES_256_GCM"          # AES-256-GCM session key seeded via QKD
    QKD_OTP_SIM = "QKD_OTP_SIM"                  # Information-theoretic OTP simulation
    POST_QUANTUM_HYBRID = "POST_QUANTUM_HYBRID"  # QKD key mixed with Classical/PQC KEM
    STANDARD_TLS_FALLBACK = "STANDARD_TLS"        # Fallback profile (if policy permits non-QKD)

class SchedulingPolicyName(str, Enum):
    """Identifier for pluggable scheduling policies."""
    FIFO = "FIFO"
    PRIORITY = "PRIORITY"
    RESOURCE_AWARE_HEURISTIC = "RESOURCE_AWARE_HEURISTIC"
    AI_ASSISTED = "AI_ASSISTED"

class AuditEventType(str, Enum):
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
```

---

### 1.2 Transfer Request Models

```python
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, field_validator

class TransferRequestCreate(BaseModel):
    """Client API payload for initiating a new transfer."""
    source_id: str = Field(..., min_length=3, max_length=64, description="Logical source node identifier")
    destination_id: str = Field(..., min_length=3, max_length=64, description="Authorized destination identifier")
    data_size_bytes: int = Field(..., gt=0, le=500_000_000_000, description="Payload size in bytes (max 500 GB)")
    priority: PriorityLevel = Field(default=PriorityLevel.NORMAL, description="Operational priority")
    deadline: Optional[datetime] = Field(default=None, description="UTC completion deadline; None indicates best effort")
    security_profile: SecurityProfile = Field(default=SecurityProfile.QKD_AES_256_GCM)
    data_category: DataCategory = Field(default=DataCategory.DIAGNOSTIC_REPORT)
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary non-sensitive metadata (no PHI)")

    @field_validator("metadata")
    @classmethod
    def validate_metadata_no_phi(cls, v: Dict[str, Any]) -> Dict[str, Any]:
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
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    deadline: Optional[datetime] = None
    security_profile: SecurityProfile
    data_category: DataCategory
    status: TransferStatus = Field(default=TransferStatus.SUBMITTED)
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # Internal operational checkpoints
    scheduled_at: Optional[datetime] = None
    transfer_started_at: Optional[datetime] = None
    transfer_completed_at: Optional[datetime] = None
    verification_completed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    payload_checksum_sha256: Optional[str] = None

class StateTransitionRecord(BaseModel):
    """Immutable log of a lifecycle state change."""
    transition_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    from_status: TransferStatus
    to_status: TransferStatus
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    actor: str = Field(..., description="Subsystem or agent triggering transition")
    reason: Optional[str] = None
```

---

### 1.3 Security & Policy Decision Models

```python
class PolicyDecision(BaseModel):
    """Deterministic security gate evaluation result."""
    allowed: bool
    reason_code: str = Field(..., description="Machine-readable code (e.g., AUTH_SUCCESS, DEST_UNAUTHORIZED)")
    explanation: str = Field(..., description="Human-readable decision rationale")
    required_security_profile: SecurityProfile
    evaluated_at: datetime = Field(default_factory=datetime.utcnow)
    policy_version: str = Field(default="1.0.0")

class AuditEvent(BaseModel):
    """Append-only audit record. Guarantees zero sensitive data logging."""
    event_id: UUID = Field(default_factory=uuid4)
    event_type: AuditEventType
    request_id: Optional[UUID] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    component: str = Field(..., description="Originating service: API, Policy, Scheduler, QKD, Executor")
    details: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def sanitize_audit_details(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Strict guard against accidental cryptographic key or PHI leakage."""
        sanitized = {}
        disallowed = {"key", "key_bytes", "secret", "token", "password", "payload", "plaintext"}
        for key, val in v.items():
            if any(term in key.lower() for term in disallowed):
                sanitized[key] = "[REDACTED_BY_AUDIT_POLICY]"
            else:
                sanitized[key] = val
        return sanitized
```

---

### 1.4 Telemetry & QKD Metric Models

```python
class QKDLinkStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    DISCONNECTED = "DISCONNECTED"
    UNAVAILABLE = "UNAVAILABLE"

class QKDResourceMetrics(BaseModel):
    """Telemetry describing quantum key service status."""
    source_id: str
    destination_id: str
    link_status: QKDLinkStatus
    key_buffer_available_keys: int = Field(..., ge=0, description="Available 256-bit key count in local buffer")
    key_generation_rate_bps: float = Field(..., ge=0.0, description="Sifted/secret key rate in bits per second")
    key_service_latency_ms: float = Field(..., ge=0.0, description="Roundtrip query latency to key management service")
    is_simulated: bool = Field(default=True, description="Strict provenance tag distinguishing real vs synthetic")
    measured_at: datetime = Field(default_factory=datetime.utcnow)

class NetworkTelemetry(BaseModel):
    """Network transport metrics between nodes."""
    source_id: str
    destination_id: str
    observed_throughput_mbps: float = Field(..., ge=0.0)
    packet_loss_rate: float = Field(..., ge=0.0, le=1.0)
    round_trip_time_ms: float = Field(..., ge=0.0)
    is_simulated: bool = Field(default=True)
    measured_at: datetime = Field(default_factory=datetime.utcnow)

class TelemetrySnapshot(BaseModel):
    """Aggregated real-time system state consumed by schedulers and predictors."""
    snapshot_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    active_transfers_count: int = Field(default=0, ge=0)
    queue_length: int = Field(default=0, ge=0)
    network_metrics: Dict[str, NetworkTelemetry] = Field(default_factory=dict) # key: f"{src}->{dst}"
    qkd_metrics: Dict[str, QKDResourceMetrics] = Field(default_factory=dict)     # key: f"{src}->{dst}"
    system_load_factor: float = Field(default=0.0, ge=0.0, le=1.0)
```

---

### 1.5 Prediction & AI Schemas

```python
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
    key_service_latency_ms: Optional[float] = None
    key_resource_level: Optional[int] = None
    key_replenishment_rate_bps: Optional[float] = None

class PredictionOutput(BaseModel):
    """Estimated transfer performance output."""
    predicted_duration_seconds: float = Field(..., ge=0.0)
    deadline_miss_probability: float = Field(..., ge=0.0, le=1.0)
    confidence_interval_95: tuple[float, float] = Field(default=(0.0, 0.0))
    model_version: str
    is_fallback_analytical: bool = Field(default=False)
```

---

### 1.6 Scheduling Decision & Transfer Execution Models

```python
class SchedulingDecision(BaseModel):
    """Result of a scheduling evaluation cycle."""
    decision_id: UUID = Field(default_factory=uuid4)
    selected_request_id: UUID
    policy_name: SchedulingPolicyName
    decision_reason: str
    estimated_start_time: datetime
    predicted_completion_time: datetime
    predicted_deadline_miss_probability: float
    evaluated_at: datetime = Field(default_factory=datetime.utcnow)
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
    acquired_at: datetime = Field(default_factory=datetime.utcnow)

class TransferExecutionPlan(BaseModel):
    """Execution directives given to TransferExecutor."""
    request_id: UUID
    source_id: str
    destination_id: str
    data_size_bytes: int
    security_profile: SecurityProfile
    key_handle: Optional[KeyHandle] = None
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
    verification_success: bool
    error_message: Optional[str] = None
    is_retryable: bool = False
```

---

## 2. Component Interface Contracts (Abstract Base Classes)

To guarantee modularity, pluggability, and independent agent execution, core components interact through abstract interface boundaries.

```python
from abc import ABC, abstractmethod
from typing import List, Optional

class IPolicyEngine(ABC):
    """Deterministic security and eligibility evaluation."""
    
    @abstractmethod
    def evaluate_request(
        self, request: TransferRequest, telemetry: TelemetrySnapshot
    ) -> PolicyDecision:
        """Determines if a request is authorized to be queued and executed."""
        pass

class IScheduler(ABC):
    """Abstract interface for all scheduling policies (FIFO, Priority, Heuristic, AI)."""
    
    @property
    @abstractmethod
    def policy_name(self) -> SchedulingPolicyName:
        pass

    @abstractmethod
    def select_next(
        self,
        candidate_requests: List[TransferRequest],
        telemetry: TelemetrySnapshot,
        predictor: Optional["IPredictor"] = None,
    ) -> Optional[SchedulingDecision]:
        """Selects the next eligible request to execute according to policy rules."""
        pass

class IPredictor(ABC):
    """Model abstraction for transfer duration and deadline risk estimation."""
    
    @abstractmethod
    def predict(self, features: PredictionFeatures) -> PredictionOutput:
        """Computes predicted transfer duration and deadline-miss risk."""
        pass

    @abstractmethod
    def get_model_version(self) -> str:
        pass

class IQKDAdapter(ABC):
    """Vendor-neutral abstraction over QKD key management infrastructure."""
    
    @abstractmethod
    def get_status(self, source_id: str, destination_id: str) -> QKDLinkStatus:
        """Queries physical/simulated link connectivity status."""
        pass

    @abstractmethod
    def get_resource_metrics(self, source_id: str, destination_id: str) -> QKDResourceMetrics:
        """Retrieves key buffer levels and generation metrics."""
        pass

    @abstractmethod
    def acquire_key_handle(
        self, source_id: str, destination_id: str, key_size_bits: int = 256
    ) -> KeyHandle:
        """Consumes/allocates a key and returns an opaque handle.
        
        CRITICAL: Never exposes secret key bytes to callers outside crypto adapters.
        """
        pass

class ITransferExecutor(ABC):
    """Handles data transmission, cryptographic encapsulation, and integrity checking."""
    
    @abstractmethod
    def execute(self, plan: TransferExecutionPlan) -> TransferResult:
        """Performs data transfer and receiver-side integrity verification."""
        pass

class ITelemetryCollector(ABC):
    """Collects network, queue, and QKD metrics."""
    
    @abstractmethod
    def get_snapshot(self) -> TelemetrySnapshot:
        """Returns the current consolidated system snapshot."""
        pass

    @abstractmethod
    def record_transfer_completed(self, result: TransferResult) -> None:
        """Ingests completed transfer observations for telemetry and model retraining."""
        pass

class ITransferRepository(ABC):
    """Persistence repository contract for requests, decisions, and audit events."""
    
    @abstractmethod
    def create_request(self, request: TransferRequest) -> TransferRequest:
        pass

    @abstractmethod
    def get_request(self, request_id: UUID) -> Optional[TransferRequest]:
        pass

    @abstractmethod
    def update_status(
        self, request_id: UUID, new_status: TransferStatus, actor: str, reason: Optional[str] = None
    ) -> TransferRequest:
        pass

    @abstractmethod
    def list_requests(
        self,
        status: Optional[TransferStatus] = None,
        priority: Optional[PriorityLevel] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[TransferRequest]:
        pass

    @abstractmethod
    def record_scheduling_decision(self, decision: SchedulingDecision) -> None:
        pass

    @abstractmethod
    def record_audit_event(self, event: AuditEvent) -> None:
        pass
```

---

## 3. Standard API Envelopes & REST Endpoints

All FastAPI routes produce uniform response and error envelopes.

### 3.1 Response Envelopes

```python
from typing import Generic, TypeVar, List

T = TypeVar("T")

class ApiResponse(BaseModel, Generic[T]):
    """Standard success wrapper."""
    success: bool = True
    data: T
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ApiErrorDetail(BaseModel):
    code: str
    message: str
    field: Optional[str] = None

class ApiErrorEnvelope(BaseModel):
    """Standard failure wrapper."""
    success: bool = False
    errors: List[ApiErrorDetail]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
```

### 3.2 Endpoint Specifications

| Method | Path | Request Body | Response Body | Status Code | Description |
|---|---|---|---|---|---|
| `POST` | `/api/v1/transfers` | `TransferRequestCreate` | `ApiResponse[TransferRequest]` | `201 Created` | Submits transfer request, validates, assigns UUID, records state `VALIDATING` -> `QUEUED` or `REJECTED`. |
| `GET` | `/api/v1/transfers/{request_id}` | None | `ApiResponse[TransferRequest]` | `200 OK` | Retrieves transfer request metadata, lifecycle state, timestamps, and checksum. |
| `GET` | `/api/v1/transfers` | Query params (`status`, `priority`, `limit`, `offset`) | `ApiResponse[List[TransferRequest]]` | `200 OK` | Lists transfer requests with optional filters. |
| `POST` | `/api/v1/transfers/{request_id}/cancel` | None | `ApiResponse[TransferRequest]` | `200 OK` | Cancels queued or pending request if not completed. |
| `GET` | `/api/v1/metrics/system` | None | `ApiResponse[TelemetrySnapshot]` | `200 OK` | Fetches active throughput, queue depth, and link metrics. |
| `GET` | `/api/v1/metrics/qkd` | Query: `source_id`, `destination_id` | `ApiResponse[QKDResourceMetrics]` | `200 OK` | Retrieves key generation rate, buffer depth, and link health. |
| `GET` | `/api/v1/health` | None | `ApiResponse[Dict[str, str]]` | `200 OK` | Health probe (database connectivity, executor status). |
| `POST` | `/api/v1/simulation/scenarios/run` | `ScenarioConfig` | `ApiResponse[Dict[str, Any]]` | `202 Accepted` | Triggers background discrete-event simulation run. |
| `GET` | `/api/v1/simulation/scenarios/{run_id}` | None | `ApiResponse[ExperimentRunResult]` | `200 OK` | Inspects status and aggregated metrics of experiment run. |

---

## 4. Isolation & Mock Boundaries

To enable parallel agent development and robust hermetic testing without hardware dependencies, five explicit mock boundaries are established:

### 4.1 `MockQKDAdapter` (`app/qkd/simulator.py`)
- Emulates key buffer dynamics with leaky-bucket replenishment:
  $$\text{buffer}(t + \Delta t) = \min(\text{max\_buffer}, \text{buffer}(t) + R_{\text{gen}} \cdot \Delta t) - \text{consumed}$$
- Fully deterministic with seed configuration:
  `MockQKDAdapter(initial_keys=1000, key_gen_rate_bps=50000, seed=42)`.
- Replaces hardware API calls when operating in simulation mode.

### 4.2 `MockNetworkTransport` (`simulation/network_model.py`)
- Simulates bandwidth sharing, propagation delay, and jitter:
  $$\text{duration} = \frac{\text{size\_bytes} \times 8}{\text{throughput\_mbps} \times 10^6} + \text{rtt\_ms} / 1000$$
- Injects stochastic packet loss and dropouts according to YAML scenario configuration.

### 4.3 `MockCryptoAdapter` (`app/transfer/crypto_adapter.py`)
- In simulation mode, computes real SHA-256 integrity checksums over in-memory synthetic buffers without performing costly full-file OTP hardware XOR, guaranteeing 100% CPU efficiency in benchmark sweeps while verifying checksum contract behavior.

### 4.4 `MockPredictor` (`app/predictor/baseline.py`)
- Provides deterministic analytical estimates:
  $$\text{duration}_{\text{est}} = \frac{\text{data\_size\_bytes} \times 8}{\max(1.0, \text{observed\_throughput\_mbps}) \times 10^6}$$
- Used during Stages 1-3 before ML training in Stage 4.

### 4.5 `InMemoryTransferRepository` (`tests/fixtures/mock_repository.py`)
- Thread-safe dictionary-backed repository conforming to `ITransferRepository`.
- Guarantees zero SQLite lock contention in high-throughput unit tests and parallel mock runs.

---

## 5. Architectural Invariant Rules

1. **Security Policy Precedence**: The scheduling engine MUST ONLY receive requests whose `PolicyDecision.allowed == True`. The AI Predictor NEVER touches policy logic.
2. **Confidentiality / Zero Key Logging**: Secret key bytes MUST NOT appear in API logs, SQLite tables, telemetry streams, or audit records. Only `KeyHandle.key_id` references are permitted.
3. **Deterministic Lifecycle FSM**: State transitions MUST follow the validated lifecycle:
   $$\text{SUBMITTED} \to \text{VALIDATING} \to \{\text{QUEUED}, \text{REJECTED}\} \to \text{SCHEDULED} \to \text{TRANSFERRING} \to \text{VERIFYING} \to \{\text{COMPLETED}, \text{FAILED}\}$$
   No request can transition to `COMPLETED` without verified cryptographic SHA-256 match.
4. **Data Privacy**: No medical imaging or patient data (PHI) shall be processed; all payloads are locally generated synthetic byte streams with deterministic seed tracking.
