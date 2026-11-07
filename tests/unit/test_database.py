"""Unit tests for SQLite schema creation and table definitions (TASK-1.2)."""

from __future__ import annotations

import warnings

import pytest
from sqlalchemy import inspect

from app.database.connection import get_engine, init_db, reset_engine


def test_sqlite_schema_creation_in_memory() -> None:
    """Verifies that SQLite schema creates all required tables in memory without warnings."""
    reset_engine()
    engine = get_engine("sqlite:///:memory:", is_testing=True)

    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")
        init_db(engine)

        # Verify zero warnings were triggered during DDL execution
        assert len(recorded_warnings) == 0, f"Warnings encountered: {[str(w.message) for w in recorded_warnings]}"

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    required_tables = {
        "transfer_requests",
        "state_transitions",
        "policy_decisions",
        "scheduling_decisions",
        "audit_events",
    }

    assert required_tables.issubset(table_names), f"Missing tables: {required_tables - table_names}"

    # Verify column definitions on transfer_requests
    columns = {col["name"]: col for col in inspector.get_columns("transfer_requests")}
    assert "request_id" in columns
    assert "source_id" in columns
    assert "destination_id" in columns
    assert "data_size_bytes" in columns
    assert "priority" in columns
    assert "status" in columns
    assert "submitted_at" in columns
    assert "payload_checksum_sha256" in columns

    # Verify foreign key on state_transitions
    fks = inspector.get_foreign_keys("state_transitions")
    assert len(fks) == 1
    assert fks[0]["referred_table"] == "transfer_requests"
    assert fks[0]["referred_columns"] == ["request_id"]

    engine.dispose()
    reset_engine()


def test_sqlite_schema_creation_file(tmp_path: pytest.TempPathFactory) -> None:
    """Verifies that SQLite schema creates all tables in a file-backed database without warnings."""
    db_file = tmp_path / "test_qshield.db"  # type: ignore[operator]
    engine = get_engine(f"sqlite:///{db_file}")

    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")
        init_db(engine)
        assert len(recorded_warnings) == 0

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert "transfer_requests" in table_names
    assert "state_transitions" in table_names
    assert "policy_decisions" in table_names

    engine.dispose()
    reset_engine()
