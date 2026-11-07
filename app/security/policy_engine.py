"""Deterministic security policy engine for Q-SHIELD transfer authorization."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4

from app.core.enums import QKDLinkStatus, SecurityProfile
from app.models.policy_decision import PolicyDecision
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.security.authorization import AuthorizationTable

logger = logging.getLogger(__name__)


class IPolicyEngine(ABC):
    """Deterministic security and eligibility evaluation interface contract."""

    @abstractmethod
    def evaluate_request(
        self,
        request: TransferRequest,
        telemetry: TelemetrySnapshot | None = None,
    ) -> PolicyDecision:
        """Determines if a request is authorized to be queued and executed."""
        pass


class SecurityPolicyEngine(IPolicyEngine):
    """Deterministic security gate evaluating source/destination permissions and profiles."""

    def __init__(
        self,
        auth_table: AuthorizationTable | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        if auth_table is not None:
            self._auth_table = auth_table
        elif config_path is not None:
            self._auth_table = AuthorizationTable.load_from_yaml(config_path)
        else:
            self._auth_table = AuthorizationTable.load_from_yaml()

    @property
    def auth_table(self) -> AuthorizationTable:
        return self._auth_table

    def evaluate_request(
        self,
        request: TransferRequest,
        telemetry: TelemetrySnapshot | None = None,
    ) -> PolicyDecision:
        """Determines whether a transfer request is permitted to proceed into the scheduling queue.

        Security constraints checked deterministically:
            1. Source node authorization.
            2. Destination node authorization for the source.
            3. Cryptographic security profile permission for the link.
            4. Maximum data volume / payload size bounds.
            5. Hardware link availability when required by security profile.

        Args:
            request: Candidate TransferRequest to evaluate.
            telemetry: Optional real-time system and link telemetry snapshot.

        Returns:
            Structured PolicyDecision with machine-readable reason code.
        """
        # 1. Base authorization table check (source, destination, profile, payload size)
        allowed, reason_code, explanation = self._auth_table.validate_transfer(
            source_id=request.source_id,
            destination_id=request.destination_id,
            profile=request.security_profile,
            data_size_bytes=request.data_size_bytes,
        )

        if not allowed:
            return PolicyDecision(
                decision_id=uuid4(),
                request_id=request.request_id,
                allowed=False,
                reason_code=reason_code,
                explanation=explanation,
                required_security_profile=request.security_profile,
            )

        # 2. Environmental telemetry checks (QKD hardware status if profile requires it)
        requires_qkd = request.security_profile in (
            SecurityProfile.QKD_AES_256_GCM,
            SecurityProfile.QKD_OTP_SIM,
            SecurityProfile.POST_QUANTUM_HYBRID,
        )

        if requires_qkd and telemetry is not None:
            link_key = f"{request.source_id}->{request.destination_id}"
            qkd_metric = telemetry.qkd_metrics.get(link_key)
            if qkd_metric is not None and qkd_metric.link_status == QKDLinkStatus.UNAVAILABLE:
                return PolicyDecision(
                    decision_id=uuid4(),
                    request_id=request.request_id,
                    allowed=False,
                    reason_code="QKD_LINK_UNAVAILABLE",
                    explanation=(
                        f"Quantum link '{link_key}' is UNAVAILABLE. "
                        f"Requested profile '{request.security_profile.value}' requires active QKD."
                    ),
                    required_security_profile=request.security_profile,
                )

        return PolicyDecision(
            decision_id=uuid4(),
            request_id=request.request_id,
            allowed=True,
            reason_code="AUTH_SUCCESS",
            explanation=f"Transfer authorized between '{request.source_id}' and '{request.destination_id}'.",
            required_security_profile=request.security_profile,
        )


__all__ = [
    "IPolicyEngine",
    "SecurityPolicyEngine",
]
