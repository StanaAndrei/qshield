"""Discrete-event simulation engine for Q-SHIELD (TASK-3.4).

Orchestrates request arrivals, queueing, scheduling policy decisions,
network transfer delays, and completion events in virtual time without
sleeping real CPU time.

Achieves 1,000 transfer requests in under 5 seconds of real execution time
by running the SimPy event loop without I/O or real-time waits.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import simpy

from app.core.enums import DataCategory, PriorityLevel, SchedulingPolicyName
from app.models.scheduling import SchedulingDecision
from app.models.telemetry import NetworkTelemetry, QKDResourceMetrics, TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from app.scheduler.ai_assisted import AIAssistedScheduler
from app.scheduler.base import IPredictor, IScheduler
from app.scheduler.fifo import FIFOScheduler
from app.scheduler.priority import PriorityScheduler
from app.scheduler.resource_aware import ResourceAwareHeuristicScheduler
from simulation.key_resource_model import KeyAcquisitionStatus, KeyResourceConfig, KeyResourceModel
from simulation.network_model import NetworkConfig, NetworkModel
from simulation.workload_generator import WorkloadConfig, WorkloadGenerator, WorkloadRequest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-request simulation record
# ---------------------------------------------------------------------------

@dataclass
class TransferRecord:
    """Full record of a single transfer's simulated lifecycle."""

    request_id: str
    source_id: str
    destination_id: str
    data_size_bytes: int
    priority: PriorityLevel
    data_category: DataCategory
    arrival_time: float
    queue_time: float = 0.0
    """Virtual time the request spent waiting in the scheduler queue."""

    start_time: float = 0.0
    """Virtual time the transfer actually began."""

    completion_time: float = 0.0
    """Virtual time the transfer completed."""

    deadline: datetime | None = None
    deadline_met: bool = True

    key_wait_time: float = 0.0
    """Additional virtual time spent waiting for QKD keys."""

    policy_name: str = ""
    actual_throughput_mbps: float = 0.0
    success: bool = True
    failure_reason: str | None = None

    @property
    def total_duration_seconds(self) -> float:
        """End-to-end virtual time from arrival to completion."""
        return self.completion_time - self.arrival_time

    @property
    def transfer_duration_seconds(self) -> float:
        """Pure network transfer virtual time (without queue wait)."""
        return self.completion_time - self.start_time


# ---------------------------------------------------------------------------
# Simulation result
# ---------------------------------------------------------------------------

@dataclass
class SimulationResult:
    """Aggregated outcome of a single simulation run."""

    scenario_name: str
    policy_name: str
    seed: int
    total_requests: int
    completed: int
    failed: int
    deadline_met: int
    deadline_missed: int

    mean_total_duration_seconds: float
    median_total_duration_seconds: float
    p95_total_duration_seconds: float
    mean_queue_wait_seconds: float
    max_queue_wait_seconds: float
    mean_throughput_mbps: float
    deadline_success_rate: float

    real_elapsed_seconds: float
    """Wall-clock seconds for the entire simulation run."""

    records: list[TransferRecord] = field(default_factory=list)

    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scheduler factory
# ---------------------------------------------------------------------------

