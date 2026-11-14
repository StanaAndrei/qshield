#!/usr/bin/env python3
"""Automated security compliance and zero-leakage scanner (TASK-5.7).

Scans SQLite database tables and log files for:
- Private cryptographic key material or raw key bytes.
- Secret tokens, passwords, and authorization credentials.
- Unredacted synthetic medical payload markers or PHI.
- Active honeytoken markers.

Usage:
    python scripts/audit_security_compliance.py \
        --db-path ./qshield.db \
        --log-dir ./logs \
        --strict
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("audit_scanner")

# Patterns matching forbidden leaked sensitive data
SCAN_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key_pem": re.compile(
        r"-----BEGIN[ A-Z0-9_-]*PRIVATE KEY-----", re.IGNORECASE
    ),
    "unredacted_key_bytes": re.compile(
        r'["\']?key_bytes["\']?\s*[:=]\s*["\']?(?!\[REDACTED)[a-zA-Z0-9+/=]{8,}["\']?',
        re.IGNORECASE,
    ),
    "deadbeef_key": re.compile(
        r'["\']?key_bytes["\']?\s*[:=]\s*["\']?deadbeef["\']?', re.IGNORECASE
    ),
    "unredacted_secret": re.compile(
        r'["\']?(?:secret_key|private_key|auth_token|passwd|password)["\']?\s*[:=]\s*["\']?(?!\[REDACTED)[a-zA-Z0-9._\-/+=]{8,}["\']?',
        re.IGNORECASE,
    ),
    "unredacted_synthetic_payload": re.compile(
        r"QSHIELD_SYNTHETIC_MEDICAL_PAYLOAD_SEED_", re.IGNORECASE
    ),
    "unredacted_phi_patient_info": re.compile(
        r'["\']?(?:patient_ssn|patient_name|patient_dob|mrn)["\']?\s*[:=]\s*["\'](?!\[REDACTED)[a-zA-Z0-9\s,.-]{3,}["\']',
        re.IGNORECASE,
    ),
    "honeytoken_marker": re.compile(
        r"\bhoneytoken_[a-zA-Z0-9_]+\b", re.IGNORECASE
    ),
}


@dataclass
class ComplianceFinding:
    """Represents an identified security or leakage policy violation."""

    source_type: str  # "database" or "logfile"
    location: str  # table/column or file:line
    rule_name: str
    match_snippet: str


def scan_text(text: str, source_type: str, location: str) -> list[ComplianceFinding]:
    """Tests a single text block against all security compliance patterns."""
    findings: list[ComplianceFinding] = []
    for rule_name, pattern in SCAN_PATTERNS.items():
        match = pattern.search(text)
        if match:
            # Extract excerpt for context
            start = max(0, match.start() - 20)
            end = min(len(text), match.end() + 20)
            snippet = text[start:end].strip()
            findings.append(
                ComplianceFinding(
                    source_type=source_type,
                    location=location,
                    rule_name=rule_name,
                    match_snippet=snippet,
                )
            )
    return findings


def scan_database(db_path: Path) -> list[ComplianceFinding]:
    """Scans all tables and columns of a SQLite database for sensitive leaks."""
    findings: list[ComplianceFinding] = []
    if not db_path.exists():
        logger.info("Database file not found at %s (skipping DB scan)", db_path)
        return findings

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Enumerate user tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        tables = [row[0] for row in cursor.fetchall()]

        for table in tables:
            cursor.execute(f"PRAGMA table_info({table});")
            columns = [col[1] for col in cursor.fetchall()]

            cursor.execute(f"SELECT * FROM {table};")
            rows = cursor.fetchall()

            for row_idx, row in enumerate(rows):
                for col_idx, col_name in enumerate(columns):
                    val = row[col_idx]
                    if val is None:
                        continue
                    text_repr = str(val)
                    loc = f"table '{table}', row #{row_idx + 1}, column '{col_name}'"
                    cell_findings = scan_text(text_repr, source_type="database", location=loc)
                    findings.extend(cell_findings)

        conn.close()
    except Exception as e:
        logger.error("Failed to scan database %s: %s", db_path, e)
        findings.append(
            ComplianceFinding(
                source_type="database",
                location=str(db_path),
                rule_name="db_read_error",
                match_snippet=f"Database inspection error: {e}",
            )
        )

    return findings


def scan_log_directory(log_dir: Path) -> list[ComplianceFinding]:
    """Scans all log, txt, and jsonl files in the given directory for leaks."""
    findings: list[ComplianceFinding] = []
    if not log_dir.exists():
        logger.info("Log directory not found at %s (skipping logs scan)", log_dir)
        return findings

    candidate_files = [
        f for f in log_dir.glob("**/*")
        if f.is_file() and f.suffix in {".log", ".txt", ".jsonl", ".out"}
    ]

    for log_file in candidate_files:
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, start=1):
                    loc = f"{log_file.name}:{line_num}"
                    line_findings = scan_text(line, source_type="logfile", location=loc)
                    findings.extend(line_findings)
        except Exception as e:
            logger.error("Failed to read log file %s: %s", log_file, e)

    return findings


def run_compliance_audit(
    db_path: Path,
    log_dir: Path,
    strict: bool = False,
) -> tuple[int, list[ComplianceFinding]]:
    """Runs database and log scans and determines compliance status.

    Returns:
        Tuple of (exit_code, findings_list).
    """
    logger.info("Starting security compliance audit...")
    logger.info("Target DB: %s", db_path)
    logger.info("Target Logs: %s", log_dir)

    all_findings: list[ComplianceFinding] = []
    all_findings.extend(scan_database(db_path))
    all_findings.extend(scan_log_directory(log_dir))

    if not all_findings:
        print("\n" + "=" * 60)
        print("AUDIT CLEAN: Zero secrets or PHI detected")
        print("=" * 60)
        print(f"Scanned DB:   {db_path} (0 leaks)")
        print(f"Scanned Logs: {log_dir} (0 leaks)")
        print("Compliance status: PASSED\n")
        return 0, []

    print("\n" + "!" * 60)
    print(f"COMPLIANCE ALERT: Detected {len(all_findings)} sensitive data leaks!")
    print("!" * 60)
    for idx, finding in enumerate(all_findings, start=1):
        print(f"[{idx}] Rule: {finding.rule_name}")
        print(f"    Source: {finding.source_type} ({finding.location})")
        print(f"    Snippet: {finding.match_snippet}")
        print("-" * 60)

    return 1, all_findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automated Compliance Scanner for Q-SHIELD secrets and PHI zero-leakage."
    )
    parser.add_argument(
        "--db-path",
        default="./qshield.db",
        help="Path to SQLite database file to audit (default: ./qshield.db).",
    )
    parser.add_argument(
        "--log-dir",
        default="./logs",
        help="Path to logs directory to scan (default: ./logs).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail-fast strict verification mode.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write JSON audit report.",
    )

    args = parser.parse_args()
    db_path = Path(args.db_path)
    log_dir = Path(args.log_dir)

    exit_code, findings = run_compliance_audit(
        db_path=db_path,
        log_dir=log_dir,
        strict=args.strict,
    )

    if args.output:
        report_data: dict[str, Any] = {
            "db_path": str(db_path),
            "log_dir": str(log_dir),
            "findings_count": len(findings),
            "findings": [asdict(f) for f in findings],
            "passed": exit_code == 0,
        }
        with open(args.output, "w", encoding="utf-8") as out_f:
            json.dump(report_data, out_f, indent=2)
        logger.info("Compliance report written to %s", args.output)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
