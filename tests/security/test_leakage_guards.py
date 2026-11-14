"""Zero-leakage and honeytoken security guard tests (TASK-5.6)."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.enums import (
    AuditEventType,
    DataCategory,
    PriorityLevel,
    SecurityProfile,
    TransferStatus,
)
from app.database.repository import SqliteTransferRepository
from app.database.schema import Base
from app.models.scheduling import TransferExecutionPlan
from app.models.transfer_request import TransferRequest
from app.qkd.simulator import QKDSimulatorAdapter
from app.security.audit import REDACTION_SENTINEL, AuditLogger
from app.transfer.crypto_adapter import CryptoAdapter
from app.transfer.executor import SyntheticTransferExecutor


def _load_compliance_scanner() -> Any:
    mod_name = "scripts.audit_security_compliance"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    scanner_path = Path(__file__).parent.parent.parent / "scripts" / "audit_security_compliance.py"
    spec = importlib.util.spec_from_file_location(mod_name, scanner_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load scanner from {scanner_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestKeyHandleOpacity:
    """Tests guaranteeing that KeyHandle never exposes secret key material."""

    def test_key_handle_contains_zero_secret_attributes(self) -> None:
        sim = QKDSimulatorAdapter()
        handle = sim.acquire_key_handle("univ-lab", "poly-clinic", key_size_bits=256)

        # Attribute inspections
        assert not hasattr(handle, "key_bytes")
        assert not hasattr(handle, "raw_key")
        assert not hasattr(handle, "secret")
        assert not hasattr(handle, "key_material")

        # Serialization inspection
        data = handle.model_dump()
        disallowed_fields = {"key_bytes", "raw_key", "secret", "password"}
        for field in disallowed_fields:
            assert field not in data

        # Check key_id format is opaque UUID reference
        assert data["key_id"].startswith("qkd-key-")


class TestPhiMetadataGuards:
    """Tests ensuring transfer requests reject PHI or patient identifiers in metadata."""

    @pytest.mark.parametrize(
        "phi_key, phi_val",
        [
            ("patient_name", "Jane Doe"),
            ("patient_ssn", "000-12-3456"),
            ("mrn", "MRN99887766"),
            ("patient_dob", "1980-01-01"),
            ("phi_notes", "Sensitive health diagnosis"),
        ],
    )
    def test_phi_in_metadata_raises_validation_error(self, phi_key: str, phi_val: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            TransferRequest(
                request_id=uuid4(),
                source_id="univ-lab",
                destination_id="poly-clinic",
                data_size_bytes=1000,
                priority=PriorityLevel.NORMAL,
                security_profile=SecurityProfile.QKD_AES_256_GCM,
                data_category=DataCategory.DIAGNOSTIC_REPORT,
                status=TransferStatus.SUBMITTED,
                metadata={phi_key: phi_val},
            )
        assert "PHI" in str(exc_info.value) or "metadata" in str(exc_info.value)


class TestHoneytokenAndLeakageGuards:
    """Tests injecting honeytokens and confirming zero leakage across logs and database tables."""

    def test_honeytoken_scrubbed_from_audit_logger(self, tmp_path: Path) -> None:
        db_file = tmp_path / "qshield_honeytoken_test.db"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        engine = create_engine(f"sqlite:///{db_file}")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
        repo = SqliteTransferRepository(session_factory())
        logger = AuditLogger(repository=repo)

        # Honeytokens representing high-value leaked secrets
        honeytoken_key = "honeytoken_deadbeef_secret_key_material_9999"
        honeytoken_patient = "honeytoken_patient_ssn_secret_phi_8888"

        # Log audit event containing honeytokens
        event = logger.log_event(
            event_type=AuditEventType.KEY_REQUESTED,
            component="QKD",
            details={
                "key_bytes": honeytoken_key,
                "user_note": honeytoken_patient,
                "nested": {
                    "token": "honeytoken_bearer_1234",
                    "safe_metadata": "allowed_field_value",
                },
            },
        )

        # Assert in-memory event is scrubbed
        assert event.details["key_bytes"] == REDACTION_SENTINEL
        assert event.details["user_note"] == REDACTION_SENTINEL
        assert event.details["nested"]["token"] == REDACTION_SENTINEL
        assert event.details["nested"]["safe_metadata"] == "allowed_field_value"

        # Assert persisted SQLite record is scrubbed
        stored_events = repo.get_audit_events()
        assert len(stored_events) == 1
        stored = stored_events[0]
        assert stored.details["key_bytes"] == REDACTION_SENTINEL
        assert stored.details["user_note"] == REDACTION_SENTINEL
        assert stored.details["nested"]["token"] == REDACTION_SENTINEL

        # Run automated compliance scanner against the database
        scanner = _load_compliance_scanner()
        exit_code, findings = scanner.run_compliance_audit(
            db_path=db_file,
            log_dir=log_dir,
            strict=True,
        )
        assert exit_code == 0
        assert len(findings) == 0

    def test_zero_raw_key_leakage_in_execution_and_logging(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)

        # Execute full cryptographic transfer with QKD and CryptoAdapter
        sim = QKDSimulatorAdapter()
        crypto = CryptoAdapter()
        executor = SyntheticTransferExecutor(crypto_adapter=crypto)

        handle = sim.acquire_key_handle("univ-lab", "poly-clinic")
        plan = TransferExecutionPlan(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=5000,
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            key_handle=handle,
            allocated_bandwidth_mbps=200.0,
            simulate_failures=False,
        )

        result = executor.execute(plan)
        assert result.success is True
        assert result.verification_success is True

        # Check all logged records for raw key bytes or secrets
        all_logs = " ".join([record.getMessage() for record in caplog.records])
        scanner = _load_compliance_scanner()
        findings = scanner.scan_text(all_logs, source_type="logfile", location="caplog")
        assert len(findings) == 0