def _build_scheduler(
    policy_name: str,
    heuristic_overrides: dict[str, Any] | None = None,
    predictor: IPredictor | None = None,
) -> IScheduler:
    """Instantiate the scheduler for the given policy name."""
    if policy_name == SchedulingPolicyName.FIFO or policy_name == "FIFO":
        return FIFOScheduler()
    if policy_name == SchedulingPolicyName.PRIORITY or policy_name == "PRIORITY":
        return PriorityScheduler()
    if policy_name in (SchedulingPolicyName.RESOURCE_AWARE_HEURISTIC, "RESOURCE_AWARE_HEURISTIC"):
        overrides = heuristic_overrides or {}
        return ResourceAwareHeuristicScheduler(
            weight_priority=overrides.get("weight_priority"),
            weight_slack=overrides.get("weight_slack"),
            weight_aging=overrides.get("weight_aging"),
            max_consecutive_high_priority=overrides.get("max_consecutive_high_priority"),
        )
    if policy_name in (SchedulingPolicyName.AI_ASSISTED, "AI_ASSISTED"):
        overrides = heuristic_overrides or {}
        return AIAssistedScheduler(
            predictor=predictor,
            alpha_priority=overrides.get("alpha_priority", 2.0),
            beta_deadline=overrides.get("beta_deadline", 50.0),
            gamma_duration=overrides.get("gamma_duration", 0.05),
            delta_age=overrides.get("delta_age", 0.05),
        )
    raise ValueError(f"Unknown scheduling policy: {policy_name}")


# ---------------------------------------------------------------------------
# Discrete-event engine
# ---------------------------------------------------------------------------

@dataclass
class EngineConfig:
    """Configuration for one simulation engine run."""

    policy_name: str = "FIFO"
    scenario_name: str = "unnamed"
    seed: int = 42
    heuristic_overrides: dict[str, Any] = field(default_factory=dict)
    predictor: IPredictor | None = None


