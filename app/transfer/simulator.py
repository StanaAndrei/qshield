"""Synthetic data generation and network transfer simulator."""

from __future__ import annotations

import time
from collections.abc import Generator

DEFAULT_CHUNK_SIZE: int = 65536  # 64 KB


def generate_synthetic_buffer(size_bytes: int, seed: int = 42) -> bytes:
    """Generates a deterministic synthetic byte buffer without PHI content.

    Args:
        size_bytes: Exact byte size of the payload to generate.
        seed: Deterministic integer seed for reproducible byte content.

    Returns:
        Bytes buffer of exact length size_bytes.
    """
    if size_bytes <= 0:
        return b""

    pattern = f"QSHIELD_SYNTHETIC_MEDICAL_PAYLOAD_SEED_{seed}_SAFE_DATA_".encode("ascii")
    repeats = (DEFAULT_CHUNK_SIZE // len(pattern)) + 1
    chunk = (pattern * repeats)[:DEFAULT_CHUNK_SIZE]

    full_chunks = size_bytes // DEFAULT_CHUNK_SIZE
    remainder = size_bytes % DEFAULT_CHUNK_SIZE

    parts = [chunk] * full_chunks
    if remainder > 0:
        parts.append(chunk[:remainder])

    return b"".join(parts)


def generate_synthetic_stream(
    size_bytes: int,
    seed: int = 42,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Generator[bytes, None, None]:
    """Yields deterministic synthetic byte chunks up to size_bytes."""
    pattern = f"QSHIELD_SYNTHETIC_MEDICAL_PAYLOAD_SEED_{seed}_SAFE_DATA_".encode("ascii")
    repeats = (chunk_size // len(pattern)) + 1
    base_chunk = (pattern * repeats)[:chunk_size]

    remaining = size_bytes
    while remaining > 0:
        send_bytes = min(remaining, chunk_size)
        yield base_chunk[:send_bytes]
        remaining -= send_bytes


def calculate_transmission_duration(
    size_bytes: int,
    bandwidth_mbps: float,
    latency_ms: float = 0.0,
) -> float:
    """Calculates theoretical transmission duration in seconds."""
    bw = max(0.001, bandwidth_mbps)
    transfer_seconds = (size_bytes * 8.0) / (bw * 1_000_000.0)
    propagation_seconds = max(0.0, latency_ms) / 1000.0
    return transfer_seconds + propagation_seconds


class SyntheticTransferSimulator:
    """Simulates network transport dynamics, bandwidth constraints, and payload corruption."""

    def __init__(
        self,
        default_bandwidth_mbps: float = 800.0,
        default_latency_ms: float = 10.0,
        enable_real_sleep: bool = False,
        max_real_sleep_seconds: float = 0.05,
    ) -> None:
        self.default_bandwidth_mbps = default_bandwidth_mbps
        self.default_latency_ms = default_latency_ms
        self.enable_real_sleep = enable_real_sleep
        self.max_real_sleep_seconds = max_real_sleep_seconds

    def simulate_transfer(
        self,
        payload: bytes,
        bandwidth_mbps: float | None = None,
        latency_ms: float | None = None,
        simulate_corruption: bool = False,
    ) -> tuple[bytes, float, float]:
        """Simulates network transport of a byte buffer.

        Args:
            payload: Origin payload bytes to transfer.
            bandwidth_mbps: Allocated throughput limit (defaults to simulator default).
            latency_ms: Roundtrip propagation latency in milliseconds.
            simulate_corruption: If True, artificially corrupts received data for integrity testing.

        Returns:
            Tuple of (received_bytes, actual_duration_seconds, effective_throughput_mbps).
        """
        bw = bandwidth_mbps if bandwidth_mbps is not None and bandwidth_mbps > 0.0 else self.default_bandwidth_mbps
        lat = latency_ms if latency_ms is not None and latency_ms >= 0.0 else self.default_latency_ms

        size = len(payload)
        calc_duration = calculate_transmission_duration(size, bw, lat)

        # Optional real CPU sleep for demo/cli throttle
        if self.enable_real_sleep and calc_duration > 0.0:
            sleep_time = min(calc_duration, self.max_real_sleep_seconds)
            time.sleep(sleep_time)

        # Calculate effective throughput
        effective_throughput = (size * 8.0) / (calc_duration * 1_000_000.0) if calc_duration > 0.0 else bw

        if simulate_corruption and len(payload) > 0:
            # Inject bit corruption into the received data stream
            corrupted = bytearray(payload)
            corrupted[-1] ^= 0xFF  # Flip all bits in the final byte
            return bytes(corrupted), calc_duration, effective_throughput

        return payload, calc_duration, effective_throughput


__all__ = [
    "SyntheticTransferSimulator",
    "calculate_transmission_duration",
    "generate_synthetic_buffer",
    "generate_synthetic_stream",
]
