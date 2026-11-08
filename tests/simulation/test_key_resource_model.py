"""Tests for key resource model (TASK-3.2)."""

from __future__ import annotations

from simulation.key_resource_model import (
    KeyAcquisitionStatus,
    KeyResourceConfig,
    KeyResourceModel,
)


class TestKeyResourceModel:
    def test_initial_state_and_acquisition(self) -> None:
        cfg = KeyResourceConfig(
            source_id="src",
            destination_id="dst",
            initial_keys=10,
            buffer_ceiling=20,
            generation_rate_keys_per_second=2.0,
            keys_per_transfer=2,
        )
        model = KeyResourceModel(cfg)
        assert model.available_keys == 10
        assert not model.is_depleted

        res = model.try_acquire(simulation_time=0.0)
        assert res.status == KeyAcquisitionStatus.SUCCESS
        assert res.keys_consumed == 2
        assert res.keys_remaining == 8
        assert res.wait_time_seconds == 0.0

    def test_depletion_and_wait_time(self) -> None:
        cfg = KeyResourceConfig(
            source_id="src",
            destination_id="dst",
            initial_keys=1,
            buffer_ceiling=10,
            generation_rate_keys_per_second=2.0,
            keys_per_transfer=5,
        )
        model = KeyResourceModel(cfg)
        res = model.try_acquire(simulation_time=0.0)
        assert res.status == KeyAcquisitionStatus.INSUFFICIENT_KEYS
        assert res.keys_consumed == 0
        assert res.keys_remaining == 1
        # Deficit is 5 - 1 = 4. Rate is 2.0 keys/sec -> wait = 2.0s
        assert abs(res.wait_time_seconds - 2.0) < 1e-6
        assert model.snapshot().depletion_events == 1

    def test_replenishment_advances(self) -> None:
        cfg = KeyResourceConfig(
            source_id="src",
            destination_id="dst",
            initial_keys=0,
            buffer_ceiling=10,
            generation_rate_keys_per_second=5.0,
            keys_per_transfer=1,
        )
        model = KeyResourceModel(cfg)
        # Empty buffer initially
        res = model.try_acquire(0.0)
        assert res.status == KeyAcquisitionStatus.BUFFER_EMPTY

        # After 1.0 second, 5 keys generated
        added = model.advance_time(1.0)
        assert added == 5
        assert model.available_keys == 5

        # Ceiling respected
        model.advance_time(3.0)  # total 15 keys would have generated, capped at 10
        assert model.available_keys == 10

    def test_release_and_reset(self) -> None:
        cfg = KeyResourceConfig(
            source_id="src",
            destination_id="dst",
            initial_keys=5,
            buffer_ceiling=10,
            generation_rate_keys_per_second=1.0,
            keys_per_transfer=3,
        )
        model = KeyResourceModel(cfg)
        model.try_acquire(0.0)
        assert model.available_keys == 2

        model.release(2, simulation_time=0.0)
        assert model.available_keys == 4

        model.reset()
        assert model.available_keys == 5
