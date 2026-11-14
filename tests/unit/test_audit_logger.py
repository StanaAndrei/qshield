"""Unit tests for Cryptographic AuditLogger, redaction filters, and compliance scanner (Milestone 5)."""

from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import AuditEventType
from app.database.repository import SqliteTransferRepository
from app.database.schema import Base
from app.models.policy_decision import AuditEvent
from app.security.audit import (
    REDACTION_SENTINEL,
    AuditLogger,
    sanitize_audit_data,
)


def _load_scanner_module() -> Any:
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


@pytest.fixture
def in_memory_repo() -> SqliteTransferRepository:
    """Creates an in-memory SQLite repository with initialized schema."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    return SqliteTransferRepository(session_factory())


class TestAuditDataSanitization:
    """Tests for recursive redaction of sensitive parameters in audit logging (TASK-5.5)."""

    def test_dod_key_bytes_redacted(self) -> None:
        """DoD: Audit event containing 'key_bytes': 'deadbeef' is saved as '[REDACTED_BY_AUDIT_POLICY]'."""
        raw = {"key_bytes": "deadbeef", "source_id": "univ-lab"}
        sanitized = sanitize_audit_data(raw)
        assert sanitized["key_bytes"] == REDACTION_SENTINEL
        assert sanitized["source_id"] == "univ-lab"

    def test_nested_dictionary_redaction(self) -> None:
        raw = {
            "metadata": {
                "user": "dr_smith",
                "secret_key": "supersecretvalue",
                "auth": {
                    "token": "bearer-token-12345",
                    "nested_payload": "raw_sensitive_bytes",
                },
            },
            "status": "APPROVED",
        }
        sanitized = sanitize_audit_data(raw)
        assert sanitized["metadata"]["user"] == "dr_smith"
        assert sanitized["metadata"]["secret_key"] == REDACTION_SENTINEL
        assert sanitized["metadata"]["auth"]["token"] == REDACTION_SENTINEL
        assert sanitized["metadata"]["auth"]["nested_payload"] == REDACTION_SENTINEL
        assert sanitized["status"] == "APPROVED"

    def test_list_and_value_pattern_redaction(self) -> None:
        raw = [
            {"password": "mypassword"},
            "QSHIELD_SYNTHETIC_MEDICAL_PAYLOAD_SEED_42_SAFE_DATA",
            "honeytoken_user_leak_probe",
            "safe_string_identifier",
        ]
        sanitized = sanitize_audit_data(raw)
        assert sanitized[0]["password"] == REDACTION_SENTINEL
        assert sanitized[1] == REDACTION_SENTINEL
        assert sanitized[2] == REDACTION_SENTINEL
        assert sanitized[3] == "safe_string_identifier"

    def test_bytes_automatically_redacted(self) -> None:
        raw = {"raw_blob": b"\xde\xad\xbe\xef"}
        sanitized = sanitize_audit_data(raw)
        assert sanitized["raw_blob"] == REDACTION_SENTINEL


class TestAuditLogger:
    """Tests for append-only AuditLogger writing to SQLite audit_events (TASK-5.5)."""

    def test_log_event_redacts_and_persists_to_sqlite(
        self, in_memory_repo: SqliteTransferRepository
    ) -> None:
        logger = AuditLogger(repository=in_memory_repo)
        req_id = uuid4()

        event = logger.log_event(
            event_type=AuditEventType.KEY_REQUESTED,
            component="QKD",
            request_id=req_id,
            details={
                "key_bytes": "deadbeef",
                "key_length_bits": 256,
                "secret": "top-secret-token",
            },
        )

        assert event.details["key_bytes"] == REDACTION_SENTINEL
        assert event.details["secret"] == REDACTION_SENTINEL
        assert event.details["key_length_bits"] == 256

        # Query back from SQLite database
        persisted_events = logger.get_events(request_id=req_id)
        assert len(persisted_events) == 1
        persisted = persisted_events[0]
        assert persisted.event_id == event.event_id
        assert persisted.event_type == AuditEventType.KEY_REQUESTED
        assert persisted.component == "QKD"
        assert persisted.details["key_bytes"] == REDACTION_SENTINEL
        assert persisted.details["secret"] == REDACTION_SENTINEL
        assert persisted.details["key_length_bits"] == 256

    def test_log_direct_event_model(self, in_memory_repo: SqliteTransferRepository) -> None:
        logger = AuditLogger(repository=in_memory_repo)
        req_id = uuid4()
        evt = AuditEvent(
            event_type=AuditEventType.TRANSFER_ATTEMPT,
            component="Executor",
            request_id=req_id,
            details={"plaintext": "patient_mri_scan_slice"},
        )
        saved = logger.log(evt)
        assert saved.details["plaintext"] == REDACTION_SENTINEL

        stored = in_memory_repo.get_audit_events(req_id)
        assert len(stored) == 1
        assert stored[0].details["plaintext"] == REDACTION_SENTINEL


class TestComplianceScanner:
    """Tests for automated security compliance and zero-leakage scanner (TASK-5.7)."""

    def test_scan_text_clean_content(self) -> None:
        scanner = _load_scanner_module()
        clean = "Normal log line with request_id=123 and status=COMPLETED and details={'key_bytes': '[REDACTED_BY_AUDIT_POLICY]'}"
        findings = scanner.scan_text(clean, source_type="logfile", location="test:1")
        assert len(findings) == 0

    def test_scan_text_detects_unredacted_key_bytes(self) -> None:
        scanner = _load_scanner_module()
        leaked = 'Leaked key_bytes: "deadbeef" in audit parameter'
        findings = scanner.scan_text(leaked, source_type="logfile", location="test:2")
        assert len(findings) > 0
        assert any(f.rule_name == "deadbeef_key" for f in findings)

    def test_scan_text_detects_unredacted_payload_marker(self) -> None:
        scanner = _load_scanner_module()
        leaked = "Payload snippet: QSHIELD_SYNTHETIC_MEDICAL_PAYLOAD_SEED_42_SAFE_DATA"
        findings = scanner.scan_text(leaked, source_type="logfile", location="test:3")
        assert len(findings) > 0
        assert any(f.rule_name == "unredacted_synthetic_payload" for f in findings)

    def test_run_compliance_audit_clean_database_and_logs(self, tmp_path: Path) -> None:
        scanner = _load_scanner_module()
        db_file = tmp_path / "test_clean.db"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Create a database with clean redacted audit events
        engine = create_engine(f"sqlite:///{db_file}")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
        repo = SqliteTransferRepository(session_factory())
        logger = AuditLogger(repository=repo)

        # Log event with sensitive parameter that gets safely redacted
        logger.log_event(
            event_type=AuditEventType.KEY_REQUESTED,
            component="QKD",
            details={"key_bytes": "deadbeef", "destination": "poly-clinic"},
        )

        # Write clean log file
        log_file = log_dir / "audit.log"
        log_file.write_text("INFO: AUDIT_EVENT: type=KEY_REQUESTED details={'key_bytes': '[REDACTED_BY_AUDIT_POLICY]'}\n")

        # Run compliance audit
        exit_code, findings = scanner.run_compliance_audit(db_path=db_file, log_dir=log_dir, strict=True)
        assert exit_code == 0
        assert len(findings) == 0

    def test_run_compliance_audit_detects_database_leak(self, tmp_path: Path) -> None:
        scanner = _load_scanner_module()
        db_file = tmp_path / "test_leak.db"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Artificially insert a raw unredacted leak into an SQLite table
        conn = sqlite3.connect(str(db_file))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE leak_table (id INT, secret_field TEXT);")
        cursor.execute("INSERT INTO leak_table VALUES (1, 'key_bytes: deadbeef');")
        conn.commit()
        conn.close()

        exit_code, findings = scanner.run_compliance_audit(db_path=db_file, log_dir=log_dir, strict=True)
        assert exit_code == 1
        assert len(findings) > 0
        assert any(f.rule_name == "deadbeef_key" for f in findings)

    def test_cli_execution_clean_database(self, tmp_path: Path) -> None:
        db_file = tmp_path / "cli_clean.db"
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        # Set up clean database
        engine = create_engine(f"sqlite:///{db_file}")
        Base.metadata.create_all(engine)

        script_path = Path(__file__).parent.parent.parent / "scripts" / "audit_security_compliance.py"
        cmd = [
            sys.executable,
            str(script_path),
            "--db-path",
            str(db_file),
            "--log-dir",
            str(log_dir),
            "--strict",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0
        assert "AUDIT CLEAN: Zero secrets or PHI detected" in result.stdout
