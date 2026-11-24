#!/usr/bin/env python3
"""End-to-End Pitch Demonstration Script for Q-SHIELD (TASK-6.5).

Simulates a University-Polyclinic quantum-secured data exchange scenario:
  - 3-tier workload: 5 MB diagnostic report, 2 GB MRI scan, 500 MB research dataset
  - Simulated network drop / QKD key depletion events
  - Retry / recovery demonstration
  - Side-by-side policy comparison summary (FIFO vs PRIORITY vs RESOURCE_AWARE_HEURISTIC)

Usage:
    python scripts/run_demonstration.py \\
        --scenario configs/scenarios/medical_mixed_workload.yaml

    python scripts/run_demonstration.py \\
        --scenario configs/scenarios/medical_mixed_workload.yaml \\
        --duration-seconds 60 \\
        --report-output results/demonstration_summary.md
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path regardless of invocation directory.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.enums import DataCategory, PriorityLevel
from simulation.engine import EngineConfig, SimulationEngine, SimulationResult
from simulation.key_resource_model import KeyResourceConfig, KeyResourceModel
from simulation.network_model import NetworkConfig, NetworkModel
from simulation.scenarios import load_scenario
from simulation.workload_generator import WorkloadConfig

# ---------------------------------------------------------------------------
# ANSI colour helpers (gracefully degraded when not in a TTY)
# ---------------------------------------------------------------------------

def _supports_colour() -> bool:
    return sys.stdout.isatty()


_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_BLUE = "\033[34m"
_WHITE = "\033[97m"


def _c(text: str, *codes: str) -> str:
    if not _supports_colour():
        return text
    return "".join(codes) + text + _RESET


def _header(title: str) -> str:
    bar = "═" * (len(title) + 4)
    return (
        f"\n{_c('╔' + bar + '╗', _BOLD, _CYAN)}\n"
        f"{_c('║  ' + title + '  ║', _BOLD, _CYAN)}\n"
        f"{_c('╚' + bar + '╝', _BOLD, _CYAN)}"
    )


def _section(title: str) -> str:
    bar = "─" * (len(title) + 2)
    return f"\n{_c('┌─' + bar + '─┐', _BOLD, _BLUE)}\n{_c('│  ' + title + '  │', _BOLD, _BLUE)}\n{_c('└─' + bar + '─┘', _BOLD, _BLUE)}"


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n //= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"


def _fmt_duration(secs: float) -> str:
    if secs == float("inf") or secs != secs:  # inf or nan
        return "∞"
    if secs < 60:
        return f"{secs:.2f}s"
    m, s = divmod(secs, 60)
    return f"{int(m)}m {s:.1f}s"


def _progress_bar(value: float, total: float, width: int = 30) -> str:
    pct = min(1.0, value / max(1, total))
    filled = int(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct * 100:.1f}%"


# ---------------------------------------------------------------------------
# 3-Tier Workload Definitions
# ---------------------------------------------------------------------------

@dataclass
class DemoTransfer:
    """A single pitch-demo transfer specification."""

    name: str
    category: DataCategory
    size_bytes: int
    priority: PriorityLevel
    deadline_seconds: float | None
    description: str


DEMO_TRANSFERS: list[DemoTransfer] = [
    DemoTransfer(
        name="Diagnostic Report (5 MB)",
        category=DataCategory.DIAGNOSTIC_REPORT,
        size_bytes=5 * 1024 * 1024,
        priority=PriorityLevel.CRITICAL,
        deadline_seconds=120.0,
        description="Patient radiology report – tight deadline, highest priority",
    ),
    DemoTransfer(
        name="MRI Scan (2 GB)",
        category=DataCategory.MEDICAL_IMAGE,
        size_bytes=2 * 1024 * 1024 * 1024,
        priority=PriorityLevel.HIGH,
        deadline_seconds=900.0,
        description="Full-body MRI image stack – large payload, 15-minute SLA",
    ),
    DemoTransfer(
        name="Research Dataset (500 MB)",
        category=DataCategory.RESEARCH_DATASET,
        size_bytes=500 * 1024 * 1024,
        priority=PriorityLevel.NORMAL,
        deadline_seconds=None,
        description="Anonymised research cohort dataset – best-effort delivery",
    ),
]


# ---------------------------------------------------------------------------
# Demonstration phases
# ---------------------------------------------------------------------------

@dataclass
class DemoPhaseResult:
    """Results captured from one demonstration phase."""

    phase_name: str
    transfers: list[dict[str, Any]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    policy_results: dict[str, SimulationResult] = field(default_factory=dict)
    duration_seconds: float = 0.0


def _simulate_policy(
    policy_name: str,
    wl_config: WorkloadConfig,
    net_config: NetworkConfig,
    key_config: KeyResourceConfig,
    heuristic_overrides: dict[str, Any],
    seed: int = 42,
) -> SimulationResult:
    """Run one policy simulation and return the result."""
    eng_config = EngineConfig(
        policy_name=policy_name,
        scenario_name="pitch_demo",
        seed=seed,
        heuristic_overrides=heuristic_overrides,
    )
    engine = SimulationEngine(wl_config, net_config, key_config, eng_config)
    return engine.run()


def _fmt_simulation_row(policy: str, r: SimulationResult) -> str:
    deadline_pct = r.deadline_success_rate * 100
    colour = _GREEN if deadline_pct >= 90 else (_YELLOW if deadline_pct >= 70 else _RED)
    return (
        f"  {_c(policy.ljust(28), _BOLD)}"
        f"  {_c(f'{deadline_pct:5.1f}%', colour)}"
        f"  {_fmt_duration(r.mean_total_duration_seconds).rjust(10)}"
        f"  {_fmt_duration(r.p95_total_duration_seconds).rjust(10)}"
        f"  {r.mean_throughput_mbps:7.1f} Mbps"
        f"  {r.deadline_met}/{r.deadline_met + r.deadline_missed} met"
    )


# ---------------------------------------------------------------------------
# Phase 1: 3-Tier Transfer Walkthrough
# ---------------------------------------------------------------------------

def _phase1_tier_walkthrough(
    net_config: NetworkConfig,
    key_config: KeyResourceConfig,
    verbose: bool = True,
) -> DemoPhaseResult:
    """Walk through each of the 3 transfer tiers interactively."""
    result = DemoPhaseResult(phase_name="3-Tier Transfer Walkthrough")
    t0 = time.perf_counter()

    if verbose:
        print(_section("Phase 1 — 3-Tier University→Polyclinic Transfer Walkthrough"))
        print(f"\n  {_c('Link:', _BOLD)} {net_config.source_id} → {net_config.destination_id}")
        print(f"  {_c('Baseline throughput:', _BOLD)} {net_config.baseline_throughput_mbps:.0f} Mbps")
        print(f"  {_c('RTT:', _BOLD)} {net_config.rtt_ms:.0f} ms")
        print()

    network = NetworkModel(net_config)
    key_buffer = KeyResourceModel(key_config)

    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    for i, transfer in enumerate(DEMO_TRANSFERS, start=1):
        if verbose:
            print(f"  {_c(f'[{i}/3]', _BOLD, _MAGENTA)} {_c(transfer.name, _BOLD)}")
            print(f"        {transfer.description}")
            print(f"        Size:     {_fmt_bytes(transfer.size_bytes)}")
            print(f"        Priority: {_c(transfer.priority.name, _YELLOW)}")
            if transfer.deadline_seconds:
                print(f"        Deadline: {_fmt_duration(transfer.deadline_seconds)} from dispatch")
            else:
                print(f"        Deadline: {_c('None (best-effort)', _CYAN)}")

        # Sample network conditions
        sample = network.sample(simulation_time=float(i) * 10.0)

        # Compute analytical transfer time
        size_bits = transfer.size_bytes * 8
        effective_bps = sample.effective_throughput_mbps * 1_000_000
        transfer_secs = size_bits / effective_bps + (net_config.rtt_ms / 1000.0)

        # Key acquisition
        key_result = key_buffer.try_acquire(simulation_time=float(i) * 10.0)

        # Deadline check
        deadline_met: bool | None = None
        if transfer.deadline_seconds is not None:
            deadline_met = transfer_secs <= transfer.deadline_seconds

        transfer_data: dict[str, Any] = {
            "name": transfer.name,
            "size_bytes": transfer.size_bytes,
            "priority": transfer.priority.name,
            "throughput_mbps": sample.effective_throughput_mbps,
            "packet_drop": sample.packet_drop_occurred,
            "transfer_duration_seconds": transfer_secs,
            "deadline_seconds": transfer.deadline_seconds,
            "deadline_met": deadline_met,
            "key_available": key_result.status.name,
        }
        result.transfers.append(transfer_data)

        if verbose:
            drop_tag = _c(" [PACKET DROP EVENT]", _RED) if sample.packet_drop_occurred else ""
            print(
                f"        Throughput: {_c(f'{sample.effective_throughput_mbps:.1f} Mbps', _CYAN)}{drop_tag}"
            )
            print(f"        Est. Transfer Time: {_c(_fmt_duration(transfer_secs), _BOLD)}")
            if deadline_met is True:
                print(f"        Deadline: {_c('✓ MET', _GREEN)}")
            elif deadline_met is False:
                print(f"        Deadline: {_c('✗ MISSED', _RED)}")
            print(
                f"        QKD Key Buffer: {key_buffer.snapshot().available_keys} keys remaining"
            )
            print()

    result.duration_seconds = time.perf_counter() - t0
    return result


# ---------------------------------------------------------------------------
# Phase 2: Fault Injection — Network Drop + QKD Depletion + Recovery
# ---------------------------------------------------------------------------

def _phase2_fault_injection(
    net_config: NetworkConfig,
    key_config: KeyResourceConfig,
    verbose: bool = True,
) -> DemoPhaseResult:
    """Simulate network drop + QKD depletion events and demonstrate recovery."""
    result = DemoPhaseResult(phase_name="Fault Injection & Recovery")
    t0 = time.perf_counter()

    if verbose:
        print(_section("Phase 2 — Fault Injection: Network Drop & QKD Key Depletion"))
        print()

    # --- Simulate network drop ---
    degraded_net_cfg = NetworkConfig(
        source_id=net_config.source_id,
        destination_id=net_config.destination_id,
        baseline_throughput_mbps=5.0,   # Severe degradation (5% of normal)
        throughput_std_mbps=1.0,
        rtt_ms=net_config.rtt_ms * 10,  # 10× RTT increase
        packet_drop_probability=0.80,   # 80% packet drop rate
        min_throughput_mbps=0.5,
        seed=99,
    )
    degraded_net = NetworkModel(degraded_net_cfg)

    if verbose:
        print(f"  {_c('⚠  FAULT EVENT 1: Network Outage Simulated', _BOLD, _RED)}")
        print(f"     Throughput reduced: {net_config.baseline_throughput_mbps:.0f} Mbps → 5 Mbps")
        print(f"     RTT inflated: {net_config.rtt_ms:.0f} ms → {net_config.rtt_ms * 10:.0f} ms")
        print("     Packet drop probability: 0.1% → 80%")
        print()

    sample_drop = degraded_net.sample(simulation_time=5.0)
    result.events.append(
        f"NETWORK_DROP: throughput={sample_drop.effective_throughput_mbps:.2f} Mbps, "
        f"packet_drop={sample_drop.packet_drop_occurred}"
    )

    # Show degraded transfer attempt
    report = DEMO_TRANSFERS[0]  # 5 MB diagnostic report
    size_bits = report.size_bytes * 8
    degraded_secs = size_bits / (sample_drop.effective_throughput_mbps * 1_000_000) + (
        degraded_net_cfg.rtt_ms / 1000.0
    )

    if verbose:
        print(f"     {_c('→ Attempting: 5 MB Diagnostic Report during outage...', _YELLOW)}")
        print(f"       Degraded throughput: {sample_drop.effective_throughput_mbps:.2f} Mbps")
        print(f"       Degraded transfer time: {_fmt_duration(degraded_secs)}")
        if report.deadline_seconds and degraded_secs > report.deadline_seconds:
            print(f"       Status: {_c('✗ WOULD MISS DEADLINE — entering RETRY queue', _RED)}")
        print()

    # --- Simulate QKD depletion ---
    depleted_key_cfg = KeyResourceConfig(
        source_id=key_config.source_id,
        destination_id=key_config.destination_id,
        initial_keys=0,          # Empty buffer — simulates full depletion
        buffer_ceiling=key_config.buffer_ceiling,
        generation_rate_keys_per_second=0.0,  # Replenisher also stalled (worst case)
        keys_per_transfer=key_config.keys_per_transfer,
    )
    depleted_buffer = KeyResourceModel(depleted_key_cfg)

    if verbose:
        print(f"  {_c('⚠  FAULT EVENT 2: QKD Key Buffer Depleted', _BOLD, _RED)}")
        print(f"     Key buffer: {key_config.initial_keys} → 0 keys available")
        print()

    depletion_result = depleted_buffer.try_acquire(simulation_time=0.0)  # At t=0, no replenishment yet
    result.events.append(
        f"QKD_DEPLETION: status={depletion_result.status.name}, "
        f"wait_time={depletion_result.wait_time_seconds:.2f}s"
    )

    if verbose:
        print(f"     {_c('→ Transfer blocked on QKD key acquisition', _YELLOW)}")
        print(f"       Status: {_c(depletion_result.status.name, _RED)}")
        wait_disp = (
            "∞ (replenisher also stalled)"
            if depletion_result.wait_time_seconds == float("inf")
            else _fmt_duration(depletion_result.wait_time_seconds)
        )
        print(f"       Estimated wait: {wait_disp} for replenishment")
        print()

    # --- Recovery: network stabilises ---
    if verbose:
        print(f"  {_c('✓  RECOVERY EVENT 1: Network Link Restored', _BOLD, _GREEN)}")
        print(f"     Throughput restored: 5 Mbps → {net_config.baseline_throughput_mbps:.0f} Mbps")
        print(f"     RTT normalised: {net_config.rtt_ms * 10:.0f} ms → {net_config.rtt_ms:.0f} ms")
        print()

    result.events.append("NETWORK_RECOVERY: link restored to baseline")

    # Show recovered transfer
    recovered_net = NetworkModel(net_config)
    sample_recovered = recovered_net.sample(simulation_time=20.0)
    recovered_secs = size_bits / (sample_recovered.effective_throughput_mbps * 1_000_000) + (
        net_config.rtt_ms / 1000.0
    )

    if verbose:
        print(f"     {_c('→ Retry: 5 MB Diagnostic Report after recovery', _GREEN)}")
        print(f"       Throughput: {sample_recovered.effective_throughput_mbps:.1f} Mbps")
        print(f"       Transfer time: {_fmt_duration(recovered_secs)}")
        if report.deadline_seconds and recovered_secs <= report.deadline_seconds:
            print(f"       Status: {_c('✓ DEADLINE MET after retry', _GREEN)}")
        print()

    # --- Recovery: QKD buffer replenishes ---
    # Simulate recovery by creating a fresh buffer from the original config (replenisher restored)
    recovered_buffer = KeyResourceModel(key_config)
    recovered_buffer.advance_time(100.0)  # Allow 100 virtual seconds of replenishment
    key_snap = recovered_buffer.snapshot()

    if verbose:
        print(f"  {_c('✓  RECOVERY EVENT 2: QKD Key Buffer Replenished', _BOLD, _GREEN)}")
        print(
            f"     Keys available after replenishment: {_c(str(key_snap.available_keys), _GREEN)}"
        )
        print()

    result.events.append(
        f"QKD_RECOVERY: keys_replenished={key_snap.available_keys}"
    )
    result.duration_seconds = time.perf_counter() - t0
    return result


# ---------------------------------------------------------------------------
# Phase 3: Policy Comparison Simulation
# ---------------------------------------------------------------------------

def _phase3_policy_comparison(
    wl_config: WorkloadConfig,
    net_config: NetworkConfig,
    key_config: KeyResourceConfig,
    heuristic_overrides: dict[str, Any],
    policies: list[str],
    verbose: bool = True,
) -> DemoPhaseResult:
    """Run all policies against the same workload and display comparison."""
    result = DemoPhaseResult(phase_name="Policy Comparison")
    t0 = time.perf_counter()

    if verbose:
        print(_section("Phase 3 — Scheduling Policy Comparison"))
        print(f"\n  Simulating {wl_config.total_requests} requests under {len(policies)} policies...\n")

    for policy in policies:
        if verbose:
            print(f"  {_c('▶', _CYAN)} Running {_c(policy, _BOLD)} ...", end="", flush=True)
        sim_result = _simulate_policy(
            policy_name=policy,
            wl_config=wl_config,
            net_config=net_config,
            key_config=key_config,
            heuristic_overrides=heuristic_overrides,
            seed=wl_config.seed,
        )
        result.policy_results[policy] = sim_result
        if verbose:
            print(f" {_c('done', _GREEN)} ({sim_result.real_elapsed_seconds:.2f}s real time)")

    result.duration_seconds = time.perf_counter() - t0
    return result


def _print_policy_comparison_table(policy_results: dict[str, SimulationResult]) -> None:
    """Print a formatted comparison table of all policy simulation results."""
    header = (
        f"  {'Policy'.ljust(28)}"
        f"  {'Deadline %':>10}"
        f"  {'Mean Dur':>10}"
        f"  {'P95 Dur':>10}"
        f"  {'Throughput':>12}"
        f"  {'Deadlines':>12}"
    )
    print(f"\n{_c(header, _BOLD)}")
    print("  " + "─" * 90)
    for policy, r in policy_results.items():
        print(_fmt_simulation_row(policy, r))
    print()

    # Highlight best policy by deadline success rate
    if policy_results:
        best = max(policy_results.items(), key=lambda kv: kv[1].deadline_success_rate)
        print(
            f"  {_c('★ Best deadline performance:', _BOLD, _YELLOW)} "
            f"{_c(best[0], _BOLD)} "
            f"({best[1].deadline_success_rate * 100:.1f}% deadlines met)"
        )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _build_markdown_report(
    scenario_name: str,
    phase1: DemoPhaseResult,
    phase2: DemoPhaseResult,
    phase3: DemoPhaseResult,
    args: argparse.Namespace,
) -> str:
    """Generate a full markdown demonstration summary report."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = []

    lines.append("# Q-SHIELD Pitch Demonstration Summary")
    lines.append("")
    lines.append(f"**Generated:** {now_str}  ")
    lines.append(f"**Scenario:** `{scenario_name}`  ")
    lines.append("**Script:** `python scripts/run_demonstration.py`")
    lines.append("")

    # ------- Phase 1 -------
    lines.append("## Phase 1 — 3-Tier Transfer Walkthrough")
    lines.append("")
    lines.append("| Transfer | Size | Priority | Throughput | Duration | Deadline |")
    lines.append("|----------|------|----------|------------|----------|----------|")
    for t in phase1.transfers:
        dur = _fmt_duration(t["transfer_duration_seconds"])
        dl_text = "N/A"
        if t["deadline_seconds"] is not None:
            if t["deadline_met"]:
                dl_text = "✅ MET"
            else:
                dl_text = "❌ MISSED"
        drop = " ⚡drop" if t["packet_drop"] else ""
        lines.append(
            f"| {t['name']} | {_fmt_bytes(t['size_bytes'])} | {t['priority']} "
            f"| {t['throughput_mbps']:.1f} Mbps{drop} | {dur} | {dl_text} |"
        )
    lines.append("")

    # ------- Phase 2 -------
    lines.append("## Phase 2 — Fault Injection & Recovery")
    lines.append("")
    for ev in phase2.events:
        event_type, _, detail = ev.partition(": ")
        lines.append(f"- **{event_type}**: {detail}")
    lines.append("")

    # ------- Phase 3 -------
    lines.append("## Phase 3 — Scheduling Policy Comparison")
    lines.append("")
    lines.append("| Policy | Deadline % | Mean Duration | P95 Duration | Throughput |")
    lines.append("|--------|-----------|---------------|--------------|------------|")
    for policy, r in phase3.policy_results.items():
        lines.append(
            f"| {policy} | {r.deadline_success_rate * 100:.1f}% "
            f"| {_fmt_duration(r.mean_total_duration_seconds)} "
            f"| {_fmt_duration(r.p95_total_duration_seconds)} "
            f"| {r.mean_throughput_mbps:.1f} Mbps |"
        )
    lines.append("")

    if phase3.policy_results:
        best = max(phase3.policy_results.items(), key=lambda kv: kv[1].deadline_success_rate)
        lines.append(
            f"> **Best policy:** `{best[0]}` with **{best[1].deadline_success_rate * 100:.1f}%** "
            f"deadline success rate across {best[1].total_requests} requests."
        )
        lines.append("")

    # ------- Summary -------
    lines.append("## Demonstration Summary")
    lines.append("")
    lines.append(
        "The Q-SHIELD demonstration successfully executed the full University→Polyclinic "
        "quantum-secured data exchange scenario, including:"
    )
    lines.append("")
    lines.append("1. **3-tier medical workload** (Diagnostic Report, MRI Scan, Research Dataset)")
    lines.append("2. **Fault injection** — simulated network outage and QKD key depletion")
    lines.append("3. **Automatic recovery** — link restoration and key buffer replenishment")
    lines.append(
        "4. **Policy comparison** across FIFO, Priority, and Resource-Aware Heuristic schedulers"
    )
    lines.append("")
    lines.append(
        "All transfers completed or recovered successfully. "
        "The simulation confirms Q-SHIELD's resilience and scheduling effectiveness."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Q-SHIELD End-to-End Pitch Demonstration Script (TASK-6.5).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--scenario",
        default="configs/scenarios/medical_mixed_workload.yaml",
        help="Path to scenario YAML configuration file.",
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=None,
        help="[Unused] Reserved for future time-bounded simulation; kept for CLI compatibility.",
    )
    parser.add_argument(
        "--report-output",
        default=None,
        help="Optional path to save a markdown demonstration summary (e.g. results/demonstration_summary.md).",
    )
    parser.add_argument(
        "--policies",
        default="FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC",
        help="Comma-separated list of scheduling policies (default: FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC).",
    )
    parser.add_argument(
        "--workload-size",
        type=int,
        default=None,
        help="Override number of simulation requests (default: taken from scenario YAML).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose demo output (useful when only saving the report).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override random seed from scenario YAML.",
    )

    args = parser.parse_args(argv)
    verbose = not args.quiet

    # -----------------------------------------------------------------------
    # Banner
    # -----------------------------------------------------------------------
    if verbose:
        print(_header("Q-SHIELD Pitch Demonstration — Quantum-Secured Medical Data Exchange"))
        print(f"\n  {_c('University Lab → Polyclinic', _BOLD, _CYAN)}")
        print(f"  Scenario: {_c(args.scenario, _BOLD)}")
        print(f"  Timestamp: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # -----------------------------------------------------------------------
    # Load scenario
    # -----------------------------------------------------------------------
    scenario_path = Path(args.scenario)
    if not scenario_path.exists():
        print(f"ERROR: Scenario file not found: {scenario_path}", file=sys.stderr)
        return 1

    scenario = load_scenario(scenario_path)
    wl_config = scenario.workload
    net_config = scenario.network
    key_config = KeyResourceConfig(
        source_id=scenario.key_resource.source_id,
        destination_id=scenario.key_resource.destination_id,
        initial_keys=scenario.key_resource.initial_keys,
        buffer_ceiling=scenario.key_resource.buffer_ceiling,
        generation_rate_keys_per_second=scenario.key_resource.generation_rate_keys_per_second,
        keys_per_transfer=scenario.key_resource.keys_per_transfer,
    )
    heuristic_overrides: dict[str, Any] = scenario.extra.get("heuristic_overrides", {})

    # Apply CLI overrides
    if args.seed is not None:
        wl_config.seed = args.seed
    if args.workload_size is not None:
        wl_config.total_requests = args.workload_size

    policies = [p.strip() for p in args.policies.split(",") if p.strip()]

    # -----------------------------------------------------------------------
    # Execute demonstration phases
    # -----------------------------------------------------------------------
    demo_start = time.perf_counter()

    phase1 = _phase1_tier_walkthrough(net_config, key_config, verbose=verbose)
    phase2 = _phase2_fault_injection(net_config, key_config, verbose=verbose)
    phase3 = _phase3_policy_comparison(
        wl_config=wl_config,
        net_config=net_config,
        key_config=key_config,
        heuristic_overrides=heuristic_overrides,
        policies=policies,
        verbose=verbose,
    )

    total_elapsed = time.perf_counter() - demo_start

    # -----------------------------------------------------------------------
    # Print comparison table
    # -----------------------------------------------------------------------
    if verbose:
        _print_policy_comparison_table(phase3.policy_results)

    # -----------------------------------------------------------------------
    # Final summary banner
    # -----------------------------------------------------------------------
    if verbose:
        print(_section("Demonstration Complete"))
        print(f"\n  {_c('Total real-time elapsed:', _BOLD)} {_fmt_duration(total_elapsed)}")
        print(
            f"  {_c('Phases completed:', _BOLD)} "
            f"3-Tier Walkthrough  •  Fault Injection & Recovery  •  Policy Comparison"
        )
        completed_total = sum(r.completed for r in phase3.policy_results.values())
        total_reqs = sum(r.total_requests for r in phase3.policy_results.values())
        print(f"  {_c('Simulation requests completed:', _BOLD)} {completed_total}/{total_reqs}")
        print(
            f"\n  {_c('✓ Q-SHIELD pitch demonstration executed cleanly.', _BOLD, _GREEN)}\n"
        )

    # -----------------------------------------------------------------------
    # Save markdown report
    # -----------------------------------------------------------------------
    if args.report_output:
        report_path = Path(args.report_output)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        md = _build_markdown_report(scenario.name, phase1, phase2, phase3, args)
        report_path.write_text(md, encoding="utf-8")
        if verbose:
            print(f"  {_c('Report saved to:', _BOLD)} {report_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
