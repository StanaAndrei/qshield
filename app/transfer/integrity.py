"""Cryptographic SHA-256 integrity verifier for transfer payloads."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable
from typing import BinaryIO

from app.core.exceptions import IntegrityVerificationError


class IntegrityVerifier:
    """Cryptographic integrity verification using SHA-256.

    Provides streaming and in-memory checksum computation and constant-time
    verification guards against corruption and transmission tampering.
    """

    CHUNK_SIZE: int = 65536  # 64 KB streaming chunks

    @classmethod
    def calculate_checksum(cls, payload: bytes | BinaryIO) -> str:
        """Computes the SHA-256 hexadecimal checksum for the provided payload bytes or stream.

        Args:
            payload: Bytes buffer or readable binary stream.

        Returns:
            Lowercase hexadecimal SHA-256 digest string.
        """
        hasher = hashlib.sha256()

        if isinstance(payload, bytes):
            hasher.update(payload)
            return hasher.hexdigest().lower()

        # Handle file-like / binary stream
        while chunk := payload.read(cls.CHUNK_SIZE):
            hasher.update(chunk)
        return hasher.hexdigest().lower()

    @classmethod
    def calculate_stream_checksum(cls, chunk_stream: Iterable[bytes]) -> str:
        """Computes SHA-256 across an iterable/generator of byte chunks."""
        hasher = hashlib.sha256()
        for chunk in chunk_stream:
            if chunk:
                hasher.update(chunk)
        return hasher.hexdigest().lower()

    @classmethod
    def verify_checksum(
        cls,
        payload: bytes | BinaryIO,
        expected_checksum: str,
        raise_on_error: bool = False,
    ) -> bool:
        """Validates that payload data matches the expected SHA-256 checksum.

        Args:
            payload: Transferred payload bytes or binary stream to verify.
            expected_checksum: Known expected SHA-256 checksum hex string.
            raise_on_error: If True, raises IntegrityVerificationError on mismatch.

        Returns:
            True if calculated digest matches expected checksum, otherwise False.

        Raises:
            IntegrityVerificationError: If raise_on_error is True and checksums do not match.
        """
        computed = cls.calculate_checksum(payload)
        matches = cls.verify_hashes_match(computed, expected_checksum)

        if not matches and raise_on_error:
            raise IntegrityVerificationError(
                f"Integrity check failed: computed SHA-256 '{computed}' does not match expected '{expected_checksum.lower()}'.",
                code="INTEGRITY_VERIFICATION_FAILED",
            )
        return matches

    @classmethod
    def verify_hashes_match(cls, hash_a: str, hash_b: str) -> bool:
        """Constant-time comparison of two hexadecimal hash strings."""
        if not hash_a or not hash_b:
            return False
        return hmac.compare_digest(hash_a.strip().lower(), hash_b.strip().lower())


__all__ = ["IntegrityVerifier"]
