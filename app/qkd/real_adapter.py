"""ETSI GS QKD 014 physical QKD adapter adhering to Spec §7.2 (TASK-5.3)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.enums import QKDLinkStatus
from app.core.exceptions import QKDHardwareNotConfiguredError
from app.models.scheduling import KeyHandle
from app.models.telemetry import QKDResourceMetrics
from app.qkd.base import IQKDAdapter
from app.qkd.capabilities import QKDCapabilities

logger = logging.getLogger(__name__)


class QKDRealAdapter(IQKDAdapter):
    """ETSI GS QKD 014 REST client for physical QKD Key Management Entities (KME).

    In accordance with Q-SHIELD Spec §7.2:
    - Remains strictly unconfigured unless authorized physical KME endpoint and
      credentials/mTLS certificates are explicitly provisioned.
    - Contains zero hardcoded or fabricated endpoints.
    - Derives effective key refill rate (bits/sec) from consecutive `/status`
      buffer deltas because ETSI 014 abstracts raw optical QBER metrics.
    """

    def __init__(
        self,
        endpoint_url: str | None = None,
        auth_token: str | None = None,
        ca_cert_path: str | None = None,
        client_cert_path: str | None = None,
        client_key_path: str | None = None,
        is_hardware_authorized: bool = False,
        timeout_seconds: float = 5.0,
    ) -> None:
        has_mtls = bool(client_cert_path and client_key_path)
        has_token = bool(auth_token)

        if not is_hardware_authorized or not endpoint_url or not (has_mtls or has_token):
            raise QKDHardwareNotConfiguredError(
                "Real QKD hardware adapter is explicitly unconfigured. Authorized KME endpoint "
                "and either mTLS certificates (client_cert_path, client_key_path) or auth_token "
                "must be provided per Q-SHIELD Spec §7.2. "
                "Use QKDSimulatorAdapter for development and simulation modes.",
                code="QKD_HARDWARE_NOT_CONFIGURED",
            )

        self.endpoint_url = endpoint_url.rstrip("/")
        self.auth_token = auth_token
        self.ca_cert_path = ca_cert_path
        self.client_cert_path = client_cert_path
        self.client_key_path = client_key_path
        self.timeout_seconds = timeout_seconds

        # Track previous /status sample per destination SAE to estimate key refill rate
        self._last_sample: dict[str, tuple[float, int]] = {}

    def _build_client(self, source_id: str) -> httpx.Client:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-SAE-ID": source_id,  # Used by UPB/RoNaQCI emulator & non-mTLS proxies
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        cert = (
            (self.client_cert_path, self.client_key_path)
            if self.client_cert_path and self.client_key_path
            else None
        )
        verify: str | bool = self.ca_cert_path if self.ca_cert_path else True

        return httpx.Client(
            base_url=self.endpoint_url,
            headers=headers,
            cert=cert,
            verify=verify,
            timeout=self.timeout_seconds,
        )

    def _fetch_etsi_status(self, source_id: str, destination_id: str) -> dict[str, Any]:
        with self._build_client(source_id) as client:
            response = client.get(f"/api/v1/keys/{destination_id}/status")
            response.raise_for_status()
            return response.json()

    def get_status(self, source_id: str, destination_id: str) -> QKDLinkStatus:
        try:
            payload = self._fetch_etsi_status(source_id, destination_id)
            stored_keys = int(payload.get("stored_key_count", 0))
            if stored_keys > 0:
                return QKDLinkStatus.ACTIVE
            return QKDLinkStatus.DEGRADED
        except httpx.HTTPError as exc:
            logger.warning("ETSI 014 status check failed for %s -> %s: %s", source_id, destination_id, exc)
            return QKDLinkStatus.OFFLINE

    def get_resource_metrics(self, source_id: str, destination_id: str) -> QKDResourceMetrics:
        now = time.monotonic()
        t0 = time.perf_counter()
        payload = self._fetch_etsi_status(source_id, destination_id)
        rtt_ms = (time.perf_counter() - t0) * 1000.0

        stored_keys = int(payload.get("stored_key_count", 0))
        max_keys = int(payload.get("max_key_count", max(stored_keys, 1)))
        key_size_bits = int(payload.get("key_size", 256))

        available_bits = stored_keys * key_size_bits
        capacity_bits = max_keys * key_size_bits

        # Estimate net key generation/refill rate from buffer delta since last poll
        prev = self._last_sample.get(destination_id)
        if prev is not None and (now - prev[0]) > 0:
            dt = now - prev[0]
            delta_keys = max(0, stored_keys - prev[1])
            estimated_rate_bps = (delta_keys * key_size_bits) / dt
        else:
            estimated_rate_bps = 0.0
        self._last_sample[destination_id] = (now, stored_keys)

        return QKDResourceMetrics(
            source_id=source_id,
            destination_id=destination_id,
            available_key_bits=available_bits,
            max_key_bits=capacity_bits,
            key_generation_rate_bps=estimated_rate_bps,
            qber=0.0,  # Abstracted away by ETSI GS QKD 014 KME layer
            latency_ms=rtt_ms,
            status=QKDLinkStatus.ACTIVE if stored_keys > 0 else QKDLinkStatus.DEGRADED,
        )

    def acquire_key_handle(
        self, source_id: str, destination_id: str, key_size_bits: int = 256
    ) -> KeyHandle:
        with self._build_client(source_id) as client:
            response = client.post(
                f"/api/v1/keys/{destination_id}/enc_keys",
                json={"number": 1, "size": key_size_bits},
            )
            response.raise_for_status()
            data = response.json()

        keys = data.get("keys", [])
        if not keys:
            raise RuntimeError(f"ETSI 014 KME returned empty key list for {source_id} -> {destination_id}")

        key_entry = keys[0]
        return KeyHandle(
            key_id=key_entry["key_ID"],
            key_material_b64=key_entry["key"],
            key_size_bits=key_size_bits,
            source_id=source_id,
            destination_id=destination_id,
            acquired_at=datetime.now(timezone.utc),
        )

    def get_capabilities(
        self, source_id: str | None = None, destination_id: str | None = None
    ) -> QKDCapabilities:
        if not source_id or not destination_id:
            return QKDCapabilities(
                min_key_size_bits=128,
                max_key_size_bits=4096,
                max_keys_per_request=128,
                supports_etsi_014=True,
            )

        payload = self._fetch_etsi_status(source_id, destination_id)
        return QKDCapabilities(
            min_key_size_bits=int(payload.get("min_key_size", 128)),
            max_key_size_bits=int(payload.get("max_key_size", 4096)),
            max_keys_per_request=int(payload.get("max_key_per_request", 128)),
            supports_etsi_014=True,
        )


__all__ = ["QKDRealAdapter"]