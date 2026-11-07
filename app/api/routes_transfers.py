"""FastAPI transfer orchestration endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.schemas import ApiResponse
from app.core.enums import PriorityLevel, TransferStatus
from app.core.exceptions import TransferNotFoundError
from app.core.state_machine import TransferStateMachine
from app.database.connection import get_db
from app.database.repository import ITransferRepository, SqliteTransferRepository
from app.models.transfer_request import (
    TransferRequest,
    TransferRequestCreate,
)
from app.security.policy_engine import SecurityPolicyEngine

router = APIRouter()
_policy_engine = SecurityPolicyEngine()


def get_repository(session: Session = Depends(get_db)) -> ITransferRepository:
    """Dependency provider for transfer repository."""
    return SqliteTransferRepository(session=session)


@router.post(
    "",
    response_model=ApiResponse[TransferRequest],
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new transfer request",
    description="Submits transfer request, assigns UUID, transitions SUBMITTED -> VALIDATING -> QUEUED (or REJECTED).",
)
async def create_transfer(
    payload: TransferRequestCreate,
    repo: ITransferRepository = Depends(get_repository),
) -> ApiResponse[TransferRequest]:
    """Submits transfer request, performs security validation, and queues or rejects request."""
    # 1. Instantiate domain model in SUBMITTED status
    req = TransferRequest(
        request_id=uuid4(),
        source_id=payload.source_id,
        destination_id=payload.destination_id,
        data_size_bytes=payload.data_size_bytes,
        priority=payload.priority,
        submitted_at=datetime.now(UTC),
        deadline=payload.deadline,
        security_profile=payload.security_profile,
        data_category=payload.data_category,
        status=TransferStatus.SUBMITTED,
        metadata=payload.metadata,
    )
    saved_req = repo.create_request(req)

    # 2. Transition: SUBMITTED -> VALIDATING
    TransferStateMachine.transition(
        saved_req,
        TransferStatus.VALIDATING,
        actor="api_transfers",
        reason="Evaluating deterministic security policy",
    )
    repo.update_status(
        saved_req.request_id,
        TransferStatus.VALIDATING,
        actor="api_transfers",
        reason="Validation started",
    )

    # 3. Deterministic Policy Gate via SecurityPolicyEngine
    decision = _policy_engine.evaluate_request(saved_req)

    if not decision.allowed:
        TransferStateMachine.transition(
            saved_req,
            TransferStatus.REJECTED,
            actor="policy_engine",
            reason=decision.reason_code,
        )
        repo.update_status(
            saved_req.request_id,
            TransferStatus.REJECTED,
            actor="policy_engine",
            reason=decision.reason_code,
        )
        if hasattr(repo, "record_policy_decision"):
            repo.record_policy_decision(decision)
    else:
        TransferStateMachine.transition(
            saved_req,
            TransferStatus.QUEUED,
            actor="policy_engine",
            reason=decision.reason_code,
        )
        repo.update_status(
            saved_req.request_id,
            TransferStatus.QUEUED,
            actor="policy_engine",
            reason=decision.reason_code,
        )
        if hasattr(repo, "record_policy_decision"):
            repo.record_policy_decision(decision)

    return ApiResponse(data=saved_req)


@router.get(
    "/{request_id}",
    response_model=ApiResponse[TransferRequest],
    summary="Get transfer request by ID",
    description="Retrieves transfer request metadata, lifecycle state, timestamps, and checksum.",
)
async def get_transfer(
    request_id: UUID,
    repo: ITransferRepository = Depends(get_repository),
) -> ApiResponse[TransferRequest]:
    """Retrieves single transfer request by UUID."""
    req = repo.get_request(request_id)
    if req is None:
        raise TransferNotFoundError(
            f"Transfer request {request_id} not found",
            code="TRANSFER_NOT_FOUND",
        )
    return ApiResponse(data=req)


@router.get(
    "",
    response_model=ApiResponse[list[TransferRequest]],
    summary="List transfer requests",
    description="Lists transfer requests with optional status and priority filtering and pagination.",
)
async def list_transfers(
    status: TransferStatus | None = Query(default=None, description="Filter by lifecycle status"),
    priority: PriorityLevel | None = Query(default=None, description="Filter by operational priority"),
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum items to return"),
    offset: int = Query(default=0, ge=0, description="Offset items for pagination"),
    repo: ITransferRepository = Depends(get_repository),
) -> ApiResponse[list[TransferRequest]]:
    """Lists requests matching filter criteria."""
    items = repo.list_requests(status=status, priority=priority, limit=limit, offset=offset)
    return ApiResponse(data=items)


@router.post(
    "/{request_id}/cancel",
    response_model=ApiResponse[TransferRequest],
    summary="Cancel transfer request",
    description="Cancels queued or pending request if not already in terminal status.",
)
async def cancel_transfer(
    request_id: UUID,
    repo: ITransferRepository = Depends(get_repository),
) -> ApiResponse[TransferRequest]:
    """Cancels a pending or queued transfer request."""
    req = repo.get_request(request_id)
    if req is None:
        raise TransferNotFoundError(
            f"Transfer request {request_id} not found",
            code="TRANSFER_NOT_FOUND",
        )

    # Validate and transition via state machine
    TransferStateMachine.transition(
        req,
        TransferStatus.CANCELLED,
        actor="api_client",
        reason="Client requested cancellation",
    )
    updated = repo.update_status(
        request_id,
        TransferStatus.CANCELLED,
        actor="api_client",
        reason="Client requested cancellation",
    )
    return ApiResponse(data=updated)


__all__ = ["get_repository", "router"]
