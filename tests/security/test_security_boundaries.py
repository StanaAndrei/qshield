"""Security boundary tests verifying deterministic authorization and AI non-override (TASK-5.6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.enums import (
    DataCategory,
    PriorityLevel,
    QKDLinkStatus,
    SchedulingPolicyName,
    SecurityProfile,
    TransferStatus,
)
from app.core.exceptions import (
    InvalidStateTransitionError,
    QKDHardwareNotConfiguredError,
)
from app.core.state_machine import TransferStateMachine
from app.models.scheduling import (
    KeyHandle,
    PredictionFeatures,
    PredictionOutput,
    TransferExecutionPlan,
)
from app.models.telemetry import QKDResourceMetrics, TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.qkd.real_adapter import QKDRealAdapter
from app.qkd.simulator import QKDSimulatorAdapter
from app.scheduler.ai_assisted import AIAssistedScheduler
from app.scheduler.base import IPredictor
from app.security.policy_engine import SecurityPolicyEngine
from app.services.scheduling_service import SchedulingService
from app.transfer.crypto_adapter import CryptoAdapter
from app.transfer.executor import SyntheticTransferExecutor
from tests.fixtures.mock_repository import InMemoryTransferRepository


class AdversarialInstantPredictor(IPredictor):
    """Adversarial predictor that claims impossible performance (0 duration, 0 miss risk) to attempt bypassing policies."""

    def predict(self, features: PredictionFeatures) -> PredictionOutput:
        return PredictionOutput(
            predicted_duration_seconds=0.0001,
            deadline_miss_probability=0.0,
            confidence_interval_95=(0.0, 0.0001),
            model_version="adversarial_super_ai_v999",
            is_fallback_analytical=False,
        )

    def get_model_version(self) -> str:
        return "adversarial_super_ai_v999"


class TestAuthorizationBoundaries:
    """Tests that unauthorized endpoints, profiles, and volumes are deterministically rejected."""

    @pytest.fixture
    def policy_engine(self) -> SecurityPolicyEngine:
        return SecurityPolicyEngine()

    def test_unauthorized_destination_rejected(self, policy_engine: SecurityPolicyEngine) -> None:
        req = TransferRequest(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="unauthorized-foreign-node",
            data_size_bytes=1000,
            priority=PriorityLevel.NORMAL,
            submitted_at=datetime.now(UTC),
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.SUBMITTED,
        )
        decision = policy_engine.evaluate_request(req)
        assert decision.allowed is False
        assert decision.reason_code == "DEST_UNAUTHORIZED"

    def test_unauthorized_source_rejected(self, policy_engine: SecurityPolicyEngine) -> None:
        req = TransferRequest(
            request_id=uuid4(),
            source_id="unauthorized-rogue-lab",
            destination_id="poly-clinic",
            data_size_bytes=1000,
            priority=PriorityLevel.NORMAL,
            submitted_at=datetime.now(UTC),
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.SUBMITTED,
        )
        decision = policy_engine.evaluate_request(req)
        assert decision.allowed is False
        assert decision.reason_code == "SRC_UNAUTHORIZED"

    def test_unapproved_profile_rejected(self, policy_engine: SecurityPolicyEngine) -> None:
        # research-cluster is not authorized for QKD_OTP_SIM in configs/security.yaml
        req = TransferRequest(
            request_id=uuid4(),
            source_id="research-cluster",
            destination_id="univ-lab",
            data_size_bytes=1000,
            priority=PriorityLevel.NORMAL,
            submitted_at=datetime.now(UTC),
            security_profile=SecurityProfile.QKD_OTP_SIM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.SUBMITTED,
        )
        decision = policy_engine.evaluate_request(req)
        assert decision.allowed is False
        assert decision.reason_code == "PROFILE_NOT_PERMITTED"

    def test_oversized_payload_rejected(self, policy_engine: SecurityPolicyEngine) -> None:
        # Direct validation of size boundary exceeding global limit
        allowed, reason_code, _ = policy_engine.auth_table.validate_transfer(
            source_id="univ-lab",
            destination_id="poly-clinic",
            profile=SecurityProfile.QKD_AES_256_GCM,
            data_size_bytes=600_000_000_000,  # 600 GB exceeds 500 GB limit
        )
        assert allowed is False
        assert reason_code == "PAYLOAD_SIZE_EXCEEDED"

    def test_qkd_link_outage_rejects_qkd_profile(self, policy_engine: SecurityPolicyEngine) -> None:
        req = TransferRequest(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=5_000_000,
            priority=PriorityLevel.HIGH,
            submitted_at=datetime.now(UTC),
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.SUBMITTED,
        )
        # Snapshot showing QKD link UNAVAILABLE
        telemetry = TelemetrySnapshot(
            qkd_metrics={
                "univ-lab->poly-clinic": QKDResourceMetrics(
                    source_id="univ-lab",
                    destination_id="poly-clinic",
                    link_status=QKDLinkStatus.UNAVAILABLE,
                    key_buffer_available_keys=0,
                    key_generation_rate_bps=0.0,
                    key_service_latency_ms=999.0,
                )
            }
        )
        decision = policy_engine.evaluate_request(req, telemetry=telemetry)
        assert decision.allowed is False
        assert decision.reason_code == "QKD_LINK_UNAVAILABLE"

    def test_api_submission_marks_unauthorized_request_as_rejected(
        self, test_client: TestClient
    ) -> None:
        payload = {
            "source_id": "univ-lab",
            "destination_id": "unauthorized-external-server",
            "data_size_bytes": 1024,
            "priority": PriorityLevel.NORMAL.value,
            "security_profile": SecurityProfile.QKD_AES_256_GCM.value,
            "data_category": DataCategory.DIAGNOSTIC_REPORT.value,
            "metadata": {"test": "unauthorized_boundary"},
        }
        response = test_client.post("/api/v1/transfers", json=payload)
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["status"] == TransferStatus.REJECTED.value

    def test_state_machine_prevents_advancing_rejected_request(self) -> None:
        fsm = TransferStateMachine()
        req = TransferRequest(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=1000,
            priority=PriorityLevel.NORMAL,
            submitted_at=datetime.now(UTC),
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.REJECTED,
        )
        with pytest.raises(InvalidStateTransitionError):
            fsm.transition(req, TransferStatus.SCHEDULED)
        with pytest.raises(InvalidStateTransitionError):
            fsm.transition(req, TransferStatus.TRANSFERRING)


class TestAIPredictionCannotOverrideSecurityPolicy:
    """Tests enforcing Spec §1.2: AI predictions can never override security policy or select ineligible requests."""

    def test_ai_scheduler_never_selects_unauthorized_or_rejected_candidate(
        self, mock_telemetry_generator: Any
    ) -> None:
        telemetry = mock_telemetry_generator()
        scheduler = AIAssistedScheduler(predictor=AdversarialInstantPredictor())

        # Candidate pool: 1 REJECTED request with tiny size/fake instant duration, 1 valid QUEUED request
        rejected_req = TransferRequest(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="unauthorized-hacker-host",
            data_size_bytes=10,
            priority=PriorityLevel.CRITICAL,  # Critical priority
            submitted_at=datetime.now(UTC),
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.REJECTED,  # Policy rejected
        )

        valid_req = TransferRequest(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=5_000_000,
            priority=PriorityLevel.LOW,
            submitted_at=datetime.now(UTC),
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.QUEUED,
        )

        decision = scheduler.select_next(
            candidate_requests=[rejected_req, valid_req],
            telemetry=telemetry,
        )

        assert decision is not None
        # Strict enforcement: valid request selected, rejected request NEVER selected
        assert decision.selected_request_id == valid_req.request_id
        assert decision.selected_request_id != rejected_req.request_id

    def test_ai_scheduler_returns_none_if_all_candidates_unauthorized(
        self, mock_telemetry_generator: Any
    ) -> None:
        telemetry = mock_telemetry_generator()
        scheduler = AIAssistedScheduler(predictor=AdversarialInstantPredictor())

        rejected_reqs = [
            TransferRequest(
                request_id=uuid4(),
                source_id="univ-lab",
                destination_id="bad-node-1",
                data_size_bytes=100,
                priority=PriorityLevel.CRITICAL,
                submitted_at=datetime.now(UTC),
                security_profile=SecurityProfile.QKD_AES_256_GCM,
                data_category=DataCategory.DIAGNOSTIC_REPORT,
                status=TransferStatus.REJECTED,
            ),
            TransferRequest(
                request_id=uuid4(),
                source_id="bad-node-2",
                destination_id="poly-clinic",
                data_size_bytes=100,
                priority=PriorityLevel.CRITICAL,
                submitted_at=datetime.now(UTC),
                security_profile=SecurityProfile.QKD_AES_256_GCM,
                data_category=DataCategory.DIAGNOSTIC_REPORT,
                status=TransferStatus.REJECTED,
            ),
        ]

        decision = scheduler.select_next(
            candidate_requests=rejected_reqs,
            telemetry=telemetry,
        )
        assert decision is None

    def test_scheduling_service_strictly_filters_policy_violators(
        self, in_memory_repo: InMemoryTransferRepository, mock_telemetry_generator: Any
    ) -> None:
        service = SchedulingService(
            repository=in_memory_repo,
            policy=SchedulingPolicyName.AI_ASSISTED,
            predictor=AdversarialInstantPredictor(),
            telemetry_provider=lambda: mock_telemetry_generator(),
        )

        # Place one REJECTED and one QUEUED in repo
        req_rejected = TransferRequest(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="bad-dest",
            data_size_bytes=500,
            priority=PriorityLevel.CRITICAL,
            submitted_at=datetime.now(UTC),
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.REJECTED,
        )
        req_queued = TransferRequest(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=1000,
            priority=PriorityLevel.NORMAL,
            submitted_at=datetime.now(UTC),
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.QUEUED,
        )
        in_memory_repo.create_request(req_rejected)
        in_memory_repo.create_request(req_queued)

        decision = service.run_scheduling_cycle()

        assert decision is not None
        assert decision.selected_request_id == req_queued.request_id


class TestPhysicalSimulatedSeparation:
    """Tests ensuring physical QKD adapter fails safe and simulation flags are strictly preserved."""

    def test_real_qkd_adapter_fails_safe_without_credentials(self) -> None:
        with pytest.raises(QKDHardwareNotConfiguredError) as exc_info:
            QKDRealAdapter()
        assert "explicitly unconfigured" in str(exc_info.value)
        assert exc_info.value.code == "QKD_HARDWARE_NOT_CONFIGURED"

    def test_simulator_adapter_maintains_is_simulated_tag(self) -> None:
        sim = QKDSimulatorAdapter()
        metrics = sim.get_resource_metrics("univ-lab", "poly-clinic")
        assert metrics.is_simulated is True
        caps = sim.get_capabilities()
        assert caps.is_simulated is True


class TestTransferIntegrityBoundary:
    """Tests ensuring incomplete or corrupted transfers are never marked as success."""

    def test_corrupted_transfer_never_marked_success(self) -> None:
        executor = SyntheticTransferExecutor()
        plan = TransferExecutionPlan(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=10_000,
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            allocated_bandwidth_mbps=100.0,
            simulate_failures=True,  # Artificially inject transport corruption
        )
        result = executor.execute(plan)
        assert result.success is False
        assert result.verification_success is False
        assert result.error_message is not None

    def test_tampered_crypto_payload_never_marked_success(self) -> None:
        crypto = CryptoAdapter()
        executor = SyntheticTransferExecutor(crypto_adapter=crypto)
        handle = KeyHandle(
            key_id=f"qkd-key-{uuid4()}",
            key_length_bits=256,
            source_id="univ-lab",
            destination_id="poly-clinic",
        )
        plan = TransferExecutionPlan(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=10_000,
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            key_handle=handle,
            allocated_bandwidth_mbps=100.0,
            simulate_failures=True,
        )
        result = executor.execute(plan)
        assert result.success is False
        assert result.verification_success is False
        assert result.error_message is not None
        assert "failed" in result.error_message.lower()
