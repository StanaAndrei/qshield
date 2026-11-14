"""Synthetic transfer executor implementation for Q-SHIELD."""

from __future__ import annotations

import logging

from app.models.scheduling import TransferExecutionPlan, TransferResult
from app.transfer.base import ITransferExecutor
from app.transfer.crypto_adapter import CryptoAdapter
from app.transfer.integrity import IntegrityVerifier
from app.transfer.simulator import (
    SyntheticTransferSimulator,
    generate_synthetic_buffer,
)

logger = logging.getLogger(__name__)


class SyntheticTransferExecutor(ITransferExecutor):
    """Executes synthetic data transfers, simulates link throughput, and verifies SHA-256 integrity."""

    def __init__(
        self,
        simulator: SyntheticTransferSimulator | None = None,
        crypto_adapter: CryptoAdapter | None = None,
    ) -> None:
        self.simulator = simulator or SyntheticTransferSimulator()
        self.crypto_adapter = crypto_adapter or CryptoAdapter()

    def execute(self, plan: TransferExecutionPlan) -> TransferResult:
        """Executes synthetic transfer according to execution plan directives.

        Args:
            plan: TransferExecutionPlan containing request details, bandwidth, and flags.

        Returns:
            TransferResult reflecting execution throughput, duration, and integrity verification.
        """
        logger.info(
            "Executing transfer for request %s (%s -> %s, size=%d bytes, bw=%.1f Mbps)",
            plan.request_id,
            plan.source_id,
            plan.destination_id,
            plan.data_size_bytes,
            plan.allocated_bandwidth_mbps,
        )

        # 1. Generate synthetic medical payload
        payload = generate_synthetic_buffer(plan.data_size_bytes)
        sender_checksum = IntegrityVerifier.calculate_checksum(payload)

        # Handle cryptographic encapsulation if key_handle is present
        if plan.key_handle is not None:
            duration = 0.0
            effective_throughput = 0.0
            received_bytes_count = 0
            try:
                pkg = self.crypto_adapter.encrypt(
                    plaintext=payload,
                    key=plan.key_handle,
                    profile=plan.security_profile,
                )
                serialized = pkg.serialize()
                received_payload, duration, effective_throughput = self.simulator.simulate_transfer(
                    payload=serialized,
                    bandwidth_mbps=plan.allocated_bandwidth_mbps,
                    simulate_corruption=plan.simulate_failures,
                )
                received_bytes_count = len(received_payload)
                decrypted = self.crypto_adapter.decrypt(
                    package=received_payload,
                    key=plan.key_handle,
                    profile=plan.security_profile,
                    expected_checksum=sender_checksum,
                )
                receiver_checksum = IntegrityVerifier.calculate_checksum(decrypted)
                integrity_ok = IntegrityVerifier.verify_hashes_match(sender_checksum, receiver_checksum)
            except Exception as e:
                logger.error("Cryptographic transfer %s failed: %s", plan.request_id, e)
                return TransferResult(
                    request_id=plan.request_id,
                    success=False,
                    actual_duration_seconds=round(duration, 4),
                    bytes_transferred=received_bytes_count,
                    effective_throughput_mbps=round(effective_throughput, 2),
                    payload_checksum_sha256=sender_checksum,
                    verification_success=False,
                    error_message=f"Cryptographic transfer/verification failed: {e}",
                    is_retryable=False,
                )

            if integrity_ok:
                logger.info("Transfer %s completed: Encrypted + SHA-256 verified (%s)", plan.request_id, sender_checksum)
                return TransferResult(
                    request_id=plan.request_id,
                    success=True,
                    actual_duration_seconds=round(duration, 4),
                    bytes_transferred=received_bytes_count,
                    effective_throughput_mbps=round(effective_throughput, 2),
                    payload_checksum_sha256=sender_checksum,
                    verification_success=True,
                    error_message=None,
                    is_retryable=False,
                )

        # Standard unencrypted synthetic transport
        received_payload, duration, effective_throughput = self.simulator.simulate_transfer(
            payload=payload,
            bandwidth_mbps=plan.allocated_bandwidth_mbps,
            simulate_corruption=plan.simulate_failures,
        )

        # 3. Receiver-side SHA-256 verification
        receiver_checksum = IntegrityVerifier.calculate_checksum(received_payload)
        integrity_ok = IntegrityVerifier.verify_hashes_match(sender_checksum, receiver_checksum)

        if integrity_ok:
            logger.info("Transfer %s completed: SHA-256 integrity verified (%s)", plan.request_id, sender_checksum)
            return TransferResult(
                request_id=plan.request_id,
                success=True,
                actual_duration_seconds=round(duration, 4),
                bytes_transferred=len(received_payload),
                effective_throughput_mbps=round(effective_throughput, 2),
                payload_checksum_sha256=sender_checksum,
                verification_success=True,
                error_message=None,
                is_retryable=False,
            )

        logger.error(
            "Transfer %s failed: Checksum mismatch (sender=%s, receiver=%s)",
            plan.request_id,
            sender_checksum,
            receiver_checksum,
        )
        return TransferResult(
            request_id=plan.request_id,
            success=False,
            actual_duration_seconds=round(duration, 4),
            bytes_transferred=len(received_payload),
            effective_throughput_mbps=round(effective_throughput, 2),
            payload_checksum_sha256=sender_checksum,
            verification_success=False,
            error_message=(
                f"Payload integrity verification failed (SHA-256 checksum mismatch): "
                f"sender={sender_checksum}, receiver={receiver_checksum}"
            ),
            is_retryable=False,
        )


__all__ = ["SyntheticTransferExecutor"]
