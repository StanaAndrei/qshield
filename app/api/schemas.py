"""API request and response schemas and envelope wrappers for Q-SHIELD."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


def utc_now() -> datetime:
    return datetime.now(UTC)


class ApiResponse(BaseModel, Generic[T]):
    """Standard success API response envelope."""

    success: bool = True
    data: T
    timestamp: datetime = Field(default_factory=utc_now)


class ApiErrorDetail(BaseModel):
    """Machine and human readable error specification."""

    code: str
    message: str
    field: str | None = None


class ApiErrorEnvelope(BaseModel):
    """Standard error API response envelope."""

    success: bool = False
    errors: list[ApiErrorDetail]
    timestamp: datetime = Field(default_factory=utc_now)


__all__ = [
    "ApiErrorDetail",
    "ApiErrorEnvelope",
    "ApiResponse",
    "utc_now",
]
