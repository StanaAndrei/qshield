"""Unit tests for pluggable CryptoAdapter (TASK-5.4)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.core.enums import SecurityProfile
from app.core.exceptions import (
    CryptoError,
    DecryptionError,
    IntegrityVerificationError,
)
from app.models.scheduling import KeyHandle, TransferExecutionPlan
from app.transfer.crypto_adapter import (
    CryptoAdapter,
    EncryptedPackage,
    derive_key_from_handle,
    generate_otp_keystream,
)
from app.transfer.executor import SyntheticTransferExecutor
from app.transfer.integrity import IntegrityVerifier


@pytest.fixture
def crypto_adapter() -> CryptoAdapter:
    return CryptoAdapter()


@pytest.fixture
def sample_key_handle() -> KeyHandle:
    return KeyHandle(
        key_id=f"qkd-key-{uuid4()}",
        key_length_bits=256,
        source_id="univ-lab",
        destination_id="poly-clinic",
    )


class TestKeyDerivationAndKeystream:
    """Tests for deterministic key derivation from opaque KeyHandle and OTP keystream."""

    def test_derive_key_from_handle_deterministic(self, sample_key_handle: KeyHandle) -> None:
        key_a = derive_key_from_handle(sample_key_handle, key_size_bytes=32)
        key_b = derive_key_from_handle(sample_key_handle, key_size_bytes=32)
        assert len(key_a) == 32
        assert key_a == key_b

        # Different key_id produces different key bytes
        other_handle = KeyHandle(
            key_id="qkd-key-different-id",
            key_length_bits=256,
            source_id=sample_key_handle.source_id,
            destination_id=sample_key_handle.destination_id,
        )
        key_other = derive_key_from_handle(other_handle, key_size_bytes=32)
        assert key_a != key_other

    def test_generate_otp_keystream_exact_length_and_reproducible(self) -> None:
        seed = b"test-seed-material-for-keystream"
        stream1 = generate_otp_keystream(seed, 1024)
        stream2 = generate_otp_keystream(seed, 1024)
        assert len(stream1) == 1024
        assert stream1 == stream2

        # Empty length
        assert generate_otp_keystream(seed, 0) == b""


class TestAES256GCMCryptoAdapter:
    """Tests for AES-256-GCM encryption, decryption, and SHA-256 integrity verification."""

    def test_roundtrip_encrypt_decrypt_with_key_handle(
        self, crypto_adapter: CryptoAdapter, sample_key_handle: KeyHandle
    ) -> None:
        plaintext = b"CRITICAL_HEALTH_DIAGNOSTIC_DATA_PATIENT_10928374"
        expected_hash = IntegrityVerifier.calculate_checksum(plaintext)

        # 1. Encrypt
        pkg = crypto_adapter.encrypt(
            plaintext=plaintext,
            key=sample_key_handle,
            profile=SecurityProfile.QKD_AES_256_GCM,
        )
        assert pkg.profile == SecurityProfile.QKD_AES_256_GCM
        assert pkg.plaintext_checksum_sha256 == expected_hash
        assert len(pkg.nonce) == 12
        assert pkg.ciphertext != plaintext
        assert pkg.key_id == sample_key_handle.key_id

        # 2. Decrypt
        decrypted = crypto_adapter.decrypt(
            package=pkg,
            key=sample_key_handle,
            expected_checksum=expected_hash,
        )
        assert decrypted == plaintext
        assert IntegrityVerifier.calculate_checksum(decrypted) == expected_hash

    def test_roundtrip_with_raw_bytes_key(self, crypto_adapter: CryptoAdapter) -> None:
        raw_key = os.urandom(32)
        plaintext = b"RESEARCH_DATASET_DNA_SEQUENCE_ATCGCGATCGATCGATCG"
        pkg = crypto_adapter.encrypt(
            plaintext=plaintext,
            key=raw_key,
            profile=SecurityProfile.QKD_AES_256_GCM,
        )
        decrypted = crypto_adapter.decrypt(package=pkg, key=raw_key)
        assert decrypted == plaintext

    def test_associated_data_authentication(
        self, crypto_adapter: CryptoAdapter, sample_key_handle: KeyHandle
    ) -> None:
        plaintext = b"AUTHENTICATED_ASSOCIATED_DATA_PAYLOAD"
        ad = b"metadata-auth-token-12345"

        pkg = crypto_adapter.encrypt(
            plaintext=plaintext,
            key=sample_key_handle,
            profile=SecurityProfile.QKD_AES_256_GCM,
            associated_data=ad,
        )

        # Decrypt with matching AD succeeds
        recovered = crypto_adapter.decrypt(package=pkg, key=sample_key_handle, associated_data=ad)
        assert recovered == plaintext

        # Decrypt with tampered AD fails tag authentication
        with pytest.raises(DecryptionError) as exc_info:
            crypto_adapter.decrypt(package=pkg, key=sample_key_handle, associated_data=b"tampered-ad")
        assert exc_info.value.code == "TAG_VERIFICATION_FAILED"

    def test_ciphertext_tampering_raises_decryption_error(
        self, crypto_adapter: CryptoAdapter, sample_key_handle: KeyHandle
    ) -> None:
        plaintext = b"HIGH_PRIORITY_NEUROLOGICAL_SCAN_DATA"
        pkg = crypto_adapter.encrypt(
            plaintext=plaintext,
            key=sample_key_handle,
            profile=SecurityProfile.QKD_AES_256_GCM,
        )

        # Tamper with 1 byte of the ciphertext
        tampered_bytes = bytearray(pkg.ciphertext)
        tampered_bytes[0] ^= 0xFF
        pkg.ciphertext = bytes(tampered_bytes)

        with pytest.raises(DecryptionError) as exc_info:
            crypto_adapter.decrypt(package=pkg, key=sample_key_handle)
        assert exc_info.value.code == "TAG_VERIFICATION_FAILED"

    def test_checksum_mismatch_raises_integrity_error(
        self, crypto_adapter: CryptoAdapter, sample_key_handle: KeyHandle
    ) -> None:
        plaintext = b"TEST_PAYLOAD"
        pkg = crypto_adapter.encrypt(
            plaintext=plaintext,
            key=sample_key_handle,
            profile=SecurityProfile.QKD_AES_256_GCM,
        )

        with pytest.raises(IntegrityVerificationError):
            crypto_adapter.decrypt(
                package=pkg,
                key=sample_key_handle,
                expected_checksum="0000000000000000000000000000000000000000000000000000000000000000",
            )


class TestOTPSIMCryptoAdapter:
    """Tests for simulated One-Time Pad (QKD_OTP_SIM) encryption, decryption, and SHA-256 verification."""

    def test_otp_roundtrip_with_key_handle(
        self, crypto_adapter: CryptoAdapter, sample_key_handle: KeyHandle
    ) -> None:
        plaintext = b"INFORMATION_THEORETIC_SECURE_PAYLOAD_TRANSFERRED_OVER_QKD"
        expected_hash = IntegrityVerifier.calculate_checksum(plaintext)

        pkg = crypto_adapter.encrypt(
            plaintext=plaintext,
            key=sample_key_handle,
            profile=SecurityProfile.QKD_OTP_SIM,
        )
        assert pkg.profile == SecurityProfile.QKD_OTP_SIM
        assert pkg.plaintext_checksum_sha256 == expected_hash
        assert pkg.ciphertext != plaintext
        assert len(pkg.ciphertext) == len(plaintext)

        # Decrypt
        decrypted = crypto_adapter.decrypt(
            package=pkg,
            key=sample_key_handle,
            expected_checksum=expected_hash,
        )
        assert decrypted == plaintext
        assert IntegrityVerifier.calculate_checksum(decrypted) == expected_hash

    def test_otp_roundtrip_with_exact_raw_pad(self, crypto_adapter: CryptoAdapter) -> None:
        plaintext = b"EXACT_ONE_TIME_PAD_BUFFER"
        exact_pad = os.urandom(len(plaintext))

        pkg = crypto_adapter.encrypt(
            plaintext=plaintext,
            key=exact_pad,
            profile=SecurityProfile.QKD_OTP_SIM,
        )
        assert len(pkg.ciphertext) == len(plaintext)

        decrypted = crypto_adapter.decrypt(package=pkg, key=exact_pad)
        assert decrypted == plaintext

    def test_otp_tampering_fails_sha256_integrity(
        self, crypto_adapter: CryptoAdapter, sample_key_handle: KeyHandle
    ) -> None:
        plaintext = b"OTP_TRANSMISSION_INTEGRITY_TEST"
        pkg = crypto_adapter.encrypt(
            plaintext=plaintext,
            key=sample_key_handle,
            profile=SecurityProfile.QKD_OTP_SIM,
        )

        # Corrupt 1 byte of the ciphertext
        corrupted = bytearray(pkg.ciphertext)
        corrupted[4] ^= 0x01
        pkg.ciphertext = bytes(corrupted)

        with pytest.raises(IntegrityVerificationError):
            crypto_adapter.decrypt(package=pkg, key=sample_key_handle)


class TestEncryptedPackageSerialization:
    """Tests for serialization and deserialization of EncryptedPackage envelopes."""

    def test_serialize_deserialize_package(
        self, crypto_adapter: CryptoAdapter, sample_key_handle: KeyHandle
    ) -> None:
        plaintext = b"SERIALIZED_PACKAGE_BINARY_DATA"
        pkg = crypto_adapter.encrypt(
            plaintext=plaintext,
            key=sample_key_handle,
            profile=SecurityProfile.QKD_AES_256_GCM,
            associated_data=b"test-ad",
        )

        binary_envelope = pkg.serialize()
        assert isinstance(binary_envelope, bytes)
        assert len(binary_envelope) > len(pkg.ciphertext)

        restored_pkg = EncryptedPackage.deserialize(binary_envelope)
        assert restored_pkg.profile == pkg.profile
        assert restored_pkg.nonce == pkg.nonce
        assert restored_pkg.plaintext_checksum_sha256 == pkg.plaintext_checksum_sha256
        assert restored_pkg.ciphertext == pkg.ciphertext
        assert restored_pkg.associated_data == pkg.associated_data

        decrypted = crypto_adapter.decrypt(package=binary_envelope, key=sample_key_handle)
        assert decrypted == plaintext

    def test_deserialize_corrupted_data_raises_crypto_error(self) -> None:
        with pytest.raises(CryptoError) as exc_info:
            EncryptedPackage.deserialize(b"abc")
        assert exc_info.value.code == "INVALID_PACKAGE"


class TestSyntheticTransferExecutorWithCrypto:
    """Tests integration between SyntheticTransferExecutor and CryptoAdapter when key_handle is supplied."""

    def test_executor_with_key_handle_executes_encrypted_transfer(
        self, sample_key_handle: KeyHandle
    ) -> None:
        executor = SyntheticTransferExecutor()
        plan = TransferExecutionPlan(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=10000,
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            key_handle=sample_key_handle,
            allocated_bandwidth_mbps=500.0,
            simulate_failures=False,
        )

        result = executor.execute(plan)
        assert result.success is True
        assert result.verification_success is True
        assert result.bytes_transferred > 0
        assert result.payload_checksum_sha256 is not None
        assert result.error_message is None

    def test_executor_with_key_handle_and_corruption_fails_safely(
        self, sample_key_handle: KeyHandle
    ) -> None:
        executor = SyntheticTransferExecutor()
        plan = TransferExecutionPlan(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=5000,
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            key_handle=sample_key_handle,
            allocated_bandwidth_mbps=500.0,
            simulate_failures=True,  # Corrupts bits during simulated transit
        )

        result = executor.execute(plan)
        assert result.success is False
        assert result.verification_success is False
        assert result.error_message is not None
