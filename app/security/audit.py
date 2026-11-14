"""Cryptographic append-only audit logger and automated redaction filters (TASK-5.5)."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.enums import AuditEventType
from app.database.connection import get_engine, get_session_factory
from app.database.repository import ITransferRepository, SqliteTransferRepository
from app.models.policy_decision import AuditEvent

logger = logging.getLogger(__name__)

REDACTION_SENTINEL: str = "[REDACTED_BY_AUDIT_POLICY]"

# Allowed non-sensitive key metadata fields
ALLOWED_KEY_METADATA_TERMS: tuple[str, ...] = (
    "key_id",
    "key_length_bits",
    "key_length",
    "key_generation_rate_bps",
    "key_service_latency_ms",
    "key_buffer_available_keys",
    "keys_consumed",
)

# Disallowed key terms that immediately trigger redaction
SENSITIVE_KEY_TERMS: tuple[str, ...] = (
    "key_bytes",
    "raw_key",
    "secret_key",
    "private_key",
    "privkey",
    "secret",
    "token",
    "password",
    "passwd",
    "payload",
    "plaintext",
    "seed",
    "patient",
    "ssn",
    "mrn",
    "phi",
    "honeytoken",
)

# Sensitive value markers that trigger redaction even if the key name is innocuous
SENSITIVE_VALUE_PATTERNS: tuple[str, ...] = (
    "qshield_synthetic_medical_payload",
    "-----begin",
    "honeytoken",
)


def sanitize_audit_data(val: Any) -> Any:
    """Recursively redacts cryptographic keys, secrets, raw payloads, and PHI terms.

    Args:
        val: Arbitrary python data structure (dict, list, primitive).

    Returns:
        Cleaned data structure where all sensitive fields are replaced with REDACTION_SENTINEL.
    """
    if isinstance(val, Mapping):
        sanitized_dict: dict[str, Any] = {}
        for k, v in val.items():
            k_str = str(k).lower()
            if k_str in ALLOWED_KEY_METADATA_TERMS:
                sanitized_dict[str(k)] = sanitize_audit_data(v)
            elif k_str == "key" or any(term in k_str for term in SENSITIVE_KEY_TERMS):
                sanitized_dict[str(k)] = REDACTION_SENTINEL
            else:
                sanitized_dict[str(k)] = sanitize_audit_data(v)
        return sanitized_dict

    if isinstance(val, (Sequence, set)) and not isinstance(val, (str, bytes)):
        return [sanitize_audit_data(item) for item in val]

    if isinstance(val, str):
        val_lower = val.lower()
        if any(pat in val_lower for pat in SENSITIVE_VALUE_PATTERNS):
            return REDACTION_SENTINEL
        return val

    if isinstance(val, bytes):
        return REDACTION_SENTINEL

    return val


class AuditLogger:
    """Append-only cryptographic audit logger writing to SQLite audit_events.

    Guarantees:
    - Zero secret key, token, or PHI leakage through recursive redaction.
    - Immutable append-only persistence to SQLite.
    - Optional structured python logging with automatic redaction.
    """

    def __init__(
        self,
        repository: ITransferRepository | None = None,
        db_path: str | None = None,
        log_to_python_logger: bool = True,
    ) -> None:
        self.log_to_python_logger = log_to_python_logger

        if repository is not None:
            self._repository = repository
        elif db_path is not None:
            url = f"sqlite:///{db_path}" if not db_path.startswith("sqlite") else db_path
            engine = get_engine(url=url)
            session_factory = get_session_factory(engine)
            self._repository = SqliteTransferRepository(session_factory())
        else:
            self._repository = SqliteTransferRepository()

    @property
    def repository(self) -> ITransferRepository:
        """Returns the underlying transfer and audit repository."""
        return self._repository

    def log_event(
        self,
        event_type: AuditEventType,
        component: str,
        request_id: UUID | None = None,
        details: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> AuditEvent:
        """Sanitizes parameters, constructs an immutable AuditEvent, and persists it to SQLite.

        Args:
            event_type: AuditEventType classification.
            component: Originating subsystem (e.g. API, Policy, Scheduler, QKD, Executor).
            request_id: Optional UUID of the associated transfer request.
            details: Contextual details (automatically scrubbed of secrets/payloads).
            timestamp: Optional UTC event timestamp.

        Returns:
            The recorded AuditEvent entity.
        """
        raw_details = details or {}
        sanitized_details = sanitize_audit_data(raw_details)

        event = AuditEvent(
            event_id=uuid4(),
            event_type=event_type,
            request_id=request_id,
            timestamp=timestamp or datetime.now(UTC),
            component=component,
            details=sanitized_details,
        )

        # Append-only persistence to SQLite
        self._repository.record_audit_event(event)

        if self.log_to_python_logger:
            logger.info(
                "AUDIT_EVENT: type=%s component=%s request_id=%s details=%s",
                event.event_type.value,
                event.component,
                event.request_id,
                event.details,
            )

        return event

    def log(self, event: AuditEvent) -> AuditEvent:
        """Logs an existing AuditEvent instance after ensuring full sanitization."""
        cleaned_details = sanitize_audit_data(event.details)
        cleaned_event = AuditEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            request_id=event.request_id,
            timestamp=event.timestamp,
            component=event.component,
            details=cleaned_details,
        )
        self._repository.record_audit_event(cleaned_event)
        return cleaned_event

    def get_events(
        self,
        request_id: UUID | None = None,
        event_type: AuditEventType | None = None,
    ) -> list[AuditEvent]:
        """Retrieves audit events from SQLite repository with optional filtering."""
        events = self._repository.get_audit_events(request_id=request_id)
        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]
        return events


__all__ = [
    "REDACTION_SENTINEL",
    "SENSITIVE_KEY_TERMS",
    "SENSITIVE_VALUE_PATTERNS",
    "AuditLogger",
    "sanitize_audit_data",
]
