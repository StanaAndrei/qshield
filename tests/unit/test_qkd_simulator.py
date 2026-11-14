"""Unit tests for QKD adapter interface, capabilities, simulator, and real adapter placeholder (Milestone 5)."""

from __future__ import annotations

import pytest

from app.core.enums import QKDLinkStatus, SecurityProfile
from app.core.exceptions import (
    QKDError,
    QKDHardwareNotConfiguredError,
    QKDKeyBufferDepletedError,
)
from app.models.scheduling import KeyHandle
from app.qkd.base import IQKDAdapter
from app.qkd.capabilities import QKDCapabilities
from app.qkd.real_adapter import QKDRealAdapter
from app.qkd.simulator import MockQKDAdapter, QKDSimulatorAdapter


class TestQKDCapabilities:
    """Tests for QKDCapabilities model and negotiation methods."""

    def test_default_capabilities(self) -> None:
        caps = QKDCapabilities()
        assert caps.can_support_profile(SecurityProfile.QKD_AES_256_GCM)
        assert caps.can_support_profile(SecurityProfile.QKD_OTP_SIM)
        assert caps.can_support_key_length(256)
        assert caps.can_support_key_length(128)
        assert not caps.can_support_key_length(1024)
        assert caps.is_simulated is True
        assert caps.max_buffer_capacity == 2000

    def test_custom_capabilities(self) -> None:
        caps = QKDCapabilities(
            supported_key_lengths_bits=[256],
            supported_security_profiles=[SecurityProfile.QKD_AES_256_GCM],
            max_buffer_capacity=500,
            is_simulated=False,
            vendor_info="Toshiba QKD KMS",
        )
        assert caps.can_support_profile(SecurityProfile.QKD_AES_256_GCM)
        assert not caps.can_support_profile(SecurityProfile.QKD_OTP_SIM)
        assert caps.vendor_info == "Toshiba QKD KMS"
        assert not caps.is_simulated


