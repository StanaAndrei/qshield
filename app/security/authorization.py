"""Authorization table and security policy configuration loader for Q-SHIELD."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.enums import SecurityProfile

logger = logging.getLogger(__name__)

DEFAULT_SECURITY_CONFIG_PATH = Path("configs/security.yaml")
MAX_PAYLOAD_BYTES_GLOBAL = 500_000_000_000  # 500 GB


@dataclass(frozen=True)
class NodeAuthConfig:
    """Security authorization directives for a single logical node."""

    node_id: str
    name: str
    authorized_destinations: frozenset[str]
    allowed_profiles: frozenset[SecurityProfile]
    max_payload_bytes: int = MAX_PAYLOAD_BYTES_GLOBAL


class AuthorizationTable:
    """Deterministic lookup table enforcing node pair authorizations and cryptographic profiles."""

    def __init__(
        self,
        nodes: dict[str, NodeAuthConfig] | None = None,
        global_max_payload: int = MAX_PAYLOAD_BYTES_GLOBAL,
    ) -> None:
        self._nodes: dict[str, NodeAuthConfig] = nodes or self._default_nodes()
        self._global_max_payload = global_max_payload

    @classmethod
    def load_from_yaml(cls, path: Path | str | None = None) -> AuthorizationTable:
        """Loads authorization table from a YAML configuration file with graceful fallback."""
        config_file = Path(path) if path else DEFAULT_SECURITY_CONFIG_PATH
        if not config_file.is_file():
            logger.warning(
                "Security config %s not found. Falling back to built-in nominal table.",
                config_file,
            )
            return cls()

        try:
            with open(config_file, encoding="utf-8") as f:
                data: dict[str, Any] = yaml.safe_load(f) or {}

            nodes_data = data.get("nodes", {})
            nodes: dict[str, NodeAuthConfig] = {}

            for node_id, cfg in nodes_data.items():
                destinations = frozenset(cfg.get("authorized_destinations", []))
                raw_profiles = cfg.get("allowed_profiles", [])
                profiles: set[SecurityProfile] = set()
                for p in raw_profiles:
                    try:
                        profiles.add(SecurityProfile(p))
                    except ValueError:
                        # Handle potential fallback aliases
                        if p == "STANDARD_TLS":
                            profiles.add(SecurityProfile.STANDARD_TLS_FALLBACK)
                        else:
                            logger.warning("Unknown security profile in config: %s", p)

                nodes[node_id] = NodeAuthConfig(
                    node_id=node_id,
                    name=cfg.get("name", node_id),
                    authorized_destinations=destinations,
                    allowed_profiles=frozenset(profiles),
                    max_payload_bytes=cfg.get("max_payload_bytes", MAX_PAYLOAD_BYTES_GLOBAL),
                )

            constraints = data.get("constraints", {})
            global_max = constraints.get("max_payload_bytes_global", MAX_PAYLOAD_BYTES_GLOBAL)
            return cls(nodes=nodes, global_max_payload=global_max)

        except Exception as err:
            logger.error("Failed to parse %s: %s. Using default table.", config_file, err)
            return cls()

    @staticmethod
    def _default_nodes() -> dict[str, NodeAuthConfig]:
        """Provides default authorization table when configs/security.yaml is absent."""
        all_profiles = frozenset({
            SecurityProfile.QKD_AES_256_GCM,
            SecurityProfile.QKD_OTP_SIM,
            SecurityProfile.POST_QUANTUM_HYBRID,
            SecurityProfile.STANDARD_TLS_FALLBACK,
        })
        return {
            "univ-lab": NodeAuthConfig(
                node_id="univ-lab",
                name="University Research Laboratory",
                authorized_destinations=frozenset({"poly-clinic", "central-hospital", "research-cluster"}),
                allowed_profiles=all_profiles,
            ),
            "poly-clinic": NodeAuthConfig(
                node_id="poly-clinic",
                name="Polyclinic Diagnostic Center",
                authorized_destinations=frozenset({"univ-lab", "central-hospital"}),
                allowed_profiles=all_profiles,
            ),
            "central-hospital": NodeAuthConfig(
                node_id="central-hospital",
                name="Central Metropolitan Hospital",
                authorized_destinations=frozenset({"univ-lab", "poly-clinic"}),
                allowed_profiles=all_profiles,
            ),
            "research-cluster": NodeAuthConfig(
                node_id="research-cluster",
                name="Regional HPC Medical Research Cluster",
                authorized_destinations=frozenset({"univ-lab"}),
                allowed_profiles=frozenset({
                    SecurityProfile.QKD_AES_256_GCM,
                    SecurityProfile.POST_QUANTUM_HYBRID,
                    SecurityProfile.STANDARD_TLS_FALLBACK,
                }),
            ),
        }

    def is_source_known(self, source_id: str) -> bool:
        """Returns True if the source node is registered in the authorization matrix."""
        return source_id in self._nodes

    def is_destination_authorized(self, source_id: str, destination_id: str) -> bool:
        """Checks if destination_id is in the whitelist of permitted endpoints for source_id."""
        node = self._nodes.get(source_id)
        if not node:
            return False
        return destination_id in node.authorized_destinations

    def is_profile_allowed(self, source_id: str, profile: SecurityProfile) -> bool:
        """Checks if the requested cryptographic security profile is approved for this source node."""
        node = self._nodes.get(source_id)
        if not node:
            return False
        return profile in node.allowed_profiles

    def get_max_payload_bytes(self, source_id: str) -> int:
        """Returns the maximum allowed transfer payload bytes for the specified source."""
        node = self._nodes.get(source_id)
        if not node:
            return self._global_max_payload
        return min(node.max_payload_bytes, self._global_max_payload)

    def validate_transfer(
        self,
        source_id: str,
        destination_id: str,
        profile: SecurityProfile,
        data_size_bytes: int,
    ) -> tuple[bool, str, str]:
        """Performs deterministic validation of node pair, security profile, and payload size.

        Returns:
            Tuple of (allowed: bool, reason_code: str, explanation: str).
        """
        # 1. Source check
        if not self.is_source_known(source_id):
            return (
                False,
                "SRC_UNAUTHORIZED",
                f"Source node '{source_id}' is not a recognized or authorized participant.",
            )

        # 2. Destination check
        if not self.is_destination_authorized(source_id, destination_id):
            return (
                False,
                "DEST_UNAUTHORIZED",
                f"Destination node '{destination_id}' is not in the authorized access whitelist for source '{source_id}'.",
            )

        # 3. Security profile check
        if not self.is_profile_allowed(source_id, profile):
            return (
                False,
                "PROFILE_NOT_PERMITTED",
                f"Security profile '{profile.value}' is not permitted for source '{source_id}'.",
            )

        # 4. Size constraints
        max_bytes = self.get_max_payload_bytes(source_id)
        if data_size_bytes > max_bytes:
            return (
                False,
                "PAYLOAD_SIZE_EXCEEDED",
                f"Payload size {data_size_bytes:,} bytes exceeds maximum authorized limit of {max_bytes:,} bytes.",
            )

        return (
            True,
            "AUTH_SUCCESS",
            f"Transfer authorized between '{source_id}' and '{destination_id}' under {profile.value}.",
        )


__all__ = [
    "AuthorizationTable",
    "NodeAuthConfig",
]
