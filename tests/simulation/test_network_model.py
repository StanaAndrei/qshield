"""Tests for network variability and latency model (TASK-3.1)."""

from __future__ import annotations

from simulation.network_model import (
    NetworkConfig,
    NetworkModel,
    NetworkModelRegistry,
)


class TestNetworkModel:
    def test_sample_deterministic_with_seed(self) -> None:
        cfg1 = NetworkConfig(source_id="src", destination_id="dst", baseline_throughput_mbps=100.0, seed=42)
        cfg2 = NetworkConfig(source_id="src", destination_id="dst", baseline_throughput_mbps=100.0, seed=42)
        m1 = NetworkModel(cfg1)
        m2 = NetworkModel(cfg2)

        s1 = [m1.sample(t) for t in range(10)]
        s2 = [m2.sample(t) for t in range(10)]

        for sample1, sample2 in zip(s1, s2, strict=True):
            assert sample1.throughput_mbps == sample2.throughput_mbps
            assert sample1.effective_throughput_mbps == sample2.effective_throughput_mbps
            assert sample1.packet_drop_occurred == sample2.packet_drop_occurred

    def test_transfer_duration_calculation(self) -> None:
        cfg = NetworkConfig(
            source_id="src",
            destination_id="dst",
            baseline_throughput_mbps=100.0,
            throughput_std_mbps=0.0,
            rtt_ms=20.0,
            packet_drop_probability=0.0,
            min_throughput_mbps=10.0,
            seed=1,
        )
        model = NetworkModel(cfg)
        # 100 Mbps = 12.5 MB/s. 12.5 MB payload = 1 second transfer + 0.020s RTT = 1.020s
        duration = model.transfer_duration_seconds(12_500_000)
        assert abs(duration - 1.020) < 1e-4

    def test_from_dict(self) -> None:
        data = {
            "source_id": "lab",
            "destination_id": "clinic",
            "throughput_mbps": 50.0,
            "round_trip_time_ms": 15.0,
            "packet_loss_rate": 0.05,
        }
        model = NetworkModel.from_dict(data)
        assert model.config.source_id == "lab"
        assert model.config.destination_id == "clinic"
        assert model.config.baseline_throughput_mbps == 50.0
        assert model.config.rtt_ms == 15.0
        assert model.config.packet_drop_probability == 0.05

    def test_registry(self) -> None:
        reg = NetworkModelRegistry()
        m1 = NetworkModel(NetworkConfig("s1", "d1"))
        reg.register(m1)
        assert len(reg) == 1
        assert reg.get("s1", "d1") is m1
        assert reg.get("s2", "d2") is None
        default_m = reg.get_or_default("s2", "d2", default_throughput_mbps=200.0)
        assert default_m.config.baseline_throughput_mbps == 200.0
