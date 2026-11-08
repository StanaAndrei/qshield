"""Integration tests for Milestone 2 baseline schedulers (TASK-2.5).

Comparative tests running all three policies against identical 50-request queues,
verifying basic behavioral properties and SchedulingService integration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.enums import (
    DataCategory,
    PriorityLevel,
    SchedulingPolicyName,
    SecurityProfile,
    TransferStatus,
)
from app.models.telemetry import NetworkTelemetry, TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.services.scheduling_service import SchedulingService
from tests.fixtures.mock_repository import InMemoryTransferRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    priority: PriorityLevel = PriorityLevel.NORMAL,
    *,
    submitted_offset_seconds: float = 0.0,
    data_size_bytes: int = 5_242_880,
    deadline: datetime | None = None,
    source_id: str = "univ-lab",
    destination_id: str = "poly-clinic",
) -> TransferRequest:
    base_time = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    return TransferRequest(
        request_id=uuid4(),
        source_id=source_id,
        destination_id=destination_id,
        data_size_bytes=data_size_bytes,
        priority=priority,
        submitted_at=base_time + timedelta(seconds=submitted_offset_seconds),
        deadline=deadline,
        security_profile=SecurityProfile.QKD_AES_256_GCM,
        data_category=DataCategory.DIAGNOSTIC_REPORT,
        status=TransferStatus.QUEUED,
    )


def _make_telemetry(throughput_mbps: float = 100.0) -> TelemetrySnapshot:
    return TelemetrySnapshot(
        timestamp=datetime.now(UTC),
        network_metrics={
            "univ-lab->poly-clinic": NetworkTelemetry(
                source_id="univ-lab",
                destination_id="poly-clinic",
                observed_throughput_mbps=throughput_mbps,
                packet_loss_rate=0.001,
                round_trip_time_ms=10.0,
            )
        },
    )


def _populate_repo(repo: InMemoryTransferRepository, requests: list[TransferRequest]) -> None:
    for req in requests:
        repo.create_request(req)


def _make_mixed_queue(n: int = 50) -> list[TransferRequest]:
    """Generate a mixed queue of *n* requests with varied priorities and sizes."""
    priorities = [PriorityLevel.LOW, PriorityLevel.NORMAL, PriorityLevel.HIGH, PriorityLevel.CRITICAL]
    sizes = [1_000_000, 10_000_000, 100_000_000, 500_000_000]
    requests = []
    for i in range(n):
        priority = priorities[i % len(priorities)]
        size = sizes[i % len(sizes)]
        requests.append(
            _make_request(
                priority=priority,
                submitted_offset_seconds=float(i * 2),  # stagger submissions by 2s
                data_size_bytes=size,
            )
        )
    return requests


# ---------------------------------------------------------------------------
# Integration tests: SchedulingService with each policy
# ---------------------------------------------------------------------------


class TestSchedulingServiceFIFO:
    def test_fifo_selects_in_submission_order(self) -> None:
        """FIFO must always pick the earliest-submitted request."""
        repo = InMemoryTransferRepository()
        requests = _make_mixed_queue(10)
        _populate_repo(repo, requests)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.FIFO,
            telemetry_provider=lambda: _make_telemetry(),
        )

        decision = service.run_scheduling_cycle()
        assert decision is not None

        # The selected request should be the earliest-submitted QUEUED request
        earliest = min(requests, key=lambda r: (r.submitted_at, str(r.request_id)))
        assert decision.selected_request_id == earliest.request_id

    def test_fifo_drains_queue_in_order(self) -> None:
        """Running multiple FIFO cycles produces strictly chronological picks."""
        repo = InMemoryTransferRepository()
        requests = _make_mixed_queue(10)
        _populate_repo(repo, requests)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.FIFO,
            telemetry_provider=lambda: _make_telemetry(),
        )

        # Run 3 cycles; each should select progressively later requests
        decisions = service.run_n_cycles(3)
        assert len(decisions) == 3

        # Verify selected IDs are in submission order
        selected_ids = [str(d.selected_request_id) for d in decisions]
        sorted_requests = sorted(requests, key=lambda r: (r.submitted_at, str(r.request_id)))
        expected_ids = [str(r.request_id) for r in sorted_requests[:3]]
        assert selected_ids == expected_ids

    def test_fifo_records_decision_to_repo(self) -> None:
        repo = InMemoryTransferRepository()
        requests = _make_mixed_queue(5)
        _populate_repo(repo, requests)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.FIFO,
            telemetry_provider=lambda: _make_telemetry(),
        )

        decision = service.run_scheduling_cycle()
        assert decision is not None

        # Decision must be persisted to the repository
        recorded = repo.get_scheduling_decisions()
        assert len(recorded) == 1
        assert recorded[0].selected_request_id == decision.selected_request_id

    def test_fifo_transitions_selected_request_to_scheduled(self) -> None:
        repo = InMemoryTransferRepository()
        req = _make_request(priority=PriorityLevel.NORMAL)
        repo.create_request(req)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.FIFO,
            telemetry_provider=lambda: _make_telemetry(),
        )

        decision = service.run_scheduling_cycle()
        assert decision is not None

        updated = repo.get_request(req.request_id)
        assert updated is not None
        assert updated.status == TransferStatus.SCHEDULED


class TestSchedulingServicePriority:
    def test_priority_persists_to_sqlite(self, db_session: Session) -> None:
        """DoD (TASK-2.4): Service logs structured scheduling decisions with duration and rationale to SQLite."""
        from app.database.repository import SqliteTransferRepository
        from app.database.schema import Base

        Base.metadata.create_all(bind=db_session.get_bind())
        repo = SqliteTransferRepository(db_session)
        req = _make_request(priority=PriorityLevel.HIGH)
        repo.create_request(req)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.PRIORITY,
            telemetry_provider=lambda: _make_telemetry(),
        )

        decision = service.run_scheduling_cycle()
        assert decision is not None

        decisions = repo.get_scheduling_decisions()
        assert len(decisions) == 1
        assert decisions[0].selected_request_id == req.request_id
        assert decisions[0].decision_reason == decision.decision_reason

        # Also check state transition
        updated_req = repo.get_request(req.request_id)
        assert updated_req is not None
        assert updated_req.status == TransferStatus.SCHEDULED

    def test_priority_policy_picks_highest_priority(self) -> None:
        """Priority policy must pick CRITICAL first regardless of submission order."""
        repo = InMemoryTransferRepository()

        normal = _make_request(priority=PriorityLevel.NORMAL, submitted_offset_seconds=0)
        critical = _make_request(priority=PriorityLevel.CRITICAL, submitted_offset_seconds=100)
        _populate_repo(repo, [normal, critical])

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.PRIORITY,
            telemetry_provider=lambda: _make_telemetry(),
        )

        decision = service.run_scheduling_cycle()
        assert decision is not None
        assert decision.selected_request_id == critical.request_id

    def test_priority_policy_records_audit_event(self) -> None:
        repo = InMemoryTransferRepository()
        req = _make_request(priority=PriorityLevel.HIGH)
        repo.create_request(req)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.PRIORITY,
            telemetry_provider=lambda: _make_telemetry(),
        )

        service.run_scheduling_cycle()

        audit_events = repo.get_audit_events()
        assert len(audit_events) >= 1
        scheduling_events = [e for e in audit_events if "SCHEDULING" in e.event_type]
        assert len(scheduling_events) == 1

    def test_priority_runs_on_50_request_queue(self) -> None:
        """Priority policy must handle 50-request queue without errors."""
        repo = InMemoryTransferRepository()
        requests = _make_mixed_queue(50)
        _populate_repo(repo, requests)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.PRIORITY,
            telemetry_provider=lambda: _make_telemetry(),
        )

        decision = service.run_scheduling_cycle()
        assert decision is not None
        # Must select one of the CRITICAL requests
        critical_ids = {r.request_id for r in requests if r.priority == PriorityLevel.CRITICAL}
        assert decision.selected_request_id in critical_ids


class TestSchedulingServiceResourceAware:
    def test_resource_aware_selects_a_request(self) -> None:
        repo = InMemoryTransferRepository()
        requests = _make_mixed_queue(20)
        _populate_repo(repo, requests)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC,
            telemetry_provider=lambda: _make_telemetry(),
        )

        decision = service.run_scheduling_cycle()
        assert decision is not None

    def test_resource_aware_on_50_request_queue(self) -> None:
        """Resource-aware heuristic must handle 50-request queue without errors."""
        repo = InMemoryTransferRepository()
        requests = _make_mixed_queue(50)
        _populate_repo(repo, requests)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC,
            telemetry_provider=lambda: _make_telemetry(),
            scheduler_overrides={
                "weight_priority": 2.0,
                "weight_slack": 0.01,
                "weight_aging": 0.05,
                "max_consecutive_high_priority": 5,
            },
        )

        decisions = service.run_n_cycles(10)
        assert len(decisions) == 10

    def test_resource_aware_aging_promotes_low_after_many_high_picks(self) -> None:
        """After max_consecutive_high_priority, LOW request gets served."""
        repo = InMemoryTransferRepository()

        # One OLD low-priority request
        old_low = TransferRequest(
            request_id=uuid4(),
            source_id="univ-lab",
            destination_id="poly-clinic",
            data_size_bytes=1_000_000,
            priority=PriorityLevel.LOW,
            submitted_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),  # very old
            security_profile=SecurityProfile.QKD_AES_256_GCM,
            data_category=DataCategory.DIAGNOSTIC_REPORT,
            status=TransferStatus.QUEUED,
        )
        repo.create_request(old_low)

        # Three HIGH requests
        for i in range(3):
            high = _make_request(priority=PriorityLevel.HIGH, submitted_offset_seconds=float(i))
            repo.create_request(high)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC,
            telemetry_provider=lambda: _make_telemetry(),
            scheduler_overrides={
                "weight_priority": 100.0,  # strongly prefer HIGH
                "weight_slack": 0.0,
                "weight_aging": 0.0,
                "max_consecutive_high_priority": 2,  # preempt after 2
            },
        )

        selected_ids = []
        for _ in range(3):
            d = service.run_scheduling_cycle()
            if d:
                selected_ids.append(d.selected_request_id)

        # The 3rd selection must be the low-priority request
        assert old_low.request_id in selected_ids, (
            "LOW request must receive service after forced preemption"
        )


class TestPolicyComparison:
    """Comparative tests running all three policies on the same queue."""

    def _run_policy(
        self,
        policy: SchedulingPolicyName,
        requests: list[TransferRequest],
        n_cycles: int = 5,
        scheduler_overrides: dict | None = None,
    ) -> list[str]:
        """Return list of selected request UUIDs (as strings) for given policy."""
        repo = InMemoryTransferRepository()
        _populate_repo(repo, requests)

        service = SchedulingService(
            repository=repo,
            policy=policy,
            telemetry_provider=lambda: _make_telemetry(),
            scheduler_overrides=scheduler_overrides or {},
        )

        decisions = service.run_n_cycles(n_cycles)
        return [str(d.selected_request_id) for d in decisions]

    def test_all_policies_make_selections_on_mixed_queue(self) -> None:
        """All three policies must make selections from a 50-request mixed queue."""
        requests = _make_mixed_queue(50)
        for policy in [
            SchedulingPolicyName.FIFO,
            SchedulingPolicyName.PRIORITY,
            SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC,
        ]:
            selected = self._run_policy(policy, requests, n_cycles=5)
            assert len(selected) == 5, f"Policy {policy} failed to make 5 selections"

    def test_fifo_and_priority_differ_on_mixed_queue(self) -> None:
        """FIFO and PRIORITY should select different first requests on a mixed queue."""
        requests = _make_mixed_queue(20)

        fifo_selected = self._run_policy(SchedulingPolicyName.FIFO, requests, n_cycles=1)
        priority_selected = self._run_policy(SchedulingPolicyName.PRIORITY, requests, n_cycles=1)

        # FIFO selects the oldest submitted request.
        oldest_id = str(min(requests, key=lambda r: (r.submitted_at, str(r.request_id))).request_id)
        assert fifo_selected[0] == oldest_id

        # PRIORITY selects the highest-priority request using the same tiebreaker
        # as PriorityScheduler: (-priority_value, submitted_at, str(request_id)).
        highest_priority = min(
            requests,
            key=lambda r: (-r.priority.value, r.submitted_at, str(r.request_id)),
        )
        highest_priority_id = str(highest_priority.request_id)
        assert priority_selected[0] == highest_priority_id

        # On a mixed-priority queue the two policies must pick different requests.
        assert fifo_selected[0] != priority_selected[0], (
            "FIFO and PRIORITY should diverge on a mixed-priority queue"
        )

    def test_no_policy_selects_non_queued_request(self) -> None:
        """No policy should ever select a non-QUEUED request."""
        requests = _make_mixed_queue(20)

        for policy in [
            SchedulingPolicyName.FIFO,
            SchedulingPolicyName.PRIORITY,
            SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC,
        ]:
            repo = InMemoryTransferRepository()
            _populate_repo(repo, requests)

            service = SchedulingService(
                repository=repo,
                policy=policy,
                telemetry_provider=lambda: _make_telemetry(),
            )

            decisions = service.run_n_cycles(5)
            selected_ids = {d.selected_request_id for d in decisions}

            for selected_id in selected_ids:
                assert repo.get_request(selected_id) is not None
                # After scheduling it transitions to SCHEDULED, so check original status
                # via transitions (the transition *from* QUEUED must exist)
                transitions = repo.get_transitions(selected_id)
                from_statuses = {t.from_status for t in transitions}
                assert TransferStatus.QUEUED in from_statuses, (
                    f"Policy {policy} selected a request that was not QUEUED"
                )

    def test_policy_switch_at_runtime(self) -> None:
        """SchedulingService must accept policy switch without errors."""
        repo = InMemoryTransferRepository()
        requests = _make_mixed_queue(10)
        _populate_repo(repo, requests)

        service = SchedulingService(
            repository=repo,
            policy=SchedulingPolicyName.FIFO,
            telemetry_provider=lambda: _make_telemetry(),
        )

        d1 = service.run_scheduling_cycle()
        assert d1 is not None

        service.switch_policy(SchedulingPolicyName.PRIORITY)
        assert service.active_policy == SchedulingPolicyName.PRIORITY

        d2 = service.run_scheduling_cycle()
        assert d2 is not None

    def test_empty_queue_returns_no_decision(self) -> None:
        """When the queue is empty all policies must return None."""
        repo = InMemoryTransferRepository()

        for policy in [
            SchedulingPolicyName.FIFO,
            SchedulingPolicyName.PRIORITY,
            SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC,
        ]:
            service = SchedulingService(
                repository=repo,
                policy=policy,
                telemetry_provider=lambda: _make_telemetry(),
            )
            decision = service.run_scheduling_cycle()
            assert decision is None, f"Policy {policy} returned a decision on empty queue"
