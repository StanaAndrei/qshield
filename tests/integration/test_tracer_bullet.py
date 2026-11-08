"""Integration tests for the Q-SHIELD Tracer Bullet end-to-end lifecycle.

Covers TASK-1.8 (Agent G):
  - Full FSM lifecycle: SUBMITTED -> VALIDATING -> QUEUED -> SCHEDULED ->
    TRANSFERRING -> VERIFYING -> COMPLETED
  - Policy gate: authorized vs. unauthorized submissions
  - FIFO scheduler: deterministic selection ordering across multiple requests
  - Synthetic transfer executor: integrity verification success and failure
  - API envelope contract: ApiResponse[TransferRequest] shape on POST /transfers
  - Cancellation pathway and idempotency guards

DoD: `pytest tests/integration/test_tracer_bullet.py` passes 100%.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.routes_transfers import get_repository
from app.core.enums import (
    DataCategory,
    PriorityLevel,
    SchedulingPolicyName,
    SecurityProfile,
    TransferStatus,
)
from app.core.exceptions import InvalidStateTransitionError
from app.core.state_machine import TransferStateMachine
from app.database.schema import Base
from app.main import app
from app.models.scheduling import TransferExecutionPlan, TransferResult
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.scheduler.fifo import FIFOScheduler
from app.security.policy_engine import SecurityPolicyEngine
from app.transfer.executor import SyntheticTransferExecutor
from app.transfer.integrity import IntegrityVerifier
from app.transfer.simulator import SyntheticTransferSimulator, generate_synthetic_buffer
from tests.fixtures.mock_repository import InMemoryTransferRepository

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo() -> InMemoryTransferRepository:
    """Clean in-memory repository for each test."""
    return InMemoryTransferRepository()


@pytest.fixture
def policy_engine() -> SecurityPolicyEngine:
    """Standard security policy engine loaded from configs/security.yaml."""
    return SecurityPolicyEngine()


@pytest.fixture
def fifo_scheduler() -> FIFOScheduler:
    return FIFOScheduler()


@pytest.fixture
def executor() -> SyntheticTransferExecutor:
    """Fast transfer executor (no real sleep)."""
    return SyntheticTransferExecutor(
        simulator=SyntheticTransferSimulator(
            default_bandwidth_mbps=1000.0,
            enable_real_sleep=False,
        )
    )


@pytest.fixture
def telemetry() -> TelemetrySnapshot:
    """Minimal telemetry snapshot for unit-level scheduling calls."""
    return TelemetrySnapshot(
        active_transfers_count=0,
        queue_length=1,
    )


@pytest.fixture
def api_client(sqlite_engine: Engine) -> Generator[TestClient, None, None]:
    """FastAPI TestClient with in-memory repository dependency override."""
    Base.metadata.create_all(bind=sqlite_engine)
    in_memory_repo = InMemoryTransferRepository()
    app.dependency_overrides[get_repository] = lambda: in_memory_repo
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _make_request(
    source_id: str = "univ-lab",
    destination_id: str = "poly-clinic",
    size_bytes: int = 1_048_576,
    priority: PriorityLevel = PriorityLevel.NORMAL,
    status: TransferStatus = TransferStatus.SUBMITTED,
    submitted_at: datetime | None = None,
) -> TransferRequest:
    return TransferRequest(
        request_id=uuid4(),
        source_id=source_id,
        destination_id=destination_id,
        data_size_bytes=size_bytes,
        priority=priority,
        submitted_at=submitted_at or datetime.now(UTC),
        security_profile=SecurityProfile.QKD_AES_256_GCM,
        data_category=DataCategory.DIAGNOSTIC_REPORT,
        status=status,
    )


# ===========================================================================
# 1. API Contract Tests — POST /transfers → 201 + ApiResponse[TransferRequest]
# ===========================================================================


class TestApiSubmission:
    """Verifies the HTTP submission contract (TASK-1.7 DoD surface tested from G perspective)."""

    def test_post_authorized_transfer_returns_201(self, api_client: TestClient) -> None:
        """POST with authorized source/destination returns 201 Created in QUEUED status."""
        payload = {
            "source_id": "univ-lab",
            "destination_id": "poly-clinic",
            "data_size_bytes": 5_242_880,
            "priority": PriorityLevel.NORMAL,
            "security_profile": SecurityProfile.QKD_AES_256_GCM,
            "data_category": DataCategory.DIAGNOSTIC_REPORT,
            "metadata": {"study_ref": "tb-integration-001"},
        }
        response = api_client.post("/api/v1/transfers", json=payload)
        assert response.status_code == 201

        body = response.json()
        assert body["success"] is True
        assert "timestamp" in body
        data = body["data"]
        assert UUID(data["request_id"])  # valid UUID
        assert data["source_id"] == "univ-lab"
        assert data["destination_id"] == "poly-clinic"
        assert data["status"] == TransferStatus.QUEUED

    def test_api_response_envelope_shape(self, api_client: TestClient) -> None:
        """ApiResponse[TransferRequest] always contains success, data, timestamp keys."""
        response = api_client.post(
            "/api/v1/transfers",
            json={
                "source_id": "univ-lab",
                "destination_id": "poly-clinic",
                "data_size_bytes": 1024,
            },
        )
        body = response.json()
        assert set(body.keys()) >= {"success", "data", "timestamp"}
        # data must carry all required TransferRequest fields
        data = body["data"]
        for field in [
            "request_id", "source_id", "destination_id", "data_size_bytes",
            "priority", "submitted_at", "status", "security_profile",
            "data_category", "retry_count", "max_retries",
        ]:
            assert field in data, f"Missing field '{field}' in transfer response data"

    def test_post_unauthorized_destination_returns_queued_as_rejected(
        self, api_client: TestClient
    ) -> None:
        """Unauthorized destination still returns 201, but status is REJECTED."""
        payload = {
            "source_id": "univ-lab",
            "destination_id": "rogue-destination-node",
            "data_size_bytes": 1024,
        }
        response = api_client.post("/api/v1/transfers", json=payload)
        assert response.status_code == 201
        assert response.json()["data"]["status"] == TransferStatus.REJECTED

    def test_post_unauthorized_source_returns_rejected(self, api_client: TestClient) -> None:
        """Unknown source node is rejected by the policy engine."""
        payload = {
            "source_id": "unknown-external-attacker",
            "destination_id": "poly-clinic",
            "data_size_bytes": 1024,
        }
        response = api_client.post("/api/v1/transfers", json=payload)
        assert response.status_code == 201
        assert response.json()["data"]["status"] == TransferStatus.REJECTED

    def test_phi_metadata_guard_returns_422(self, api_client: TestClient) -> None:
        """Forbidden PHI keys in metadata are rejected before persisting (422 Unprocessable)."""
        payload = {
            "source_id": "univ-lab",
            "destination_id": "poly-clinic",
            "data_size_bytes": 1024,
            "metadata": {"patient_name": "John Doe"},
        }
        response = api_client.post("/api/v1/transfers", json=payload)
        assert response.status_code == 422
        assert response.json()["success"] is False

    def test_zero_byte_payload_rejected_422(self, api_client: TestClient) -> None:
        """data_size_bytes=0 violates schema constraint (gt=0) and returns 422."""
        response = api_client.post(
            "/api/v1/transfers",
            json={"source_id": "univ-lab", "destination_id": "poly-clinic", "data_size_bytes": 0},
        )
        assert response.status_code == 422

    def test_get_transfer_by_id_200(self, api_client: TestClient) -> None:
        """GET /transfers/{id} returns the persisted request correctly."""
        create_res = api_client.post(
            "/api/v1/transfers",
            json={"source_id": "univ-lab", "destination_id": "poly-clinic", "data_size_bytes": 2048},
        )
        request_id = create_res.json()["data"]["request_id"]

        get_res = api_client.get(f"/api/v1/transfers/{request_id}")
        assert get_res.status_code == 200
        body = get_res.json()
        assert body["success"] is True
        assert body["data"]["request_id"] == request_id

    def test_get_unknown_transfer_returns_404(self, api_client: TestClient) -> None:
        """GET /transfers/{unknown-uuid} returns 404 with ApiErrorEnvelope."""
        unknown = uuid4()
        response = api_client.get(f"/api/v1/transfers/{unknown}")
        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert body["errors"][0]["code"] == "TRANSFER_NOT_FOUND"

    def test_list_transfers_all(self, api_client: TestClient) -> None:
        """GET /transfers returns all submitted requests."""
        for _ in range(3):
            api_client.post(
                "/api/v1/transfers",
                json={"source_id": "univ-lab", "destination_id": "poly-clinic", "data_size_bytes": 1024},
            )
        res = api_client.get("/api/v1/transfers")
        assert res.status_code == 200
        assert len(res.json()["data"]) == 3

    def test_list_transfers_status_filter(self, api_client: TestClient) -> None:
        """GET /transfers?status=QUEUED returns only QUEUED transfers."""
        # Create authorized (→ QUEUED) and unauthorized (→ REJECTED) transfers
        api_client.post(
            "/api/v1/transfers",
            json={"source_id": "univ-lab", "destination_id": "poly-clinic", "data_size_bytes": 1024},
        )
        api_client.post(
            "/api/v1/transfers",
            json={"source_id": "unknown-src", "destination_id": "poly-clinic", "data_size_bytes": 1024},
        )
        queued = api_client.get("/api/v1/transfers?status=QUEUED")
        rejected = api_client.get("/api/v1/transfers?status=REJECTED")
        assert len(queued.json()["data"]) == 1
        assert len(rejected.json()["data"]) == 1

    def test_cancel_queued_transfer(self, api_client: TestClient) -> None:
        """POST /transfers/{id}/cancel transitions QUEUED → CANCELLED."""
        create_res = api_client.post(
            "/api/v1/transfers",
            json={"source_id": "univ-lab", "destination_id": "poly-clinic", "data_size_bytes": 4096},
        )
        request_id = create_res.json()["data"]["request_id"]
        assert create_res.json()["data"]["status"] == TransferStatus.QUEUED

        cancel_res = api_client.post(f"/api/v1/transfers/{request_id}/cancel")
        assert cancel_res.status_code == 200
        assert cancel_res.json()["data"]["status"] == TransferStatus.CANCELLED

    def test_cancel_nonexistent_transfer_returns_404(self, api_client: TestClient) -> None:
        """Cancelling an unknown UUID returns 404 with error envelope."""
        unknown = uuid4()
        response = api_client.post(f"/api/v1/transfers/{unknown}/cancel")
        assert response.status_code == 404

    def test_health_endpoint(self, api_client: TestClient) -> None:
        """GET /api/v1/health returns healthy ApiResponse."""
        response = api_client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["status"] == "healthy"


# ===========================================================================
# 2. Policy Engine Integration Tests
# ===========================================================================


class TestPolicyEngineIntegration:
    """Policy engine evaluated against real TransferRequest objects."""

    def test_authorized_pair_allows(self, policy_engine: SecurityPolicyEngine) -> None:
        req = _make_request("univ-lab", "poly-clinic")
        req.status = TransferStatus.VALIDATING
        decision = policy_engine.evaluate_request(req)
        assert decision.allowed is True
        assert decision.reason_code == "AUTH_SUCCESS"

    def test_unauthorized_destination_denied(self, policy_engine: SecurityPolicyEngine) -> None:
        req = _make_request("univ-lab", "black-market-server")
        req.status = TransferStatus.VALIDATING
        decision = policy_engine.evaluate_request(req)
        assert decision.allowed is False
        assert decision.reason_code == "DEST_UNAUTHORIZED"

    def test_unknown_source_denied(self, policy_engine: SecurityPolicyEngine) -> None:
        req = _make_request("unknown-node", "poly-clinic")
        req.status = TransferStatus.VALIDATING
        decision = policy_engine.evaluate_request(req)
        assert decision.allowed is False
        assert decision.reason_code == "SRC_UNAUTHORIZED"

    def test_policy_decision_includes_required_profile(
        self, policy_engine: SecurityPolicyEngine
    ) -> None:
        req = _make_request("univ-lab", "poly-clinic")
        req.status = TransferStatus.VALIDATING
        decision = policy_engine.evaluate_request(req)
        assert decision.required_security_profile is not None


# ===========================================================================
# 3. FIFO Scheduler Integration Tests
# ===========================================================================


class TestFIFOSchedulerIntegration:
    """FIFO scheduler determinism and ordering guarantees."""

    def test_fifo_selects_oldest_submitted(
        self, fifo_scheduler: FIFOScheduler, telemetry: TelemetrySnapshot
    ) -> None:
        """Requests submitted at t1 < t2 < t3 are always selected in submission order."""
        base_time = datetime.now(UTC)
        reqs = [
            _make_request(submitted_at=base_time + timedelta(seconds=i), status=TransferStatus.QUEUED)
            for i in range(3)
        ]
        # Shuffle to verify ordering is by submitted_at, not list position
        shuffled = [reqs[2], reqs[0], reqs[1]]

        decision = fifo_scheduler.select_next(shuffled, telemetry)
        assert decision is not None
        assert decision.selected_request_id == reqs[0].request_id

    def test_fifo_returns_none_on_empty_queue(
        self, fifo_scheduler: FIFOScheduler, telemetry: TelemetrySnapshot
    ) -> None:
        decision = fifo_scheduler.select_next([], telemetry)
        assert decision is None

    def test_fifo_skips_non_queued_requests(
        self, fifo_scheduler: FIFOScheduler, telemetry: TelemetrySnapshot
    ) -> None:
        """FIFO only considers QUEUED requests, not SUBMITTED or TRANSFERRING."""
        queued = _make_request(status=TransferStatus.QUEUED)
        submitted = _make_request(status=TransferStatus.SUBMITTED)
        transferring = _make_request(status=TransferStatus.TRANSFERRING)
        decision = fifo_scheduler.select_next([submitted, transferring, queued], telemetry)
        assert decision is not None
        assert decision.selected_request_id == queued.request_id

    def test_fifo_decision_has_correct_policy_name(
        self, fifo_scheduler: FIFOScheduler, telemetry: TelemetrySnapshot
    ) -> None:
        req = _make_request(status=TransferStatus.QUEUED)
        decision = fifo_scheduler.select_next([req], telemetry)
        assert decision is not None
        assert decision.policy_name == SchedulingPolicyName.FIFO

    def test_fifo_three_candidate_submission_order(
        self, fifo_scheduler: FIFOScheduler, telemetry: TelemetrySnapshot
    ) -> None:
        """Full FIFO ordering across t1, t2, t3 — simulates draining the queue."""
        base = datetime.now(UTC)
        r1 = _make_request(submitted_at=base, status=TransferStatus.QUEUED)
        r2 = _make_request(submitted_at=base + timedelta(seconds=1), status=TransferStatus.QUEUED)
        r3 = _make_request(submitted_at=base + timedelta(seconds=2), status=TransferStatus.QUEUED)

        queue = [r3, r1, r2]  # intentionally disordered

        d1 = fifo_scheduler.select_next(queue, telemetry)
        assert d1 is not None
        assert d1.selected_request_id == r1.request_id

        queue.remove(r1)
        d2 = fifo_scheduler.select_next(queue, telemetry)
        assert d2 is not None
        assert d2.selected_request_id == r2.request_id

        queue.remove(r2)
        d3 = fifo_scheduler.select_next(queue, telemetry)
        assert d3 is not None
        assert d3.selected_request_id == r3.request_id


# ===========================================================================
# 4. Integrity Verifier Unit Tests
# ===========================================================================


class TestIntegrityVerifier:
    """SHA-256 checksum generation and constant-time verification."""

    def test_calculate_checksum_bytes(self) -> None:
        payload = b"hello-qshield"
        expected = hashlib.sha256(payload).hexdigest()
        assert IntegrityVerifier.calculate_checksum(payload) == expected

    def test_checksum_empty_payload(self) -> None:
        digest = IntegrityVerifier.calculate_checksum(b"")
        assert digest == hashlib.sha256(b"").hexdigest()

    def test_verify_checksum_match(self) -> None:
        payload = b"quantum-secure-data"
        checksum = IntegrityVerifier.calculate_checksum(payload)
        assert IntegrityVerifier.verify_checksum(payload, checksum) is True

    def test_verify_checksum_mismatch(self) -> None:
        payload = b"original"
        tampered = b"tampered"
        checksum = IntegrityVerifier.calculate_checksum(payload)
        assert IntegrityVerifier.verify_checksum(tampered, checksum) is False

    def test_verify_hashes_match_case_insensitive(self) -> None:
        h = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert IntegrityVerifier.verify_hashes_match(h, h.upper()) is True

    def test_verify_hashes_empty_returns_false(self) -> None:
        assert IntegrityVerifier.verify_hashes_match("", "abc") is False
        assert IntegrityVerifier.verify_hashes_match("abc", "") is False

    def test_stream_checksum_matches_full_buffer(self) -> None:
        payload = generate_synthetic_buffer(256 * 1024, seed=1)
        expected = IntegrityVerifier.calculate_checksum(payload)
        chunks = [payload[i : i + 65536] for i in range(0, len(payload), 65536)]
        streamed = IntegrityVerifier.calculate_stream_checksum(iter(chunks))
        assert streamed == expected

    def test_raise_on_error_raises_exception(self) -> None:
        from app.core.exceptions import IntegrityVerificationError

        payload = b"data"
        wrong_checksum = "00" * 32
        with pytest.raises(IntegrityVerificationError, match="Integrity check failed"):
            IntegrityVerifier.verify_checksum(payload, wrong_checksum, raise_on_error=True)


# ===========================================================================
# 5. SyntheticTransferExecutor Tests
# ===========================================================================


class TestSyntheticTransferExecutor:
    """Transfer executor integration: success and corruption scenarios."""

    def test_valid_transfer_success_and_verification(self, executor: SyntheticTransferExecutor) -> None:
        """DoD: Valid synthetic transfer returns success=True and verification_success=True."""
        plan = TransferExecutionPlan(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=512 * 1024,  # 512 KB
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            allocated_bandwidth_mbps=1000.0,
            simulate_failures=False,
        )
        result = executor.execute(plan)

        assert result.success is True
        assert result.verification_success is True
        assert len(result.payload_checksum_sha256) == 64  # valid SHA-256 hex
        assert result.bytes_transferred == 512 * 1024
        assert result.actual_duration_seconds > 0.0
        assert result.effective_throughput_mbps > 0.0
        assert result.error_message is None

    def test_corrupted_transfer_returns_failure(self, executor: SyntheticTransferExecutor) -> None:
        """DoD: Artificially corrupted checksum returns success=False."""
        plan = TransferExecutionPlan(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=4096,
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            allocated_bandwidth_mbps=1000.0,
            simulate_failures=True,  # triggers bit-flip corruption
        )
        result = executor.execute(plan)

        assert result.success is False
        assert result.verification_success is False
        assert result.error_message is not None
        assert "mismatch" in result.error_message.lower() or "integrity" in result.error_message.lower()

    def test_executor_checksum_deterministic_for_same_size(
        self, executor: SyntheticTransferExecutor
    ) -> None:
        """Same payload size generates the same SHA-256 (deterministic synthetic buffer)."""
        plan = TransferExecutionPlan(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=65536,
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            allocated_bandwidth_mbps=1000.0,
        )
        r1 = executor.execute(plan)
        r2 = executor.execute(TransferExecutionPlan(**{**plan.model_dump(), "request_id": uuid4()}))
        assert r1.payload_checksum_sha256 == r2.payload_checksum_sha256

    def test_executor_duration_scales_with_size(self, executor: SyntheticTransferExecutor) -> None:
        """Larger payloads take proportionally longer at the same bandwidth."""
        small_plan = TransferExecutionPlan(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=1024,
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            allocated_bandwidth_mbps=100.0,
        )
        large_plan = TransferExecutionPlan(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=1024 * 1024,
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            allocated_bandwidth_mbps=100.0,
        )
        small_result = executor.execute(small_plan)
        large_result = executor.execute(large_plan)
        assert large_result.actual_duration_seconds > small_result.actual_duration_seconds


# ===========================================================================
# 6. Full End-to-End Tracer Bullet Integration
# ===========================================================================


class TestTracerBulletEndToEnd:
    """Full lifecycle integration driving all Q-SHIELD components together.

    Exercises the architecture boundary sequence:
        POST /transfers API → Policy Gate → FIFO Scheduler → Executor → COMPLETED
    """

    def _run_full_lifecycle(
        self,
        repo: InMemoryTransferRepository,
        policy_engine: SecurityPolicyEngine,
        fifo_scheduler: FIFOScheduler,
        executor: SyntheticTransferExecutor,
        telemetry: TelemetrySnapshot,
        size_bytes: int = 1_024,
        simulate_failure: bool = False,
    ) -> tuple[TransferRequest, TransferResult]:
        """Drives a single request through the complete lifecycle programmatically."""

        # -- Stage 1: SUBMITTED
        req = _make_request(size_bytes=size_bytes)
        assert req.status == TransferStatus.SUBMITTED
        saved = repo.create_request(req)

        # -- Stage 2: SUBMITTED -> VALIDATING
        TransferStateMachine.transition(saved, TransferStatus.VALIDATING, actor="api")
        repo.update_status(saved.request_id, TransferStatus.VALIDATING, actor="api")
        assert saved.status == TransferStatus.VALIDATING

        # -- Stage 3: Policy evaluation -> QUEUED or REJECTED
        decision = policy_engine.evaluate_request(saved)
        assert decision.allowed is True, f"Expected AUTH_SUCCESS, got {decision.reason_code}"
        TransferStateMachine.transition(
            saved, TransferStatus.QUEUED, actor="policy_engine", reason=decision.reason_code
        )
        repo.update_status(saved.request_id, TransferStatus.QUEUED, actor="policy_engine")
        assert saved.status == TransferStatus.QUEUED

        # -- Stage 4: FIFO scheduling -> SCHEDULED
        sched_decision = fifo_scheduler.select_next([saved], telemetry)
        assert sched_decision is not None
        assert sched_decision.selected_request_id == saved.request_id
        TransferStateMachine.transition(
            saved, TransferStatus.SCHEDULED, actor="fifo_scheduler"
        )
        repo.update_status(saved.request_id, TransferStatus.SCHEDULED, actor="fifo_scheduler")
        assert saved.status == TransferStatus.SCHEDULED
        assert saved.scheduled_at is not None

        # -- Stage 5: SCHEDULED -> TRANSFERRING
        TransferStateMachine.transition(
            saved, TransferStatus.TRANSFERRING, actor="transfer_executor"
        )
        repo.update_status(saved.request_id, TransferStatus.TRANSFERRING, actor="transfer_executor")
        assert saved.status == TransferStatus.TRANSFERRING
        assert saved.transfer_started_at is not None

        # -- Stage 6: Execute synthetic transfer
        plan = TransferExecutionPlan(
            request_id=saved.request_id,
            source_id=saved.source_id,
            destination_id=saved.destination_id,
            data_size_bytes=saved.data_size_bytes,
            security_profile=saved.security_profile,
            allocated_bandwidth_mbps=1000.0,
            simulate_failures=simulate_failure,
        )
        result = executor.execute(plan)

        # -- Stage 7: TRANSFERRING -> VERIFYING
        TransferStateMachine.transition(
            saved, TransferStatus.VERIFYING, actor="integrity_verifier"
        )
        repo.update_status(saved.request_id, TransferStatus.VERIFYING, actor="integrity_verifier")
        assert saved.status == TransferStatus.VERIFYING
        assert saved.transfer_completed_at is not None

        # -- Stage 8: VERIFYING -> COMPLETED or FAILED
        if result.verification_success:
            saved.payload_checksum_sha256 = result.payload_checksum_sha256
            # Push checksum into the stored repo copy before status transition
            with repo._lock:
                stored = repo._requests.get(saved.request_id)
                if stored:
                    stored.payload_checksum_sha256 = result.payload_checksum_sha256
            TransferStateMachine.transition(
                saved, TransferStatus.COMPLETED, actor="transfer_executor"
            )
            repo.update_status(saved.request_id, TransferStatus.COMPLETED, actor="transfer_executor")
            assert saved.status == TransferStatus.COMPLETED
            assert saved.verification_completed_at is not None
        else:
            TransferStateMachine.transition(
                saved, TransferStatus.FAILED, actor="transfer_executor", reason="Integrity mismatch"
            )
            repo.update_status(saved.request_id, TransferStatus.FAILED, actor="transfer_executor")
            assert saved.status == TransferStatus.FAILED

        return saved, result

    def test_happy_path_complete_lifecycle(
        self,
        repo: InMemoryTransferRepository,
        policy_engine: SecurityPolicyEngine,
        fifo_scheduler: FIFOScheduler,
        executor: SyntheticTransferExecutor,
        telemetry: TelemetrySnapshot,
    ) -> None:
        """Full lifecycle from SUBMITTED to COMPLETED with all 8 state transitions verified."""
        req, result = self._run_full_lifecycle(
            repo, policy_engine, fifo_scheduler, executor, telemetry
        )
        assert req.status == TransferStatus.COMPLETED
        assert result.success is True
        assert result.verification_success is True
        assert req.payload_checksum_sha256 is not None
        assert len(req.payload_checksum_sha256) == 64  # valid SHA-256 hex string

    def test_corrupted_transfer_ends_in_failed(
        self,
        repo: InMemoryTransferRepository,
        policy_engine: SecurityPolicyEngine,
        fifo_scheduler: FIFOScheduler,
        executor: SyntheticTransferExecutor,
        telemetry: TelemetrySnapshot,
    ) -> None:
        """Simulated corruption in the network layer drives lifecycle to FAILED."""
        req, result = self._run_full_lifecycle(
            repo, policy_engine, fifo_scheduler, executor, telemetry, simulate_failure=True
        )
        assert req.status == TransferStatus.FAILED
        assert result.success is False
        assert result.verification_success is False

    def test_complete_lifecycle_state_transition_audit_trail(
        self,
        repo: InMemoryTransferRepository,
        policy_engine: SecurityPolicyEngine,
        fifo_scheduler: FIFOScheduler,
        executor: SyntheticTransferExecutor,
        telemetry: TelemetrySnapshot,
    ) -> None:
        """The transition log in InMemoryRepo records all lifecycle steps in order."""
        req, _ = self._run_full_lifecycle(
            repo, policy_engine, fifo_scheduler, executor, telemetry
        )
        transitions = repo.get_transitions(req.request_id)
        # Expect at minimum: SUBMITTED→VALIDATING, VALIDATING→QUEUED, QUEUED→SCHEDULED,
        # SCHEDULED→TRANSFERRING, TRANSFERRING→VERIFYING, VERIFYING→COMPLETED
        statuses = [(t.from_status, t.to_status) for t in transitions]
        assert (TransferStatus.SUBMITTED, TransferStatus.VALIDATING) in statuses
        assert (TransferStatus.VALIDATING, TransferStatus.QUEUED) in statuses
        assert (TransferStatus.QUEUED, TransferStatus.SCHEDULED) in statuses
        assert (TransferStatus.SCHEDULED, TransferStatus.TRANSFERRING) in statuses
        assert (TransferStatus.TRANSFERRING, TransferStatus.VERIFYING) in statuses
        assert (TransferStatus.VERIFYING, TransferStatus.COMPLETED) in statuses

    def test_lifecycle_persists_checksum_on_completion(
        self,
        repo: InMemoryTransferRepository,
        policy_engine: SecurityPolicyEngine,
        fifo_scheduler: FIFOScheduler,
        executor: SyntheticTransferExecutor,
        telemetry: TelemetrySnapshot,
    ) -> None:
        """Completed request has payload_checksum_sha256 persisted in repository."""
        req, result = self._run_full_lifecycle(
            repo, policy_engine, fifo_scheduler, executor, telemetry
        )
        persisted = repo.get_request(req.request_id)
        assert persisted is not None
        assert persisted.payload_checksum_sha256 == result.payload_checksum_sha256

    def test_completeness_requires_checksum_before_completing(
        self, sample_transfer_request: TransferRequest
    ) -> None:
        """FSM guard: transitioning to COMPLETED without checksum raises InvalidStateTransitionError."""
        req = sample_transfer_request
        req.status = TransferStatus.VERIFYING
        req.payload_checksum_sha256 = None  # explicitly clear

        with pytest.raises(InvalidStateTransitionError, match="payload_checksum_sha256"):
            TransferStateMachine.transition(req, TransferStatus.COMPLETED, actor="verifier")

    def test_fifo_ordering_preserved_in_full_lifecycle(
        self,
        repo: InMemoryTransferRepository,
        policy_engine: SecurityPolicyEngine,
        fifo_scheduler: FIFOScheduler,
        telemetry: TelemetrySnapshot,
    ) -> None:
        """When three requests are queued, FIFO selects them in submission order."""
        base = datetime.now(UTC)
        requests = []
        for i in range(3):
            req = _make_request(
                submitted_at=base + timedelta(milliseconds=i * 10),
                status=TransferStatus.SUBMITTED,
            )
            saved = repo.create_request(req)
            # Drive through policy to QUEUED
            TransferStateMachine.transition(saved, TransferStatus.VALIDATING, actor="api")
            decision = policy_engine.evaluate_request(saved)
            assert decision.allowed
            TransferStateMachine.transition(saved, TransferStatus.QUEUED, actor="policy")
            repo.update_status(saved.request_id, TransferStatus.QUEUED, actor="policy")
            requests.append(saved)

        # Reverse list to prove ordering comes from submitted_at not list order
        queue = list(reversed(requests))
        for expected_req in requests:
            sched_decision = fifo_scheduler.select_next(queue, telemetry)
            assert sched_decision is not None
            assert sched_decision.selected_request_id == expected_req.request_id
            queue = [r for r in queue if r.request_id != expected_req.request_id]

    def test_api_post_then_get_full_roundtrip(self, api_client: TestClient) -> None:
        """HTTP POST creates a request; GET retrieves the same UUID, source, and status."""
        post_res = api_client.post(
            "/api/v1/transfers",
            json={
                "source_id": "univ-lab",
                "destination_id": "poly-clinic",
                "data_size_bytes": 5_242_880,
                "priority": PriorityLevel.HIGH,
                "security_profile": SecurityProfile.QKD_AES_256_GCM,
                "data_category": DataCategory.DIAGNOSTIC_REPORT,
            },
        )
        assert post_res.status_code == 201
        created = post_res.json()["data"]
        request_id = created["request_id"]

        get_res = api_client.get(f"/api/v1/transfers/{request_id}")
        assert get_res.status_code == 200
        fetched = get_res.json()["data"]

        assert fetched["request_id"] == request_id
        assert fetched["source_id"] == "univ-lab"
        assert fetched["destination_id"] == "poly-clinic"
        assert fetched["status"] == TransferStatus.QUEUED

    def test_multiple_requests_lifecycle_independent(
        self,
        repo: InMemoryTransferRepository,
        policy_engine: SecurityPolicyEngine,
        fifo_scheduler: FIFOScheduler,
        executor: SyntheticTransferExecutor,
        telemetry: TelemetrySnapshot,
    ) -> None:
        """Five independent requests can each complete the full lifecycle independently."""
        completed = []
        for i in range(5):
            req, result = self._run_full_lifecycle(
                repo, policy_engine, fifo_scheduler, executor, telemetry, size_bytes=1024 * (i + 1)
            )
            assert req.status == TransferStatus.COMPLETED
            assert result.success is True
            completed.append(req.request_id)

        # All UUIDs are unique
        assert len(set(completed)) == 5


# ===========================================================================
# 7. FSM Transition Invariant Verification (integration context)
# ===========================================================================


class TestFSMIntegrationInvariants:
    """State machine invariants exercised in integration context with real request objects."""

    def test_no_skip_transitions_allowed(self, sample_transfer_request: TransferRequest) -> None:
        """Skip transitions (SUBMITTED -> TRANSFERRING, SUBMITTED -> COMPLETED) are always rejected."""
        for forbidden_target in [
            TransferStatus.TRANSFERRING,
            TransferStatus.VERIFYING,
            TransferStatus.COMPLETED,
            TransferStatus.SCHEDULED,
        ]:
            req2 = sample_transfer_request.model_copy(deep=True)
            with pytest.raises(InvalidStateTransitionError):
                TransferStateMachine.transition(req2, forbidden_target, actor="test")

    def test_terminal_states_are_final(self) -> None:
        """All terminal states (COMPLETED, FAILED, REJECTED, CANCELLED, EXPIRED) cannot transition."""
        terminal_states = [
            TransferStatus.COMPLETED,
            TransferStatus.FAILED,
            TransferStatus.REJECTED,
            TransferStatus.CANCELLED,
            TransferStatus.EXPIRED,
        ]
        for terminal in terminal_states:
            req = _make_request(status=terminal)
            if terminal == TransferStatus.COMPLETED:
                req.payload_checksum_sha256 = "a" * 64
            assert TransferStateMachine.is_terminal(terminal), f"{terminal} should be terminal"
            with pytest.raises(InvalidStateTransitionError, match="terminal state"):
                TransferStateMachine.transition(req, TransferStatus.QUEUED, actor="test")

    def test_scheduled_at_set_on_scheduled_transition(
        self, sample_transfer_request: TransferRequest
    ) -> None:
        req = sample_transfer_request
        TransferStateMachine.transition(req, TransferStatus.VALIDATING, actor="api")
        TransferStateMachine.transition(req, TransferStatus.QUEUED, actor="policy")
        assert req.scheduled_at is None
        TransferStateMachine.transition(req, TransferStatus.SCHEDULED, actor="scheduler")
        assert req.scheduled_at is not None

    def test_transfer_started_at_set_on_transferring_transition(
        self, sample_transfer_request: TransferRequest
    ) -> None:
        req = sample_transfer_request
        req.status = TransferStatus.SCHEDULED
        assert req.transfer_started_at is None
        TransferStateMachine.transition(req, TransferStatus.TRANSFERRING, actor="executor")
        assert req.transfer_started_at is not None

    def test_verification_completed_at_set_on_completed(
        self, sample_transfer_request: TransferRequest
    ) -> None:
        req = sample_transfer_request
        req.status = TransferStatus.VERIFYING
        req.payload_checksum_sha256 = "b" * 64
        assert req.verification_completed_at is None
        TransferStateMachine.transition(req, TransferStatus.COMPLETED, actor="verifier")
        assert req.verification_completed_at is not None
