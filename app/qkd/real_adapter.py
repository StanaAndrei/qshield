"""Defensive physical QKD adapter placeholder adhering to Spec §7.2 (TASK-5.3)."""

from __future__ import annotations

import logging

from app.core.enums import QKDLinkStatus
from app.core.exceptions import QKDHardwareNotConfiguredError
from app.models.scheduling import KeyHandle
from app.models.telemetry import QKDResourceMetrics
from app.qkd.base import IQKDAdapter
from app.qkd.capabilities import QKDCapabilities

logger = logging.getLogger(__name__)


class QKDRealAdapter(IQKDAdapter):
    """Defensive placeholder for physical QKD hardware integration.

    In accordance with Q-SHIELD Spec §7.2:
    - This adapter remains strictly unconfigured until authorized physical QKD hardware,
      vendor SDK credentials, and test procedures are provisioned.
    - It contains zero fabricated or guessed API endpoints.
    - Attempting to initialize or invoke without authorized hardware configuration raises
      an explicit QKDHardwareNotConfiguredError with instructions.
    """

    def __init__(
        self,
        endpoint_url: str | None = None,
        auth_token: str | None = None,
        ca_cert_path: str | None = None,
        is_hardware_authorized: bool = False,
    ) -> None:
        """Initializes the real QKD adapter.

        Raises:
            QKDHardwareNotConfiguredError: In all cases where authorized physical hardware
                credentials, endpoints, and validation certificates are absent.
        """
        if not is_hardware_authorized or not endpoint_url or not auth_token:
            raise QKDHardwareNotConfiguredError(
                "Real QKD hardware adapter is explicitly unconfigured. Physical credentials, "
                "authorized KMS endpoint, and mutual TLS certificates must be verified and provided "
                "per Q-SHIELD Spec §7.2. Do NOT inject fabricated endpoints or placeholder credentials. "
                "Use QKDSimulatorAdapter for all development, evaluation, and simulation modes.",
                code="QKD_HARDWARE_NOT_CONFIGURED",
            )

        self.endpoint_url = endpoint_url
        self.auth_token = auth_token
        self.ca_cert_path = ca_cert_path

    def get_status(self, source_id: str, destination_id: str) -> QKDLinkStatus:
        raise QKDHardwareNotConfiguredError(
            "Physical QKD hardware is unconfigured.",
            code="QKD_HARDWARE_NOT_CONFIGURED",
        )

    def get_resource_metrics(self, source_id: str, destination_id: str) -> QKDResourceMetrics:
        raise QKDHardwareNotConfiguredError(
            "Physical QKD hardware is unconfigured.",
            code="QKD_HARDWARE_NOT_CONFIGURED",
        )

    def acquire_key_handle(
        self, source_id: str, destination_id: str, key_size_bits: int = 256
    ) -> KeyHandle:
        raise QKDHardwareNotConfiguredError(
            "Physical QKD hardware is unconfigured.",
            code="QKD_HARDWARE_NOT_CONFIGURED",
        )

    def get_capabilities(
        self, source_id: str | None = None, destination_id: str | None = None
    ) -> QKDCapabilities:
        raise QKDHardwareNotConfiguredError(
            "Physical QKD hardware is unconfigured.",
            code="QKD_HARDWARE_NOT_CONFIGURED",
        )


__all__ = ["QKDRealAdapter"]
