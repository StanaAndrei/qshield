"""Pluggable cryptographic transport adapter supporting QKD-seeded AES-256-GCM and OTP simulation (TASK-5.4)."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, Field

from app.core.enums import SecurityProfile
from app.core.exceptions import (
    CryptoError,
    DecryptionError,
)
from app.models.scheduling import KeyHandle
from app.transfer.integrity import IntegrityVerifier

logger = logging.getLogger(__name__)


def derive_key_from_handle(handle: KeyHandle, key_size_bytes: int = 32) -> bytes:
    """Deterministically derives cryptographic key material from an opaque KeyHandle using HKDF-SHA256.

    Args:
        handle: Opaque KeyHandle containing key_id and link metadata.
        key_size_bytes: Requested output key length in bytes (default 32 for 256 bits).

    Returns:
        Cryptographic key material bytes.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=key_size_bytes,
        salt=f"{handle.source_id}->{handle.destination_id}".encode(),
        info=f"QSHIELD_KEY_MATERIAL_{handle.key_id}".encode(),
    )
    return hkdf.derive(handle.key_id.encode())


def generate_otp_keystream(key_seed: bytes, length: int) -> bytes:
    """Generates an information-theoretically deterministic keystream for OTP simulation using SHA-256 counter mode.

    Args:
        key_seed: Seed key material.
        length: Desired keystream length in bytes.

    Returns:
        Keystream bytes of exact length.
    """
    if length <= 0:
        return b""

    chunks: list[bytes] = []
    counter = 0
    remaining = length
    while remaining > 0:
        counter_bytes = counter.to_bytes(8, byteorder="big")
        block = hashlib.sha256(key_seed + counter_bytes).digest()
        take = min(remaining, len(block))
        chunks.append(block[:take])
        remaining -= take
        counter += 1

    return b"".join(chunks)


class EncryptedPackage(BaseModel):
    """Encapsulation envelope for encrypted data transport."""

    profile: SecurityProfile
    ciphertext: bytes
    nonce: bytes = Field(default=b"")
    plaintext_checksum_sha256: str
    key_id: str | None = None
    associated_data: bytes | None = None
    encrypted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def serialize(self) -> bytes:
        """Serializes the encrypted package to a binary envelope for transmission."""
        header = {
            "profile": self.profile.value,
            "nonce_hex": self.nonce.hex(),
            "checksum": self.plaintext_checksum_sha256,
            "key_id": self.key_id,
            "ad_hex": self.associated_data.hex() if self.associated_data else None,
            "ciphertext_len": len(self.ciphertext),
        }
        header_bytes = json.dumps(header).encode("utf-8")
        header_len = len(header_bytes).to_bytes(4, byteorder="big")
        return header_len + header_bytes + self.ciphertext

    @classmethod
    def deserialize(cls, data: bytes) -> EncryptedPackage:
        """Deserializes a binary envelope back into an EncryptedPackage."""
        if len(data) < 4:
            raise CryptoError("Corrupted encrypted package: data too short.", code="INVALID_PACKAGE")
        header_len = int.from_bytes(data[:4], byteorder="big")
        if len(data) < 4 + header_len:
            raise CryptoError("Corrupted encrypted package: header truncated.", code="INVALID_PACKAGE")
        header_bytes = data[4 : 4 + header_len]
        ciphertext = data[4 + header_len :]
        try:
            header: dict[str, Any] = json.loads(header_bytes.decode("utf-8"))
        except Exception as e:
            raise CryptoError(f"Corrupted encrypted package header: {e}", code="INVALID_PACKAGE") from e

        return cls(
            profile=SecurityProfile(header["profile"]),
            ciphertext=ciphertext,
            nonce=bytes.fromhex(header["nonce_hex"]) if header.get("nonce_hex") else b"",
            plaintext_checksum_sha256=header["checksum"],
            key_id=header.get("key_id"),
            associated_data=bytes.fromhex(header["ad_hex"]) if header.get("ad_hex") else None,
        )


