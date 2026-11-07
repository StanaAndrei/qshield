"""Abstract interface contract for transfer executors in Q-SHIELD."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.scheduling import TransferExecutionPlan, TransferResult


class ITransferExecutor(ABC):
    """Handles data transmission, cryptographic encapsulation, and integrity checking."""

    @abstractmethod
    def execute(self, plan: TransferExecutionPlan) -> TransferResult:
        """Performs data transfer and receiver-side integrity verification.

        Args:
            plan: The TransferExecutionPlan detailing endpoints, payload size,
                  allocated bandwidth, and security profiles.

        Returns:
            TransferResult summarizing execution outcome, actual throughput,
            calculated duration, and SHA-256 verification status.
        """
        pass


__all__ = ["ITransferExecutor"]
