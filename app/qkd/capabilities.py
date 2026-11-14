"""QKD capabilities and feature negotiation schema."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.enums import SecurityProfile


class QKDCapabilities(BaseModel):
    """Supported cryptographic profiles, key lengths, and telemetry capabilities of a QKD link/adapter."""

    supported_key_lengths_bits: list[int] = Field(
        default_factory=lambda: [128, 256, 512],
        description="List of supported key sizes in bits.",
    )
    supported_security_profiles: list[SecurityProfile] = Field(
        default_factory=lambda: [
            SecurityProfile.QKD_AES_256_GCM,
            SecurityProfile.QKD_OTP_SIM,
            SecurityProfile.POST_QUANTUM_HYBRID,
        ],
        description="Security profiles supported by the QKD adapter.",
    )
    supported_telemetry_fields: list[str] = Field(
        default_factory=lambda: [
            "link_status",
            "key_buffer_available_keys",
            "key_generation_rate_bps",
            "key_service_latency_ms",
        ],
        description="Available telemetry metrics exposed by this adapter.",
    )
    max_buffer_capacity: int = Field(
        default=2000,
        ge=0,
        description="Maximum capacity of key pool in number of keys.",
    )
    key_generation_rate_bps: float = Field(
        default=50000.0,
        ge=0.0,
        description="Key generation rate in bits per second.",
    )
    supports_key_routing: bool = Field(
        default=False,
        description="Whether multi-hop key routing is supported.",
    )
    supports_outage_simulation: bool = Field(
        default=True,
        description="Whether temporary outages can be simulated.",
    )
    is_simulated: bool = Field(
        default=True,
        description="Whether this adapter is running against simulated or physical infrastructure.",
    )
    vendor_info: str = Field(
        default="Q-SHIELD QKD Subsystem",
        description="Vendor or model identifier.",
    )

    def can_support_profile(self, profile: SecurityProfile | str) -> bool:
        """Checks if the given security profile is supported."""
        target = profile.value if isinstance(profile, SecurityProfile) else str(profile)
        return any(
            (p.value if isinstance(p, SecurityProfile) else str(p)) == target
            for p in self.supported_security_profiles
        )

    def can_support_key_length(self, length_bits: int) -> bool:
        """Checks if the requested key length in bits is supported."""
        return length_bits in self.supported_key_lengths_bits


__all__ = ["QKDCapabilities"]