class SimulationEngine:
    """Discrete-event simulation engine built on SimPy.

    Runs entirely in virtual time — no real sleeps. Simulates the full
    Q-SHIELD transfer lifecycle:

      1. Request arrival (Poisson process)
      2. Enqueue + scheduling decision
      3. Key acquisition (with blocking wait on depletion)
      4. Network transfer (duration from NetworkModel)
      5. Completion recording

    Parameters
    ----------
    workload_config:
        Workload generation parameters.
    network_config:
        Network link characteristics.
    key_config:
        QKD key buffer parameters.
    engine_config:
        Simulation run parameters (policy, seed, etc.).
    """

    def __init__(
        self,
        workload_config: WorkloadConfig,
        network_config: NetworkConfig,
        key_config: KeyResourceConfig,
        engine_config: EngineConfig,
    ) -> None:
        self._wl_config = workload_config
        self._net_config = network_config
        self._key_config = key_config
        self._eng_config = engine_config

        # Reinitialise components fresh each run
        self._network = NetworkModel(network_config)
        self._key_buffer = KeyResourceModel(key_config)
        self._scheduler = _build_scheduler(
            engine_config.policy_name,
            engine_config.heuristic_overrides,
            predictor=engine_config.predictor,
        )

        # Records
        self._records: list[TransferRecord] = []

        # SimPy shared state
        self._env: simpy.Environment | None = None
        self._queue: list[TransferRequest] = []

    # ------------------------------------------------------------------
    # Public run method
    # ------------------------------------------------------------------

    def run(self) -> SimulationResult:
        """Execute the full simulation and return aggregated results.

        Returns
        -------
        SimulationResult
            Complete metrics for this simulation run.
        """
        t_start = time.perf_counter()

        # Generate workload
        generator = WorkloadGenerator(self._wl_config)
        workload_items: list[WorkloadRequest] = generator.generate()

        # Set up SimPy environment
        env = simpy.Environment()
        self._env = env
        self._queue = []
        self._records = []

        # Launch arrivals and dispatcher
        env.process(self._arrival_process(env, workload_items))
        env.process(self._dispatcher_process(env, len(workload_items)))

        # Run until completion or timeout (guard against infinite loops)
        total_expected_time = sum(wr.arrival_time for wr in workload_items) + 1e9
        env.run(until=total_expected_time)

        t_end = time.perf_counter()

        return self._build_result(t_end - t_start)

    # ------------------------------------------------------------------
    # SimPy processes
    # ------------------------------------------------------------------

    def _arrival_process(
        self,
        env: simpy.Environment,
        workload_items: list[WorkloadRequest],
    ) -> Generator[Any, Any, None]:
        """Generate transfer request arrivals at their scheduled virtual times."""
        for wr in workload_items:
            # Wait until this request's arrival time
            if wr.arrival_time > env.now:
                yield env.timeout(wr.arrival_time - env.now)

            # Add request to queue
            self._queue.append(wr.request)
            logger.debug(
                "t=%.2f: Request %s arrived (%s, %d bytes)",
                env.now, str(wr.request.request_id)[:8],
                wr.request.priority.name, wr.request.data_size_bytes,
            )

    def _dispatcher_process(
        self,
        env: simpy.Environment,
        total_requests: int,
    ) -> Generator[Any, Any, None]:
        """Schedule and execute transfers sequentially."""
        completed_count = 0

        while completed_count < total_requests:
            # Build a telemetry snapshot for the scheduler
            telemetry = self._build_telemetry(env.now)

            # Try to find an eligible request
            decision = self._scheduler.select_next(self._queue, telemetry)

            if decision is None:
                # Nothing schedulable yet — wait a tick
                yield env.timeout(1.0)
                continue

            # Remove selected request from queue
            selected = next(
                (r for r in self._queue if r.request_id == decision.selected_request_id),
                None,
            )
            if selected is None:
                yield env.timeout(0.1)
                continue

            self._queue.remove(selected)

            # Dispatch the transfer as a sub-process
            env.process(self._transfer_process(env, selected, decision, env.now))

            # Dispatcher continues to schedule next without waiting (concurrent transfers)
            # Yield a tiny timeout to allow arrival process to run
            yield env.timeout(0.1)
            completed_count += 1

    def _transfer_process(
        self,
        env: simpy.Environment,
        request: TransferRequest,
        decision: SchedulingDecision,
        dispatch_time: float,
    ) -> Generator[Any, Any, None]:
        """Execute a single transfer lifecycle in virtual time."""
        queue_wait = dispatch_time - request.metadata.get("arrival_time", dispatch_time)
        queue_wait = max(0.0, queue_wait)

        # --- Key acquisition ---
        key_wait = 0.0
        while True:
            result = self._key_buffer.try_acquire(env.now)
            if result.status == KeyAcquisitionStatus.SUCCESS:
                break
            # Wait for replenishment
            wait_time = max(0.1, result.wait_time_seconds)
            key_wait += wait_time
            yield env.timeout(wait_time)

        transfer_start = env.now

        # --- Network transfer ---
        transfer_duration = self._network.transfer_duration_seconds(
            request.data_size_bytes,
            simulation_time=env.now,
        )
        # Ensure non-zero duration
        transfer_duration = max(0.001, transfer_duration)

        yield env.timeout(transfer_duration)

        completion_time = env.now

        # --- Check deadline ---
        base_time = datetime(2026, 1, 1, tzinfo=UTC)
        actual_completion_dt = base_time + timedelta(seconds=completion_time)
        deadline_met = True
        if request.deadline is not None:
            deadline_met = actual_completion_dt <= request.deadline

        # --- Compute effective throughput ---
        effective_bps = (request.data_size_bytes * 8.0) / transfer_duration
        effective_mbps = effective_bps / 1_000_000.0

        # --- Record outcome ---
        record = TransferRecord(
            request_id=str(request.request_id),
            source_id=request.source_id,
            destination_id=request.destination_id,
            data_size_bytes=request.data_size_bytes,
            priority=request.priority,
            data_category=request.data_category,
            arrival_time=request.metadata.get("arrival_time", dispatch_time),
            queue_time=queue_wait,
            start_time=transfer_start,
            completion_time=completion_time,
            deadline=request.deadline,
            deadline_met=deadline_met,
            key_wait_time=key_wait,
            policy_name=self._eng_config.policy_name,
            actual_throughput_mbps=effective_mbps,
            success=True,
        )
        self._records.append(record)

        logger.debug(
            "t=%.2f: Transfer %s completed (duration=%.2fs, deadline=%s)",
            env.now, str(request.request_id)[:8], transfer_duration,
            "MET" if deadline_met else "MISSED",
        )

    # ------------------------------------------------------------------
    # Telemetry builder
    # ------------------------------------------------------------------

    def _build_telemetry(self, sim_time: float) -> TelemetrySnapshot:
        """Construct a TelemetrySnapshot from current simulation state."""
        from app.core.enums import QKDLinkStatus

        link_key = f"{self._net_config.source_id}->{self._net_config.destination_id}"
        net_sample = self._network.sample(sim_time)

        network_metrics = {
            link_key: NetworkTelemetry(
                source_id=self._net_config.source_id,
                destination_id=self._net_config.destination_id,
                observed_throughput_mbps=net_sample.effective_throughput_mbps,
                packet_loss_rate=float(self._net_config.packet_drop_probability),
                round_trip_time_ms=self._net_config.rtt_ms,
                is_simulated=True,
            )
        }

        key_snapshot = self._key_buffer.snapshot()
        key_status = QKDLinkStatus.CONNECTED if key_snapshot.available_keys > 0 else QKDLinkStatus.DEGRADED
        qkd_metrics = {
            link_key: QKDResourceMetrics(
                source_id=self._key_config.source_id,
                destination_id=self._key_config.destination_id,
                link_status=key_status,
                key_buffer_available_keys=key_snapshot.available_keys,
                key_generation_rate_bps=self._key_config.generation_rate_keys_per_second * 256,
                key_service_latency_ms=1.0,
                is_simulated=True,
            )
        }

        return TelemetrySnapshot(
            active_transfers_count=0,
            queue_length=len(self._queue),
            network_metrics=network_metrics,
            qkd_metrics=qkd_metrics,
            system_load_factor=min(1.0, len(self._queue) / max(1, self._wl_config.total_requests)),
        )

    # ------------------------------------------------------------------
    # Result builder
    # ------------------------------------------------------------------

    def _build_result(self, real_elapsed: float) -> SimulationResult:
        """Aggregate records into a ``SimulationResult``."""
        import statistics

        records = self._records
        total = len(records)
        completed = sum(1 for r in records if r.success)
        failed = total - completed
        deadline_requests = [r for r in records if r.deadline is not None]
        deadline_met_count = sum(1 for r in deadline_requests if r.deadline_met)
        deadline_missed = len(deadline_requests) - deadline_met_count

        durations = [r.total_duration_seconds for r in records] or [0.0]
        queue_waits = [r.queue_time for r in records] or [0.0]
        throughputs = [r.actual_throughput_mbps for r in records] or [0.0]

        sorted_durations = sorted(durations)
        n = len(sorted_durations)
        median_dur = statistics.median(sorted_durations)
        p95_dur = sorted_durations[int(0.95 * n)] if n > 1 else sorted_durations[0]

        deadline_success_rate = (
            deadline_met_count / len(deadline_requests) if deadline_requests else 1.0
        )

        return SimulationResult(
            scenario_name=self._eng_config.scenario_name,
            policy_name=self._eng_config.policy_name,
            seed=self._eng_config.seed,
            total_requests=total,
            completed=completed,
            failed=failed,
            deadline_met=deadline_met_count,
            deadline_missed=deadline_missed,
            mean_total_duration_seconds=sum(durations) / max(1, len(durations)),
            median_total_duration_seconds=median_dur,
            p95_total_duration_seconds=p95_dur,
            mean_queue_wait_seconds=sum(queue_waits) / max(1, len(queue_waits)),
            max_queue_wait_seconds=max(queue_waits),
            mean_throughput_mbps=sum(throughputs) / max(1, len(throughputs)),
            deadline_success_rate=deadline_success_rate,
            real_elapsed_seconds=real_elapsed,
            records=records,
        )


__all__ = [
    "EngineConfig",
    "SimulationEngine",
    "SimulationResult",
    "TransferRecord",
]
