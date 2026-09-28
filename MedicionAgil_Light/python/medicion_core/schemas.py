"""Typed contracts shared by the sidecar and frontend."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetMetadata(ApiModel):
    dataset_id: str
    name: str
    backend: str
    rows: int
    columns: list[str]
    types: list[str]
    uses_disk: bool
    source_path: str | None = None


class FilterSpec(ApiModel):
    column: str
    values: list[Any]


class SortSpec(ApiModel):
    column: str
    direction: Literal["asc", "desc"] = "asc"


class TablePage(ApiModel):
    dataset_id: str
    offset: int
    limit: int
    total_rows: int
    columns: list[str]
    rows: list[dict[str, Any]]


class AnalysisManifest(ApiModel):
    id: str
    version: str = "1.0.0"
    name: str
    description: str
    category: str
    parameter_schema: dict[str, Any] | None = None
    supports_cancel: bool = True
    supports_progress: bool = True
    needs_full_dataframe: bool = True
    supports_lazy_dataset: bool = False
    recommended_for: str | None = None
    caution: str | None = None


class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobStatus(ApiModel):
    job_id: str
    type: str
    state: JobState
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress: int = Field(default=0, ge=0, le=100)
    message: str = ""
    result_id: str | None = None
    error: dict[str, Any] | None = None
    diagnostics: list[str] = Field(default_factory=list)


class ResultSummary(ApiModel):
    result_id: str
    analysis_id: str
    tables: list[str]
    charts: list[str]
    scalars: dict[str, Any]