class CryptoAdapter:
    """Pluggable cryptographic transport adapter.

    Supports:
    - QKD_AES_256_GCM: Authenticated AES-256-GCM encryption with 12-byte nonce and 16-byte tag.
    - QKD_OTP_SIM: Simulated one-time pad (OTP) stream XOR cipher.
    """

    def __init__(self) -> None:
        pass

    def _resolve_key_bytes(self, key: KeyHandle | bytes, required_len: int = 32) -> tuple[bytes, str | None]:
        """Extracts or derives key bytes from KeyHandle or raw bytes."""
        if isinstance(key, KeyHandle):
            key_bytes = derive_key_from_handle(key, key_size_bytes=required_len)
            return key_bytes, key.key_id
        if isinstance(key, bytes):
            if len(key) == required_len:
                return key, None
            # If length differs, use HKDF to expand/shrink to required_len
            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=required_len,
                salt=b"qshield-key-salt",
                info=b"qshield-raw-key",
            )
            return hkdf.derive(key), None
        raise CryptoError(f"Unsupported key type: {type(key).__name__}", code="INVALID_KEY_TYPE")

    def encrypt(
        self,
        plaintext: bytes,
        key: KeyHandle | bytes,
        profile: SecurityProfile = SecurityProfile.QKD_AES_256_GCM,
        associated_data: bytes | None = None,
        nonce: bytes | None = None,
    ) -> EncryptedPackage:
        """Encrypts plaintext using the specified security profile.

        Args:
            plaintext: Raw payload bytes to encrypt.
            key: Opaque KeyHandle or raw key bytes.
            profile: Cryptographic profile (QKD_AES_256_GCM or QKD_OTP_SIM).
            associated_data: Optional authenticated associated data (AAD).
            nonce: Optional explicit nonce (for deterministic testing).

        Returns:
            EncryptedPackage containing ciphertext, nonce, and plaintext SHA-256 digest.
        """
        sender_checksum = IntegrityVerifier.calculate_checksum(plaintext)

        if profile == SecurityProfile.QKD_AES_256_GCM:
            key_bytes, key_id = self._resolve_key_bytes(key, required_len=32)
            iv = nonce if nonce is not None else secrets.token_bytes(12)
            if len(iv) != 12:
                raise CryptoError("AES-256-GCM nonce must be exactly 12 bytes.", code="INVALID_NONCE")

            aesgcm = AESGCM(key_bytes)
            ciphertext = aesgcm.encrypt(iv, plaintext, associated_data)

            return EncryptedPackage(
                profile=profile,
                ciphertext=ciphertext,
                nonce=iv,
                plaintext_checksum_sha256=sender_checksum,
                key_id=key_id,
                associated_data=associated_data,
            )

        if profile == SecurityProfile.QKD_OTP_SIM:
            # If raw bytes matching exact plaintext length are provided, use directly
            if isinstance(key, bytes) and len(key) == len(plaintext):
                keystream = key
                key_id = None
            else:
                key_seed, key_id = self._resolve_key_bytes(key, required_len=32)
                keystream = generate_otp_keystream(key_seed, len(plaintext))

            # One-time pad XOR encryption
            ciphertext = bytes(p ^ k for p, k in zip(plaintext, keystream, strict=True))

            return EncryptedPackage(
                profile=profile,
                ciphertext=ciphertext,
                nonce=b"",
                plaintext_checksum_sha256=sender_checksum,
                key_id=key_id,
                associated_data=associated_data,
            )

        raise CryptoError(f"Unsupported cryptographic profile: {profile}", code="UNSUPPORTED_PROFILE")

    def decrypt(
        self,
        package: EncryptedPackage | bytes,
        key: KeyHandle | bytes,
        profile: SecurityProfile | None = None,
        expected_checksum: str | None = None,
        associated_data: bytes | None = None,
        nonce: bytes | None = None,
    ) -> bytes:
        """Decrypts payload, verifies authenticity tag and SHA-256 checksum.

        Args:
            package: EncryptedPackage instance or serialized binary envelope.
            key: Opaque KeyHandle or raw key bytes.
            profile: Optional override for security profile.
            expected_checksum: Optional expected SHA-256 checksum (defaults to package checksum).
            associated_data: Optional authenticated associated data (overrides package AD if provided).
            nonce: Optional explicit nonce (overrides package nonce if provided).

        Returns:
            Recovered plaintext bytes.

        Raises:
            DecryptionError: If decryption or authentication tag validation fails.
            IntegrityVerificationError: If SHA-256 checksum mismatch occurs.
        """
        if isinstance(package, bytes):
            # Try deserializing envelope, else treat as raw ciphertext
            try:
                pkg = EncryptedPackage.deserialize(package)
            except CryptoError as err:
                if profile is None or nonce is None:
                    raise CryptoError(
                        "Raw ciphertext bytes require explicit profile and nonce arguments.",
                        code="MISSING_ARGUMENTS",
                    ) from err
                pkg = EncryptedPackage(
                    profile=profile,
                    ciphertext=package,
                    nonce=nonce,
                    plaintext_checksum_sha256=expected_checksum or "",
                    associated_data=associated_data,
                )
        else:
            pkg = package

        active_profile = profile or pkg.profile
        active_ad = associated_data if associated_data is not None else pkg.associated_data
        active_nonce = nonce if nonce is not None else pkg.nonce
        active_expected_checksum = expected_checksum or pkg.plaintext_checksum_sha256

        if active_profile == SecurityProfile.QKD_AES_256_GCM:
            key_bytes, _ = self._resolve_key_bytes(key, required_len=32)
            try:
                aesgcm = AESGCM(key_bytes)
                plaintext = aesgcm.decrypt(active_nonce, pkg.ciphertext, active_ad)
            except InvalidTag as e:
                raise DecryptionError(
                    "AES-256-GCM authentication tag verification failed. Ciphertext has been tampered with or key is invalid.",
                    code="TAG_VERIFICATION_FAILED",
                ) from e
            except Exception as e:
                raise DecryptionError(f"Decryption error: {e}", code="DECRYPTION_FAILED") from e

        elif active_profile == SecurityProfile.QKD_OTP_SIM:
            if isinstance(key, bytes) and len(key) == len(pkg.ciphertext):
                keystream = key
            else:
                key_seed, _ = self._resolve_key_bytes(key, required_len=32)
                keystream = generate_otp_keystream(key_seed, len(pkg.ciphertext))

            plaintext = bytes(c ^ k for c, k in zip(pkg.ciphertext, keystream, strict=True))

        else:
            raise CryptoError(f"Unsupported profile: {active_profile}", code="UNSUPPORTED_PROFILE")

        # Verify integrity against expected SHA-256 checksum if provided
        if active_expected_checksum:
            IntegrityVerifier.verify_checksum(
                plaintext,
                active_expected_checksum,
                raise_on_error=True,
            )

        return plaintext

    def verify_integrity(self, plaintext: bytes, expected_checksum: str) -> bool:
        """Verifies plaintext against expected SHA-256 checksum."""
        return IntegrityVerifier.verify_checksum(plaintext, expected_checksum, raise_on_error=False)


__all__ = [
    "CryptoAdapter",
    "EncryptedPackage",
    "derive_key_from_handle",
    "generate_otp_keystream",
]
