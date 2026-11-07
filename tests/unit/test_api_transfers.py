"""Unit and integration tests for FastAPI transfer endpoints (TASK-1.7)."""

from __future__ import annotations

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.routes_transfers import get_repository
from app.core.enums import TransferStatus
from app.database.schema import Base
from app.main import app
from tests.fixtures.mock_repository import InMemoryTransferRepository


@pytest.fixture
def api_client(sqlite_engine: Engine) -> Generator[TestClient, None, None]:
    """Provides a TestClient with initialized database tables and repository override."""
    Base.metadata.create_all(bind=sqlite_engine)
    in_memory_repo = InMemoryTransferRepository()
    app.dependency_overrides[get_repository] = lambda: in_memory_repo
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_post_transfer_returns_201_created(api_client: TestClient) -> None:
    """Verifies TASK-1.7 DoD: HTTP client can POST a transfer and receive a 201 Created with valid JSON matching ApiResponse[TransferRequest]."""
    payload = {
        "source_id": "univ-lab",
        "destination_id": "poly-clinic",
        "data_size_bytes": 5_242_880,
        "priority": 20,
        "security_profile": "QKD_AES_256_GCM",
        "data_category": "DIAGNOSTIC_REPORT",
        "metadata": {"test_case": "tracer_bullet_post"},
    }

    response = api_client.post("/api/v1/transfers", json=payload)
    assert response.status_code == 201

    body = response.json()
    assert body["success"] is True
    assert "timestamp" in body
    data = body["data"]

    assert "request_id" in data
    assert data["source_id"] == "univ-lab"
    assert data["destination_id"] == "poly-clinic"
    assert data["data_size_bytes"] == 5_242_880
    assert data["priority"] == 20
    assert data["status"] == TransferStatus.QUEUED
    assert data["security_profile"] == "QKD_AES_256_GCM"


def test_get_transfer_by_id(api_client: TestClient) -> None:
    """Verifies retrieving an existing transfer by UUID returns 200 OK with ApiResponse[TransferRequest]."""
    # 1. Create transfer
    create_payload = {
        "source_id": "univ-lab",
        "destination_id": "poly-clinic",
        "data_size_bytes": 1024,
        "priority": 10,
    }
    create_res = api_client.post("/api/v1/transfers", json=create_payload)
    assert create_res.status_code == 201
    request_id = create_res.json()["data"]["request_id"]

    # 2. Get transfer
    get_res = api_client.get(f"/api/v1/transfers/{request_id}")
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["success"] is True
    assert body["data"]["request_id"] == request_id
    assert body["data"]["source_id"] == "univ-lab"


def test_get_transfer_not_found_returns_404(api_client: TestClient) -> None:
    """Verifies that requesting an unknown UUID returns 404 with standard ApiErrorEnvelope."""
    unknown_id = uuid4()
    response = api_client.get(f"/api/v1/transfers/{unknown_id}")
    assert response.status_code == 404

    body = response.json()
    assert body["success"] is False
    assert len(body["errors"]) == 1
    assert body["errors"][0]["code"] == "TRANSFER_NOT_FOUND"


def test_list_transfers_with_filters(api_client: TestClient) -> None:
    """Verifies listing transfers with pagination and status filtering."""
    for i in range(5):
        api_client.post(
            "/api/v1/transfers",
            json={
                "source_id": "univ-lab",
                "destination_id": "poly-clinic",
                "data_size_bytes": 2048 * (i + 1),
                "priority": 30 if i == 0 else 20,
            },
        )

    # List all
    res = api_client.get("/api/v1/transfers")
    assert res.status_code == 200
    items = res.json()["data"]
    assert len(items) == 5

    # Filter by priority
    res_filtered = api_client.get("/api/v1/transfers?priority=30")
    assert res_filtered.status_code == 200
    assert len(res_filtered.json()["data"]) == 1


def test_cancel_transfer_endpoint(api_client: TestClient) -> None:
    """Verifies cancelling a queued transfer request transitions it to CANCELLED."""
    create_res = api_client.post(
        "/api/v1/transfers",
        json={
            "source_id": "univ-lab",
            "destination_id": "poly-clinic",
            "data_size_bytes": 4096,
        },
    )
    request_id = create_res.json()["data"]["request_id"]

    cancel_res = api_client.post(f"/api/v1/transfers/{request_id}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["data"]["status"] == TransferStatus.CANCELLED


def test_phi_guard_rejection_returns_422(api_client: TestClient) -> None:
    """Verifies that forbidden patient identifiers in metadata are rejected with 422."""
    forbidden_payload = {
        "source_id": "univ-lab",
        "destination_id": "poly-clinic",
        "data_size_bytes": 1024,
        "metadata": {"patient_name": "Jane Doe", "mrn": "123456"},
    }
    response = api_client.post("/api/v1/transfers", json=forbidden_payload)
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert any("patient_name" in err["message"] or "metadata" in err.get("field", "") for err in body["errors"])


def test_policy_rejection_for_unauthorized_node(api_client: TestClient) -> None:
    """Verifies that an unauthorized node request is rejected into REJECTED status."""
    payload = {
        "source_id": "unauthorized-node",
        "destination_id": "poly-clinic",
        "data_size_bytes": 1024,
    }
    response = api_client.post("/api/v1/transfers", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["status"] == TransferStatus.REJECTED


def test_api_health_endpoint(api_client: TestClient) -> None:
    """Verifies versioned /api/v1/health probe returns ApiResponse envelope."""
    response = api_client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "healthy"
    assert "version" in body["data"]
