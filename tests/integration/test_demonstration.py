"""Integration tests for the Q-SHIELD pitch demonstration script (TASK-6.5).

Verifies that:
- The main() function exits with code 0
- All three phases produce expected data structures
- The markdown report is generated and contains required sections
- The --quiet flag suppresses output without affecting the return code
- The --report-output flag writes a valid markdown file
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is importable regardless of invocation CWD
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_SCENARIO_PATH = _PROJECT_ROOT / "configs" / "scenarios" / "medical_mixed_workload.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_demo(argv: list[str]) -> int:
    """Run the demonstration main() function and return the exit code."""
    from scripts.run_demonstration import main

    return main(argv)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDemonstrationMain:
    """Test the main() CLI entry point."""

    def test_runs_cleanly_with_scenario_flag(self) -> None:
        """DoD: script executes with exit code 0 given a valid scenario path."""
        rc = _run_demo(["--scenario", str(_SCENARIO_PATH), "--quiet"])
        assert rc == 0, f"Expected exit code 0, got {rc}"

    def test_missing_scenario_returns_nonzero(self) -> None:
        """Script returns non-zero when scenario file does not exist."""
        rc = _run_demo(["--scenario", "nonexistent_scenario.yaml", "--quiet"])
        assert rc != 0

    def test_report_output_creates_file(self, tmp_path: Path) -> None:
        """--report-output flag creates a markdown file at the specified path."""
        report_file = tmp_path / "demo_report.md"
        rc = _run_demo([
            "--scenario", str(_SCENARIO_PATH),
            "--report-output", str(report_file),
            "--quiet",
        ])
        assert rc == 0
        assert report_file.exists(), "Markdown report file was not created"
        content = report_file.read_text(encoding="utf-8")
        assert len(content) > 100, "Report file appears empty or too short"

    def test_report_contains_required_sections(self, tmp_path: Path) -> None:
        """Generated markdown report contains all three phase sections."""
        report_file = tmp_path / "demo_report.md"
        _run_demo([
            "--scenario", str(_SCENARIO_PATH),
            "--report-output", str(report_file),
            "--quiet",
        ])
        content = report_file.read_text(encoding="utf-8")
        assert "Phase 1" in content, "Report missing Phase 1 section"
        assert "Phase 2" in content, "Report missing Phase 2 section"
        assert "Phase 3" in content, "Report missing Phase 3 section"
        assert "Q-SHIELD" in content, "Report missing Q-SHIELD header"

    def test_report_contains_policy_table(self, tmp_path: Path) -> None:
        """Generated markdown report contains a policy comparison table."""
        report_file = tmp_path / "demo_report.md"
        _run_demo([
            "--scenario", str(_SCENARIO_PATH),
            "--report-output", str(report_file),
            "--quiet",
        ])
        content = report_file.read_text(encoding="utf-8")
        assert "FIFO" in content
        assert "PRIORITY" in content
        assert "RESOURCE_AWARE_HEURISTIC" in content

    def test_custom_policies_flag(self, tmp_path: Path) -> None:
        """--policies flag restricts simulation to specified policies."""
        report_file = tmp_path / "demo_report_custom.md"
        rc = _run_demo([
            "--scenario", str(_SCENARIO_PATH),
            "--policies", "FIFO",
            "--report-output", str(report_file),
            "--quiet",
        ])
        assert rc == 0
        content = report_file.read_text(encoding="utf-8")
        assert "FIFO" in content

    def test_workload_size_override(self) -> None:
        """--workload-size flag overrides workload size from YAML."""
        rc = _run_demo([
            "--scenario", str(_SCENARIO_PATH),
            "--workload-size", "10",
            "--quiet",
        ])
        assert rc == 0

    def test_seed_override(self) -> None:
        """--seed flag overrides random seed from YAML without error."""
        rc = _run_demo([
            "--scenario", str(_SCENARIO_PATH),
            "--seed", "123",
            "--quiet",
        ])
        assert rc == 0

    def test_duration_seconds_flag_accepted(self) -> None:
        """--duration-seconds flag is accepted (reserved for future use)."""
        rc = _run_demo([
            "--scenario", str(_SCENARIO_PATH),
            "--duration-seconds", "60",
            "--quiet",
        ])
        assert rc == 0


class TestDemonstrationPhases:
    """Test individual demonstration phase functions."""

    def test_phase1_tier_walkthrough(self) -> None:
        """Phase 1 returns a DemoPhaseResult with 3 transfer records."""
        from scripts.run_demonstration import _phase1_tier_walkthrough
        from simulation.key_resource_model import KeyResourceConfig
        from simulation.scenarios import load_scenario

        scenario = load_scenario(_SCENARIO_PATH)
        key_config = KeyResourceConfig(
            source_id=scenario.key_resource.source_id,
            destination_id=scenario.key_resource.destination_id,
            initial_keys=scenario.key_resource.initial_keys,
            buffer_ceiling=scenario.key_resource.buffer_ceiling,
            generation_rate_keys_per_second=scenario.key_resource.generation_rate_keys_per_second,
            keys_per_transfer=scenario.key_resource.keys_per_transfer,
        )
        result = _phase1_tier_walkthrough(scenario.network, key_config, verbose=False)

        assert result.phase_name == "3-Tier Transfer Walkthrough"
        assert len(result.transfers) == 3, f"Expected 3 transfer records, got {len(result.transfers)}"
        for t in result.transfers:
            assert "size_bytes" in t
            assert "transfer_duration_seconds" in t
            assert t["transfer_duration_seconds"] > 0

    def test_phase2_fault_injection(self) -> None:
        """Phase 2 returns a DemoPhaseResult with at least 4 events."""
        from scripts.run_demonstration import _phase2_fault_injection
        from simulation.key_resource_model import KeyResourceConfig
        from simulation.scenarios import load_scenario

        scenario = load_scenario(_SCENARIO_PATH)
        key_config = KeyResourceConfig(
            source_id=scenario.key_resource.source_id,
            destination_id=scenario.key_resource.destination_id,
            initial_keys=scenario.key_resource.initial_keys,
            buffer_ceiling=scenario.key_resource.buffer_ceiling,
            generation_rate_keys_per_second=scenario.key_resource.generation_rate_keys_per_second,
            keys_per_transfer=scenario.key_resource.keys_per_transfer,
        )
        result = _phase2_fault_injection(scenario.network, key_config, verbose=False)

        assert result.phase_name == "Fault Injection & Recovery"
        assert len(result.events) >= 4, f"Expected at least 4 events, got {len(result.events)}"
        event_names = " ".join(result.events)
        assert "NETWORK_DROP" in event_names
        assert "QKD_DEPLETION" in event_names
        assert "NETWORK_RECOVERY" in event_names
        assert "QKD_RECOVERY" in event_names

    def test_phase3_policy_comparison(self) -> None:
        """Phase 3 returns simulation results for all requested policies."""
        from scripts.run_demonstration import _phase3_policy_comparison
        from simulation.key_resource_model import KeyResourceConfig
        from simulation.scenarios import load_scenario

        scenario = load_scenario(_SCENARIO_PATH)
        key_config = KeyResourceConfig(
            source_id=scenario.key_resource.source_id,
            destination_id=scenario.key_resource.destination_id,
            initial_keys=scenario.key_resource.initial_keys,
            buffer_ceiling=scenario.key_resource.buffer_ceiling,
            generation_rate_keys_per_second=scenario.key_resource.generation_rate_keys_per_second,
            keys_per_transfer=scenario.key_resource.keys_per_transfer,
        )
        policies = ["FIFO", "PRIORITY", "RESOURCE_AWARE_HEURISTIC"]
        result = _phase3_policy_comparison(
            wl_config=scenario.workload,
            net_config=scenario.network,
            key_config=key_config,
            heuristic_overrides=scenario.extra.get("heuristic_overrides", {}),
            policies=policies,
            verbose=False,
        )

        assert result.phase_name == "Policy Comparison"
        assert set(result.policy_results.keys()) == set(policies)
        for policy, sim_result in result.policy_results.items():
            assert sim_result.total_requests > 0
            assert sim_result.completed >= 0
            assert 0.0 <= sim_result.deadline_success_rate <= 1.0


class TestHelperFunctions:
    """Test utility/helper functions."""

    def test_fmt_bytes(self) -> None:
        from scripts.run_demonstration import _fmt_bytes

        assert "5.0 MB" in _fmt_bytes(5 * 1024 * 1024)
        assert "2.0 GB" in _fmt_bytes(2 * 1024 * 1024 * 1024)

    def test_fmt_duration_seconds(self) -> None:
        from scripts.run_demonstration import _fmt_duration

        assert "0.44s" in _fmt_duration(0.44)
        assert "1m" in _fmt_duration(90.0)

    def test_fmt_duration_infinity(self) -> None:
        from scripts.run_demonstration import _fmt_duration

        assert _fmt_duration(float("inf")) == "∞"

    def test_progress_bar_full(self) -> None:
        from scripts.run_demonstration import _progress_bar

        bar = _progress_bar(100, 100)
        assert "100.0%" in bar

    def test_progress_bar_empty(self) -> None:
        from scripts.run_demonstration import _progress_bar

        bar = _progress_bar(0, 100)
        assert "0.0%" in bar
