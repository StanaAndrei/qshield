"""Sanity test verifying that Pytest fixtures and mock harness work cleanly."""

from collections.abc import Callable

from fastapi.testclient import TestClient

from app.core.enums import TransferStatus
from app.models.telemetry import TelemetrySnapshot
from app.models.transfer_request import TransferRequest
from tests.fixtures.mock_repository import InMemoryTransferRepository


def test_in_memory_repository(
    in_memory_repo: InMemoryTransferRepository,
    sample_transfer_request: TransferRequest,
) -> None:
    # 1. Create
    created = in_memory_repo.create_request(sample_transfer_request)
    assert created.request_id == sample_transfer_request.request_id

    # 2. Get
    fetched = in_memory_repo.get_request(created.request_id)
    assert fetched is not None
    assert fetched.source_id == "univ-lab"
    assert fetched.status == TransferStatus.SUBMITTED

    # 3. Update status & verify transition recording
    updated = in_memory_repo.update_status(
        request_id=created.request_id,
        new_status=TransferStatus.QUEUED,
        actor="test_policy_engine",
        reason="Authorization verified",
    )
    assert updated.status == TransferStatus.QUEUED

    transitions = in_memory_repo.get_transitions(created.request_id)
    assert len(transitions) == 1
    assert transitions[0].from_status == TransferStatus.SUBMITTED
    assert transitions[0].to_status == TransferStatus.QUEUED


def test_mock_telemetry_generator(
    mock_telemetry_generator: Callable[..., TelemetrySnapshot],
) -> None:
    snapshot = mock_telemetry_generator(throughput_mbps=150.0, available_keys=2000)
    assert snapshot.queue_length == 3
    assert "univ-lab->poly-clinic" in snapshot.network_metrics
    assert snapshot.network_metrics["univ-lab->poly-clinic"].observed_throughput_mbps == 150.0
    assert snapshot.qkd_metrics["univ-lab->poly-clinic"].key_buffer_available_keys == 2000


def test_fastapi_health_client(test_client: TestClient) -> None:
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["environment"] == "simulation"