class TestQKDSimulatorAdapter:
    """Tests for QKDSimulatorAdapter leaky-bucket dynamics, buffer depletion, and outages."""

    @pytest.fixture
    def sim_adapter(self) -> QKDSimulatorAdapter:
        return QKDSimulatorAdapter(
            initial_keys=5,
            max_buffer_capacity=10,
            key_gen_rate_bps=2560.0,  # 10 keys per second
            service_latency_ms=2.5,
            seed=42,
        )

    def test_initial_state_connected(self, sim_adapter: QKDSimulatorAdapter) -> None:
        status = sim_adapter.get_status("node-a", "node-b")
        assert status == QKDLinkStatus.CONNECTED
        metrics = sim_adapter.get_resource_metrics("node-a", "node-b")
        assert metrics.key_buffer_available_keys == 5
        assert metrics.link_status == QKDLinkStatus.CONNECTED
        assert metrics.key_generation_rate_bps == 2560.0
        assert metrics.is_simulated is True

    def test_acquire_key_handle_opaque_without_secret_leakage(self, sim_adapter: QKDSimulatorAdapter) -> None:
        handle = sim_adapter.acquire_key_handle("node-a", "node-b", key_size_bits=256)
        assert isinstance(handle, KeyHandle)
        assert handle.key_length_bits == 256
        assert handle.source_id == "node-a"
        assert handle.destination_id == "node-b"
        assert handle.key_id.startswith("qkd-key-")
        # Ensure no secret key bytes attribute exists on KeyHandle
        assert not hasattr(handle, "key_bytes")
        assert not hasattr(handle, "raw_key")

        # Verify buffer decremented
        metrics = sim_adapter.get_resource_metrics("node-a", "node-b")
        assert metrics.key_buffer_available_keys == 4

    def test_buffer_depletion_returns_resource_wait(self, sim_adapter: QKDSimulatorAdapter) -> None:
        # Drain all 5 keys
        for _ in range(5):
            sim_adapter.acquire_key_handle("node-a", "node-b")

        # Now buffer is 0
        assert sim_adapter.is_resource_wait("node-a", "node-b") is True
        status = sim_adapter.get_status("node-a", "node-b")
        assert status == QKDLinkStatus.DEGRADED

        metrics = sim_adapter.get_resource_metrics("node-a", "node-b")
        assert metrics.key_buffer_available_keys == 0
        assert metrics.link_status == QKDLinkStatus.DEGRADED

        # try_acquire returns None and DEGRADED
        handle, try_status = sim_adapter.try_acquire_key_handle("node-a", "node-b")
        assert handle is None
        assert try_status == QKDLinkStatus.DEGRADED

        # acquire_key_handle raises QKDKeyBufferDepletedError
        with pytest.raises(QKDKeyBufferDepletedError) as exc_info:
            sim_adapter.acquire_key_handle("node-a", "node-b")
        assert exc_info.value.code == "KEY_BUFFER_DEPLETED"

    def test_replenish_restores_key_availability(self, sim_adapter: QKDSimulatorAdapter) -> None:
        # Drain buffer
        for _ in range(5):
            sim_adapter.acquire_key_handle("node-a", "node-b")
        assert sim_adapter.get_status("node-a", "node-b") == QKDLinkStatus.DEGRADED

        # Replenish buffer
        added = sim_adapter.replenish(keys=3, source_id="node-a", destination_id="node-b")
        assert added == 3
        assert sim_adapter.get_status("node-a", "node-b") == QKDLinkStatus.CONNECTED
        assert sim_adapter.is_resource_wait("node-a", "node-b") is False

        # Can acquire again
        handle = sim_adapter.acquire_key_handle("node-a", "node-b")
        assert isinstance(handle, KeyHandle)
        assert sim_adapter.get_resource_metrics("node-a", "node-b").key_buffer_available_keys == 2

    def test_replenish_capped_at_max_capacity(self, sim_adapter: QKDSimulatorAdapter) -> None:
        # Top up past max_buffer_capacity (10)
        added = sim_adapter.replenish(keys=100, source_id="node-a", destination_id="node-b")
        assert added == 5  # Initial 5 + 5 = max 10
        metrics = sim_adapter.get_resource_metrics("node-a", "node-b")
        assert metrics.key_buffer_available_keys == 10

    def test_simulated_leaky_bucket_time_advancement(self) -> None:
        current_virtual_time = 100.0

        def virtual_time_provider() -> float:
            return current_virtual_time

        adapter = QKDSimulatorAdapter(
            initial_keys=0,
            max_buffer_capacity=20,
            key_gen_rate_bps=2560.0,  # 10 keys per second
            time_provider=virtual_time_provider,
        )

        assert adapter.get_status("a", "b") == QKDLinkStatus.DEGRADED

        # Advance virtual clock by 0.5s -> +5 keys
        current_virtual_time = 100.5
        metrics = adapter.get_resource_metrics("a", "b")
        assert metrics.key_buffer_available_keys == 5
        assert metrics.link_status == QKDLinkStatus.CONNECTED

    def test_manual_outage_lifecycle(self, sim_adapter: QKDSimulatorAdapter) -> None:
        sim_adapter.set_outage("node-a", "node-b", is_outage=True)
        assert sim_adapter.is_in_outage("node-a", "node-b") is True
        assert sim_adapter.get_status("node-a", "node-b") == QKDLinkStatus.UNAVAILABLE

        # Trying to acquire key raises QKDError
        with pytest.raises(QKDError) as exc_info:
            sim_adapter.acquire_key_handle("node-a", "node-b")
        assert exc_info.value.code == "QKD_LINK_UNAVAILABLE"

        # Clear outage
        sim_adapter.set_outage("node-a", "node-b", is_outage=False)
        assert sim_adapter.get_status("node-a", "node-b") == QKDLinkStatus.CONNECTED
        handle = sim_adapter.acquire_key_handle("node-a", "node-b")
        assert isinstance(handle, KeyHandle)

    def test_temporary_timed_outage(self) -> None:
        current_time = 100.0

        def mock_time() -> float:
            return current_time

        adapter = QKDSimulatorAdapter(
            initial_keys=10,
            time_provider=mock_time,
        )

        adapter.simulate_outage("node-a", "node-b", duration_seconds=5.0)
        assert adapter.get_status("node-a", "node-b") == QKDLinkStatus.UNAVAILABLE

        # Advance time by 3 seconds (still in outage)
        current_time = 103.0
        assert adapter.get_status("node-a", "node-b") == QKDLinkStatus.UNAVAILABLE

        # Advance time past 5 seconds (outage expired)
        current_time = 105.1
        assert adapter.get_status("node-a", "node-b") == QKDLinkStatus.CONNECTED

    def test_mock_qkd_adapter_alias(self) -> None:
        assert MockQKDAdapter is QKDSimulatorAdapter
        mock = MockQKDAdapter(initial_keys=10)
        assert isinstance(mock, IQKDAdapter)


class TestQKDRealAdapter:
    """Tests for defensive QKDRealAdapter placeholder enforcing Spec §7.2."""

    def test_initialization_without_credentials_raises_qkd_not_configured(self) -> None:
        with pytest.raises(QKDHardwareNotConfiguredError) as exc_info:
            QKDRealAdapter()

        message = str(exc_info.value)
        assert "explicitly unconfigured" in message
        assert "Spec §7.2" in message
        assert "QKDSimulatorAdapter" in message
        assert exc_info.value.code == "QKD_HARDWARE_NOT_CONFIGURED"

    def test_initialization_with_unauthorized_params_raises(self) -> None:
        with pytest.raises(QKDHardwareNotConfiguredError):
            QKDRealAdapter(endpoint_url="https://fake-qkd.internal", auth_token="token123")

    def test_authorized_initialization_still_fails_safe_on_unconnected_hardware(self) -> None:
        # Even if hardware flag is theoretically passed, physical adapter fails safe
        adapter = QKDRealAdapter(
            endpoint_url="https://qkd-kms.hospital.test",
            auth_token="test-token",
            is_hardware_authorized=True,
        )
        assert isinstance(adapter, IQKDAdapter)
        with pytest.raises(QKDHardwareNotConfiguredError):
            adapter.get_status("src", "dst")
        with pytest.raises(QKDHardwareNotConfiguredError):
            adapter.get_resource_metrics("src", "dst")
        with pytest.raises(QKDHardwareNotConfiguredError):
            adapter.acquire_key_handle("src", "dst")
        with pytest.raises(QKDHardwareNotConfiguredError):
            adapter.get_capabilities()
