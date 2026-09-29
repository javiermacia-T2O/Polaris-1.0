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
    # True while ``rows`` is a provisional value: a full COUNT(*) over a
    # multi-gigabyte source is computed in the background and the UI polls
    # until the exact total arrives.
    rows_approximate: bool = False


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
    approximate: bool = False
    # True while ``total_rows`` is provisional (a huge source is still being
    # counted in the background); the UI polls until the exact total arrives.
    total_rows_approximate: bool = False


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
    table_format: "TableFormat | None" = None


class TableFormat(ApiModel):
    """Estructura de tabla que un análisis necesita para ejecutarse.

    Cada análisis declara aquí las variables que requiere (temporal,
    dimensiones obligatorias/opcionales, métricas/KPIs e inversión) y los
    roles del constructor (Fila/Columna/Valor) que debe cumplir la tabla.
    """

    summary: str
    temporal: list[str] = []
    required_dimensions: list[str] = []
    optional_dimensions: list[str] = []
    metrics: list[str] = []
    investment: list[str] = []
    rows: list[str] = []
    columns: list[str] = []
    values: list[str] = []
    requires_pivot: bool = False
    notes: str | None = None


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
