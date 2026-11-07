"""Unit tests for deterministic security policy engine (TASK-1.4)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.enums import (
    DataCategory,
    PriorityLevel,
    QKDLinkStatus,
    SecurityProfile,
    TransferStatus,
)
from app.models.telemetry import QKDResourceMetrics, TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.security.authorization import AuthorizationTable, NodeAuthConfig
from app.security.policy_engine import SecurityPolicyEngine


@pytest.fixture
def policy_engine() -> SecurityPolicyEngine:
    """Returns a SecurityPolicyEngine loaded with standard configurations."""
    return SecurityPolicyEngine()


def _create_test_request(
    source_id: str = "univ-lab",
    destination_id: str = "poly-clinic",
    security_profile: SecurityProfile = SecurityProfile.QKD_AES_256_GCM,
    data_size_bytes: int = 5_242_880,
) -> TransferRequest:
    return TransferRequest(
        request_id=uuid4(),
        source_id=source_id,
        destination_id=destination_id,
        data_size_bytes=data_size_bytes,
        priority=PriorityLevel.NORMAL,
        submitted_at=datetime.now(UTC),
        security_profile=security_profile,
        data_category=DataCategory.DIAGNOSTIC_REPORT,
        status=TransferStatus.VALIDATING,
    )


def test_authorized_transfer_decision(policy_engine: SecurityPolicyEngine) -> None:
    """Verifies that authorized source and destination with valid profile returns AUTH_SUCCESS."""
    req = _create_test_request("univ-lab", "poly-clinic", SecurityProfile.QKD_AES_256_GCM)
    decision = policy_engine.evaluate_request(req)

    assert decision.allowed is True
    assert decision.reason_code == "AUTH_SUCCESS"
    assert decision.required_security_profile == SecurityProfile.QKD_AES_256_GCM
    assert decision.request_id == req.request_id


def test_unauthorized_destination_dod(policy_engine: SecurityPolicyEngine) -> None:
    """Verifies TASK-1.4 DoD: Unauthorized destination returns allowed=False with reason_code='DEST_UNAUTHORIZED'."""
    req = _create_test_request("univ-lab", "rogue-unauthorized-clinic")
    decision = policy_engine.evaluate_request(req)

    assert decision.allowed is False
    assert decision.reason_code == "DEST_UNAUTHORIZED"
    assert "rogue-unauthorized-clinic" in decision.explanation


def test_unauthorized_source(policy_engine: SecurityPolicyEngine) -> None:
    """Verifies that an unknown or untrusted source node is rejected with SRC_UNAUTHORIZED."""
    req = _create_test_request("rogue-external-node", "poly-clinic")
    decision = policy_engine.evaluate_request(req)

    assert decision.allowed is False
    assert decision.reason_code == "SRC_UNAUTHORIZED"


def test_security_profile_not_permitted() -> None:
    """Verifies rejection when a node requests a profile not permitted by authorization policy."""
    restricted_table = AuthorizationTable(
        nodes={
            "isolated-clinic": NodeAuthConfig(
                node_id="isolated-clinic",
                name="Isolated Clinic",
                authorized_destinations=frozenset({"poly-clinic"}),
                allowed_profiles=frozenset({SecurityProfile.STANDARD_TLS_FALLBACK}),
            )
        }
    )
    engine = SecurityPolicyEngine(auth_table=restricted_table)
    req = _create_test_request("isolated-clinic", "poly-clinic", SecurityProfile.QKD_OTP_SIM)

    decision = engine.evaluate_request(req)
    assert decision.allowed is False
    assert decision.reason_code == "PROFILE_NOT_PERMITTED"


def test_payload_size_exceeded() -> None:
    """Verifies rejection when requested payload size exceeds configured maximum limits."""
    small_limit_table = AuthorizationTable(
        nodes={
            "univ-lab": NodeAuthConfig(
                node_id="univ-lab",
                name="University Lab",
                authorized_destinations=frozenset({"poly-clinic"}),
                allowed_profiles=frozenset({SecurityProfile.QKD_AES_256_GCM}),
                max_payload_bytes=10_000,
            )
        }
    )
    engine = SecurityPolicyEngine(auth_table=small_limit_table)
    req = _create_test_request("univ-lab", "poly-clinic", data_size_bytes=50_000)

    decision = engine.evaluate_request(req)
    assert decision.allowed is False
    assert decision.reason_code == "PAYLOAD_SIZE_EXCEEDED"


def test_telemetry_qkd_unavailable_rejection(policy_engine: SecurityPolicyEngine) -> None:
    """Verifies that an active telemetry link marked UNAVAILABLE blocks QKD-dependent transfers."""
    req = _create_test_request("univ-lab", "poly-clinic", SecurityProfile.QKD_AES_256_GCM)
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
