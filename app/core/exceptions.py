"""Domain exceptions and error models for Q-SHIELD."""

from __future__ import annotations


class QShieldError(Exception):
    """Base exception for all domain-specific errors in Q-SHIELD."""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class PolicyViolationError(QShieldError):
    """Raised when a transfer request fails deterministic policy validation."""

    def __init__(self, message: str, code: str = "POLICY_VIOLATION") -> None:
        super().__init__(message, code=code)


class InvalidStateTransitionError(QShieldError):
    """Raised when an illegal lifecycle transition is attempted on a transfer."""

    def __init__(self, message: str, code: str = "INVALID_STATE_TRANSITION") -> None:
        super().__init__(message, code=code)


class TransferNotFoundError(QShieldError):
    """Raised when a requested transfer entity is not found."""

    def __init__(self, message: str, code: str = "TRANSFER_NOT_FOUND") -> None:
        super().__init__(message, code=code)


class IntegrityVerificationError(QShieldError):
    """Raised when cryptographic SHA-256 payload integrity check fails."""

    def __init__(
        self,
        message: str = "Payload integrity verification failed (SHA-256 mismatch)",
        code: str = "INTEGRITY_VERIFICATION_FAILED",
    ) -> None:
        super().__init__(message, code=code)


class SchedulingError(QShieldError):
    """Raised when scheduler encountered an illegal state or unresolvable queue."""

    def __init__(self, message: str, code: str = "SCHEDULING_ERROR") -> None:
        super().__init__(message, code=code)


class QKDError(QShieldError):
    """Base exception for QKD adapter failures."""

    def __init__(self, message: str, code: str = "QKD_ERROR") -> None:
        super().__init__(message, code=code)


class QKDNotConfiguredError(QKDError):
    """Raised when physical QKD hardware integration is invoked without credentials."""

    def __init__(
        self,
        message: str = "Real QKD adapter is explicitly unconfigured. Use simulation mode.",
        code: str = "QKD_NOT_CONFIGURED",
    ) -> None:
        super().__init__(message, code=code)


# Alias for spec section 7.2 compliance
QKDHardwareNotConfiguredError = QKDNotConfiguredError


class QKDKeyBufferDepletedError(QKDError):
    """Raised when key buffer is depleted and cannot fulfill key acquisition."""

    def __init__(
        self,
        message: str = "QKD key buffer depleted; resource wait required.",
        code: str = "KEY_BUFFER_DEPLETED",
    ) -> None:
        super().__init__(message, code=code)


class CryptoError(QShieldError):
    """Base exception for cryptographic operations."""

    def __init__(self, message: str, code: str = "CRYPTO_ERROR") -> None:
        super().__init__(message, code=code)


class DecryptionError(CryptoError):
    """Raised when decryption or tag verification fails."""

    def __init__(
        self,
        message: str = "Decryption failed: invalid ciphertext, key, or tag authentication mismatch.",
        code: str = "DECRYPTION_FAILED",
    ) -> None:
        super().__init__(message, code=code)


__all__ = [
    "CryptoError",
    "DecryptionError",
    "IntegrityVerificationError",
    "InvalidStateTransitionError",
    "PolicyViolationError",
    "QKDError",
    "QKDHardwareNotConfiguredError",
    "QKDKeyBufferDepletedError",
    "QKDNotConfiguredError",
    "QShieldError",
    "SchedulingError",
    "TransferNotFoundError",
]
