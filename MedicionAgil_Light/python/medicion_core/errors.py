"""Domain errors crossing the Python sidecar boundary."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code = "APP_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message,
                "details": self.details}


class DataValidationError(AppError):
    code = "DATA_VALIDATION_ERROR"


class AnalysisValidationError(AppError):
    code = "ANALYSIS_VALIDATION_ERROR"


class AnalysisExecutionError(AppError):
    code = "ANALYSIS_EXECUTION_ERROR"


class DependencyError(AppError):
    code = "DEPENDENCY_ERROR"


class MemoryBudgetError(AppError):
    code = "MEMORY_BUDGET_ERROR"


class PersistenceError(AppError):
    code = "PERSISTENCE_ERROR"


class UserInputError(AppError):
    code = "USER_INPUT_ERROR"


class ExportError(AppError):
    code = "EXPORT_ERROR"


class SidecarError(AppError):
    code = "SIDECAR_ERROR"


class InternalAppError(AppError):
    code = "INTERNAL_APP_ERROR"
