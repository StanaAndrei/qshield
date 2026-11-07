#!/usr/bin/env python3
"""Standalone Tracer Bullet Verification CLI Script for Q-SHIELD.

Executes the complete end-to-end transfer lifecycle across architecture boundaries:
    1. Client Submission (TransferRequestCreate -> SUBMITTED)
    2. Deterministic Security Policy Validation (SUBMITTED -> VALIDATING -> QUEUED)
    3. FIFO Scheduling Selection (QUEUED -> SCHEDULED)
    4. Synthetic Transfer Execution (SCHEDULED -> TRANSFERRING)
    5. Cryptographic SHA-256 Integrity Verification (TRANSFERRING -> VERIFYING)
    6. Lifecycle Completion (VERIFYING -> COMPLETED)

Usage:
    python scripts/verify_tracer_bullet.py --payload-size-mb 5 --source "univ-lab" --destination "poly-clinic"
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# Ensure project root is available on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.enums import (
    DataCategory,
    PriorityLevel,
    SecurityProfile,
    TransferStatus,
)
from app.core.exceptions import InvalidStateTransitionError
from app.core.state_machine import TransferStateMachine
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import (
    StateTransitionRecord,
    TransferRequest,
    TransferRequestCreate,
)
from app.scheduler.fifo import FIFOScheduler
from app.security.policy_engine import SecurityPolicyEngine
from tests.fixtures.mock_repository import InMemoryTransferRepository


def generate_synthetic_payload(size_bytes: int, seed: int = 42) -> bytes:
    """Generates a deterministic synthetic medical payload buffer for verification."""
    chunk_size = 65536
    pattern = f"QSHIELD_SYNTHETIC_DATA_PAYLOAD_SEED_{seed}_SECURE_EXCHANGE_".encode()
    repeats = (chunk_size // len(pattern)) + 1
    base_chunk = (pattern * repeats)[:chunk_size]

    full_chunks = size_bytes // chunk_size
    remainder = size_bytes % chunk_size

    parts = [base_chunk] * full_chunks
    if remainder > 0:
        parts.append(base_chunk[:remainder])
    return b"".join(parts)


class TracerBulletRunner:
    """Orchestrates the standalone tracer bullet lifecycle execution."""

    def __init__(
        self,
        payload_size_mb: float = 5.0,
        source_id: str = "univ-lab",
        destination_id: str = "poly-clinic",
        priority: PriorityLevel = PriorityLevel.NORMAL,
        security_profile: SecurityProfile = SecurityProfile.QKD_AES_256_GCM,
        data_category: DataCategory = DataCategory.DIAGNOSTIC_REPORT,
        simulate_failure: bool = False,
        seed: int = 42,
    ) -> None:
        self.payload_size_mb = payload_size_mb
        self.size_bytes = int(payload_size_mb * 1024 * 1024)
        self.source_id = source_id
        self.destination_id = destination_id
        self.priority = priority
        self.security_profile = security_profile
        self.data_category = data_category
        self.simulate_failure = simulate_failure
        self.seed = seed

        self.repository = InMemoryTransferRepository()
        self.transitions: list[StateTransitionRecord] = []
        self.sender_checksum: str = ""
        self.receiver_checksum: str = ""
        self.request: TransferRequest | None = None

    def log_step(self, step_num: int, total_steps: int, title: str, details: list[str]) -> None:
        print(f"\n[STEP {step_num}/{total_steps}] {title}")
        for line in details:
            print(f"  • {line}")

    def execute(self) -> int:
        start_time = time.perf_counter()

        print("=" * 80)
        print("                Q-SHIELD STANDALONE TRACER BULLET VERIFICATION")
        print("=" * 80)
        print(f"Payload Size:        {self.payload_size_mb:.2f} MB ({self.size_bytes:,} bytes)")
        print(f"Source Node:         {self.source_id}")
        print(f"Destination Node:    {self.destination_id}")
        print(f"Security Profile:    {self.security_profile.value}")
        print(f"Priority Level:      {self.priority.name} (Value: {self.priority.value})")
        print(f"Data Category:       {self.data_category.value}")
        print(f"Simulate Failure:    {self.simulate_failure}")
        print("=" * 80)

        # -------------------------------------------------------------------------
        # STEP 1: Client Request Creation & Validation
        # -------------------------------------------------------------------------
        req_create = TransferRequestCreate(
            source_id=self.source_id,
            destination_id=self.destination_id,
            data_size_bytes=self.size_bytes,
            priority=self.priority,
            security_profile=self.security_profile,
            data_category=self.data_category,
            metadata={"environment": "simulation", "verification_run": "tracer_bullet"},
        )

        canonical_request = TransferRequest(
            request_id=uuid4(),
            source_id=req_create.source_id,
            destination_id=req_create.destination_id,
            data_size_bytes=req_create.data_size_bytes,
            priority=req_create.priority,
            submitted_at=datetime.now(UTC),
            deadline=req_create.deadline,
            security_profile=req_create.security_profile,
            data_category=req_create.data_category,
            status=TransferStatus.SUBMITTED,
            metadata=req_create.metadata,
        )

        self.request = self.repository.create_request(canonical_request)
        self.log_step(
            1,
            6,
            "Transfer Request Submission",
            [
                f"Assigned Request ID: {self.request.request_id}",
                f"Payload Size: {self.request.data_size_bytes:,} bytes",
                f"Initial Status: {self.request.status.value}",
            ],
        )

        # -------------------------------------------------------------------------
        # STEP 2: Security Policy Validation (SUBMITTED -> VALIDATING -> QUEUED)
        # -------------------------------------------------------------------------
        # 2a. Move to VALIDATING
        t_val = TransferStateMachine.transition(
            self.request,
            TransferStatus.VALIDATING,
            actor="policy_engine",
            reason="Beginning deterministic authorization evaluation",
        )
        self.transitions.append(t_val)

        # 2b. Evaluate deterministic security policy via SecurityPolicyEngine
        policy_engine = SecurityPolicyEngine()
        decision = policy_engine.evaluate_request(self.request)

        if not decision.allowed:
            t_rej = TransferStateMachine.transition(
                self.request,
                TransferStatus.REJECTED,
                actor="policy_engine",
                reason=decision.reason_code,
            )
            self.transitions.append(t_rej)
            self.repository.update_status(
                self.request.request_id,
                TransferStatus.REJECTED,
                actor="policy_engine",
                reason=decision.reason_code,
            )
            print(f"\n[POLICY REJECTION] {decision.explanation}")
            return 1

        # 2c. Move to QUEUED
        t_queued = TransferStateMachine.transition(
            self.request,
            TransferStatus.QUEUED,
            actor="policy_engine",
            reason=f"Policy approved: {decision.reason_code}",
        )
        self.transitions.append(t_queued)
        self.repository.update_status(
            self.request.request_id,
            TransferStatus.QUEUED,
            actor="policy_engine",
            reason=decision.reason_code,
        )

        self.log_step(
            2,
            6,
            "Security Policy Evaluation",
            [
                f"Policy Decision: ALLOWED={decision.allowed}",
                f"Reason Code: {decision.reason_code}",
                f"Explanation: {decision.explanation}",
                f"Lifecycle: VALIDATING -> {self.request.status.value}",
            ],
        )

        # -------------------------------------------------------------------------
        # STEP 3: FIFO Scheduling Selection (QUEUED -> SCHEDULED)
        # -------------------------------------------------------------------------
        scheduler = FIFOScheduler()
        sched_decision = scheduler.select_next([self.request], TelemetrySnapshot())
        if sched_decision is None:
            print("\n[SCHEDULING ERROR] FIFO scheduler returned no decision for queued request")
            return 1
        self.repository.record_scheduling_decision(sched_decision)

        t_sched = TransferStateMachine.transition(
            self.request,
            TransferStatus.SCHEDULED,
            actor="fifo_scheduler",
            reason=sched_decision.decision_reason,
        )
        self.transitions.append(t_sched)
        self.repository.update_status(
            self.request.request_id,
            TransferStatus.SCHEDULED,
            actor="fifo_scheduler",
            reason=sched_decision.decision_reason,
        )

        self.log_step(
            3,
            6,
            "FIFO Scheduling Dispatch",
            [
                f"Scheduler Policy: {sched_decision.policy_name.value}",
                f"Decision Reason: {sched_decision.decision_reason}",
                f"Scheduled At: {self.request.scheduled_at.isoformat() if self.request.scheduled_at else 'now'}",
                f"Status: QUEUED -> {self.request.status.value}",
            ],
        )

        # -------------------------------------------------------------------------
        # STEP 4: Payload Generation & Synthetic Transmission (SCHEDULED -> TRANSFERRING)
        # -------------------------------------------------------------------------
        t_trans = TransferStateMachine.transition(
            self.request,
            TransferStatus.TRANSFERRING,
            actor="transfer_executor",
            reason="Synthetic transport initiated",
        )
        self.transitions.append(t_trans)
        self.repository.update_status(
            self.request.request_id,
            TransferStatus.TRANSFERRING,
            actor="transfer_executor",
            reason="Transmission started",
        )

        # Generate payload & compute sender SHA-256
        payload_bytes = generate_synthetic_payload(self.size_bytes, seed=self.seed)
        self.sender_checksum = hashlib.sha256(payload_bytes).hexdigest()

        # Simulated transmission: compute duration at ~100 MB/s
        simulated_bandwidth_mbps = 800.0  # 100 MB/s
        transmission_duration_sec = (self.size_bytes * 8.0) / (simulated_bandwidth_mbps * 1_000_000.0)

        self.log_step(
            4,
            6,
            "Synthetic Transfer Execution",
            [
                f"Bytes Transmitted: {len(payload_bytes):,} bytes",
                f"Sender SHA-256: {self.sender_checksum}",
                f"Simulated Bandwidth: {simulated_bandwidth_mbps:.1f} Mbps",
                f"Simulated Transmission Duration: {transmission_duration_sec:.4f}s",
                f"Status: SCHEDULED -> {self.request.status.value}",
            ],
        )

        # -------------------------------------------------------------------------
        # STEP 5: Integrity Verification (TRANSFERRING -> VERIFYING)
        # -------------------------------------------------------------------------
        t_ver = TransferStateMachine.transition(
            self.request,
            TransferStatus.VERIFYING,
            actor="integrity_verifier",
            reason="Data received; calculating receiver-side SHA-256 checksum",
        )
        self.transitions.append(t_ver)
        self.repository.update_status(
            self.request.request_id,
            TransferStatus.VERIFYING,
            actor="integrity_verifier",
            reason="Calculating SHA-256",
        )

        # Corrupt data if failure simulation requested
        received_bytes = payload_bytes
        if self.simulate_failure:
            received_bytes = payload_bytes[:-1] + b"\x00"

        self.receiver_checksum = hashlib.sha256(received_bytes).hexdigest()
        integrity_ok = self.sender_checksum == self.receiver_checksum

        self.log_step(
            5,
            6,
            "Cryptographic Integrity Verification",
            [
                f"Sender Checksum:   {self.sender_checksum}",
                f"Receiver Checksum: {self.receiver_checksum}",
                f"Checksum Match:    {'VERIFIED (Exact Match)' if integrity_ok else 'FAILED (Mismatch)'}",
                f"Status: TRANSFERRING -> {self.request.status.value}",
            ],
        )

        # -------------------------------------------------------------------------
        # STEP 6: Lifecycle Completion or Failure (VERIFYING -> COMPLETED / FAILED)
        # -------------------------------------------------------------------------
        if not integrity_ok:
            t_fail = TransferStateMachine.transition(
                self.request,
                TransferStatus.FAILED,
                actor="integrity_verifier",
                reason="SHA-256 checksum mismatch detected on received payload",
            )
            self.transitions.append(t_fail)
            self.repository.update_status(
                self.request.request_id,
                TransferStatus.FAILED,
                actor="integrity_verifier",
                reason="Integrity verification failed",
            )
            print("\n" + "!" * 80)
            print("ERROR: INTEGRITY VERIFICATION FAILED (SHA-256 MISMATCH)")
            print("!" * 80)
            return 1

        # Checksum verified: set checksum on request and transition to COMPLETED
        self.request.payload_checksum_sha256 = self.receiver_checksum
        t_comp = TransferStateMachine.transition(
            self.request,
            TransferStatus.COMPLETED,
            actor="transfer_executor",
            reason="SHA-256 integrity check verified successfully",
        )
        self.transitions.append(t_comp)
        self.repository.update_status(
            self.request.request_id,
            TransferStatus.COMPLETED,
            actor="transfer_executor",
            reason="Transfer verified and completed",
        )

        elapsed_wall_time = time.perf_counter() - start_time
        self.log_step(
            6,
            6,
            "Transfer Finalization",
            [
                f"Final Lifecycle Status: {self.request.status.value}",
                f"Verification Completed At: {self.request.verification_completed_at.isoformat() if self.request.verification_completed_at else 'now'}",
                f"Total Wall Clock Time: {elapsed_wall_time * 1000:.2f} ms",
            ],
        )

        # -------------------------------------------------------------------------
        # Summary & DoD Assertion
        # -------------------------------------------------------------------------
        print("\n" + "-" * 80)
        print("LIFECYCLE TRANSITION LOG AUDIT TRAIL:")
        for idx, tr in enumerate(self.transitions, 1):
            reason_str = f" ({tr.reason})" if tr.reason else ""
            print(f"  {idx}. {tr.from_status.value:12} -> {tr.to_status.value:12} [Actor: {tr.actor}]{reason_str}")
        print("-" * 80)
        print(f"Verified Payload SHA-256: {self.request.payload_checksum_sha256}")
        print(f"Total Execution Time:    {elapsed_wall_time:.4f}s")
        print("=" * 80)
        print("TRACER BULLET COMPLETE: OK")
        print("=" * 80)

        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Q-SHIELD Tracer Bullet Verification CLI (Agent A - Milestone 1)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--payload-size-mb",
        type=float,
        default=5.0,
        help="Synthetic payload size in megabytes",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="univ-lab",
        help="Source node identifier",
    )
    parser.add_argument(
        "--destination",
        type=str,
        default="poly-clinic",
        help="Authorized destination node identifier",
    )
    parser.add_argument(
        "--priority",
        type=str,
        default="NORMAL",
        choices=["LOW", "NORMAL", "HIGH", "CRITICAL"],
        help="Operational scheduling priority hint",
    )
    parser.add_argument(
        "--security-profile",
        type=str,
        default="QKD_AES_256_GCM",
        choices=[p.value for p in SecurityProfile],
        help="Security profile",
    )
    parser.add_argument(
        "--simulate-failure",
        action="store_true",
        help="Artificially corrupt payload to verify failure on checksum mismatch",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic pseudo-random payload seed",
    )

    args = parser.parse_args()

    try:
        priority_enum = PriorityLevel[args.priority]
        security_enum = SecurityProfile(args.security_profile)
    except (KeyError, ValueError) as err:
        print(f"Configuration error: {err}", file=sys.stderr)
        return 2

    runner = TracerBulletRunner(
        payload_size_mb=args.payload_size_mb,
        source_id=args.source,
        destination_id=args.destination,
        priority=priority_enum,
        security_profile=security_enum,
        simulate_failure=args.simulate_failure,
        seed=args.seed,
    )

    try:
        return runner.execute()
    except InvalidStateTransitionError as err:
        print(f"\n[FATAL] State transition error: {err}", file=sys.stderr)
        return 1
    except Exception as err:
        print(f"\n[FATAL] Unexpected error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
