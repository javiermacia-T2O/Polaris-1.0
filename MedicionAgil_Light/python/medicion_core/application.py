"""Typed application service used by Tkinter today and Tauri next."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import importlib
from pathlib import Path
import re
import threading
import uuid
import platform
import sys
from typing import Any

import pandas as pd

from core.plugin_loader import discover_analyses, load_analysis
from services.active_dataset import ActiveDataset
from services.analysis_service import run_analysis as execute_analysis
from services.data_service import read_dataset

from .errors import (AnalysisExecutionError, AnalysisValidationError,
                     DataValidationError, ExportError, MemoryBudgetError,
                     UserInputError)
from .jobs import JobManager
from .schemas import (AnalysisManifest, DatasetMetadata, FilterSpec, JobStatus,
                      ResultSummary, SortSpec, TablePage)
from .serialization import frame_records, scalar_value


@dataclass
class _DatasetEntry:
    dataset_id: str
    name: str
    original: ActiveDataset
    active: ActiveDataset
    source_path: Path | None


@dataclass
class _ResultEntry:
    result_id: str
    analysis_id: str
    value: dict[str, Any]


class MedicionApplication:
    """Owns lightweight handles; heavy data remains in Python/DuckDB."""

    def __init__(self, *, max_workers: int = 2):
        self._datasets: dict[str, _DatasetEntry] = {}
        self._results: dict[str, _ResultEntry] = {}
        self._lock = threading.RLock()
        self.jobs = JobManager(max_workers=max_workers)
        self._analyses = {_analysis_id(item): item
                          for item in discover_analyses()}

    def load_dataset(self, path: str | Path) -> DatasetMetadata:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise DataValidationError(
                "El archivo no existe.", details={"path": str(source)})
        try:
            loaded = read_dataset(source, 0, lambda _message: None)
            dataset = (loaded if isinstance(loaded, ActiveDataset)
                       else ActiveDataset.from_frame(loaded))
            dataset_id = uuid.uuid4().hex
            entry = _DatasetEntry(dataset_id, source.stem, dataset, dataset,
                                  source)
            with self._lock:
                self._datasets[dataset_id] = entry
            return self.get_dataset_metadata(dataset_id)
        except MemoryError as exc:
            raise MemoryBudgetError(str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise DataValidationError(
                "No se pudo cargar el dataset.",
                details={"path": str(source), "reason": str(exc)}) from exc

    def close_dataset(self, dataset_id: str) -> None:
        with self._lock:
            if self._datasets.pop(dataset_id, None) is None:
                raise DataValidationError("Dataset no encontrado.",
                                          details={"dataset_id": dataset_id})

    def list_datasets(self) -> list[DatasetMetadata]:
        with self._lock:
            ids = list(self._datasets)
        return [self.get_dataset_metadata(dataset_id) for dataset_id in ids]

    def get_dataset_metadata(self, dataset_id: str) -> DatasetMetadata:
        entry = self._dataset(dataset_id)
        try:
            rows = entry.active.row_count()
        except MemoryError as exc:
            raise MemoryBudgetError(str(exc)) from exc
        status = entry.active.resource_status()
        columns = list(entry.active.projection or entry.active.columns)
        type_map = dict(zip(entry.active.columns, entry.active.types))
        return DatasetMetadata(
            dataset_id=dataset_id, name=entry.name,
            backend=entry.active.backend, rows=rows, columns=columns,
            types=[type_map.get(column, "") for column in columns],
            uses_disk=bool(status["uses_disk"]),
            source_path=str(entry.source_path) if entry.source_path else None)

    def get_columns(self, dataset_id: str) -> list[dict[str, str]]:
        metadata = self.get_dataset_metadata(dataset_id)
        return [{"name": name, "type": dtype}
                for name, dtype in zip(metadata.columns, metadata.types)]

    def get_column_profile(self, dataset_id: str, column: str) -> dict[str, Any]:
        entry = self._dataset(dataset_id)
        if column not in entry.active.columns:
            raise UserInputError("Columna no encontrada.",
                                 details={"column": column})
        sample = entry.active.with_columns([column]).preview(limit=1000)
        series = sample[column]
        return {
            "column": column,
            "sample_size": len(series),
            "nulls": int(series.isna().sum()),
            "unique_sample": int(series.nunique(dropna=True)),
            "numeric": bool(pd.api.types.is_numeric_dtype(series)),
            "examples": [scalar_value(item) for item in
                         series.dropna().drop_duplicates().head(20)],
        }

    def get_diagnostics(self) -> dict[str, Any]:
        from core.memory_budget import system_memory
        from .observability import log_path

        memory = system_memory()
        packages = {}
        imports = {}
        for distribution in (
                "pandas", "numpy", "scipy", "scikit-learn", "statsmodels",
                "duckdb", "pyarrow", "matplotlib", "pycausalimpact",
                "meridian-geox"):
            try:
                packages[distribution] = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                packages[distribution] = None
        for module_name in (
                "pandas", "numpy", "scipy", "sklearn", "statsmodels",
                "duckdb", "pyarrow", "matplotlib", "causalimpact",
                "meridian_geox"):
            try:
                importlib.import_module(module_name)
                imports[module_name] = "ok"
            except Exception as exc:
                imports[module_name] = f"{type(exc).__name__}: {exc}"
        with self._lock:
            backends = {entry.active.backend
                        for entry in self._datasets.values()}
        return {
            "app_version": "0.1.0", "sidecar_protocol": 1,
            "python": sys.version.split()[0], "platform": platform.platform(),
            "packages": packages,
            "dependency_imports": imports,
            "memory": {"total_bytes": memory.total,
                       "available_bytes": memory.available},
            "dataset_backends": sorted(backends),
            "jobs": [job.model_dump(mode="json") for job in self.jobs.list()],
            "log_path": str(log_path()),
        }

    def apply_filters(self, dataset_id: str,
                      filters: list[FilterSpec | dict[str, Any]]) -> DatasetMetadata:
        entry = self._dataset(dataset_id)
        specs = [item if isinstance(item, FilterSpec)
                 else FilterSpec.model_validate(item) for item in filters]
        requested = {item.column: item.values for item in specs}
        try:
            entry.active = entry.original.with_filters(requested)
        except KeyError as exc:
            raise UserInputError("Una columna de filtro no existe.",
                                 details={"column": str(exc)}) from exc
        return self.get_dataset_metadata(dataset_id)

    def reset_filters(self, dataset_id: str) -> DatasetMetadata:
        entry = self._dataset(dataset_id)
        entry.active = entry.original
        return self.get_dataset_metadata(dataset_id)

    def get_table_preview(self, dataset_id: str, offset: int = 0,
                          limit: int = 200) -> TablePage:
        return self.get_table_page(dataset_id, offset=offset, limit=limit)

    def get_table_page(self, dataset_id: str, offset: int = 0,
                       limit: int = 200,
                       sort: list[SortSpec | dict[str, Any]] | None = None
                       ) -> TablePage:
        if limit < 1 or limit > 1000 or offset < 0:
            raise UserInputError("La página debe contener entre 1 y 1000 filas.")
        entry = self._dataset(dataset_id)
        sorts = [item if isinstance(item, SortSpec)
                 else SortSpec.model_validate(item) for item in (sort or [])]
        try:
            frame = entry.active.page(
                limit=limit, offset=offset,
                sort=[(item.column, item.direction == "asc") for item in sorts])
        except KeyError as exc:
            raise UserInputError("Columna de orden no encontrada.",
                                 details={"column": str(exc)}) from exc
        return TablePage(
            dataset_id=dataset_id, offset=offset, limit=limit,
            total_rows=entry.active.row_count(),
            columns=[str(column) for column in frame.columns],
            rows=frame_records(frame))

    def build_table(self, dataset_id: str, recipe: dict[str, Any]) -> str:
        from models.table_recipe import TableRecipe
        from services.table_service import build_table_view

        entry = self._dataset(dataset_id)
        try:
            table_recipe = TableRecipe.from_parts(
                rows=recipe.get("rows", ()), cols=recipe.get("columns", ()),
                val_specs=recipe.get("values", ()),
                filters=recipe.get("filters"),
                pivot=recipe.get("pivot", True),
                selected=recipe.get("selected", ()),
                order=recipe.get("order", ()),
                col_types=recipe.get("column_types"))
            view = build_table_view(entry.active, table_recipe)
            table_id = uuid.uuid4().hex
            with self._lock:
                self._datasets[table_id] = _DatasetEntry(
                    table_id, f"{entry.name} · tabla", view, view, None)
            return table_id
        except (KeyError, ValueError) as exc:
            raise UserInputError("La receta de tabla no es válida.",
                                 details={"reason": str(exc)}) from exc

    def list_analyses(self) -> list[AnalysisManifest]:
        return [self.get_analysis_manifest(analysis_id)
                for analysis_id in self._analyses]

    def get_analysis_manifest(self, analysis_id: str,
                              dataset_id: str | None = None) -> AnalysisManifest:
        item = self._analysis(analysis_id)
        parameter_schema = None
        if dataset_id is not None:
            dataset = self._dataset(dataset_id).active
            module = load_analysis(item)
            schema_factory = getattr(module, "get_config_schema", None)
            if callable(schema_factory):
                parameter_schema = schema_factory(dataset.preview(limit=1000))
        return AnalysisManifest(
            id=analysis_id, name=item["name"],
            description=item["description"], category=item["category"],
            parameter_schema=parameter_schema,
            caution=_analysis_caution(analysis_id))

    def run_analysis(self, analysis_id: str, dataset_id: str,
                     parameters: dict[str, Any] | None = None) -> str:
        item = self._analysis(analysis_id)
        entry = self._dataset(dataset_id)
        options = dict(parameters or {})

        def work(cancel, progress):
            try:
                frame = entry.active.analysis_frame(cancel=cancel)
                module = load_analysis(item)
                result, diagnostic = execute_analysis(
                    module, frame, options, {}, cancel, progress)
                if not isinstance(result, dict):
                    result = {"Resultado": result}
                if diagnostic:
                    result.setdefault("Diagnóstico de ejecución", diagnostic)
                result_id = uuid.uuid4().hex
                with self._lock:
                    self._results[result_id] = _ResultEntry(
                        result_id, analysis_id, result)
                return result_id
            except MemoryError as exc:
                raise MemoryBudgetError(str(exc)) from exc
            except (KeyError, ValueError) as exc:
                raise AnalysisValidationError(str(exc)) from exc
            except Exception as exc:
                raise AnalysisExecutionError(
                    "El análisis no pudo completarse.",
                    details={"type": type(exc).__name__,
                             "reason": str(exc)}) from exc

        return self.jobs.submit(f"analysis:{analysis_id}", work)

    def cancel_job(self, job_id: str) -> JobStatus:
        return self.jobs.cancel(job_id)

    def get_job_status(self, job_id: str) -> JobStatus:
        return self.jobs.status(job_id)

    def get_analysis_result(self, result_id: str) -> ResultSummary:
        entry = self._result(result_id)
        tables, charts, scalars = [], [], {}
        for key, value in entry.value.items():
            if isinstance(value, pd.DataFrame):
                tables.append(str(key))
            elif isinstance(value, dict) and "plot" in value:
                charts.append(str(key))
            elif not isinstance(value, (dict, list, tuple)):
                scalars[str(key)] = scalar_value(value)
        return ResultSummary(result_id=result_id,
                             analysis_id=entry.analysis_id,
                             tables=tables, charts=charts, scalars=scalars)

    def get_result_table(self, result_id: str, table: str,
                         offset: int = 0, limit: int = 200) -> dict[str, Any]:
        entry = self._result(result_id)
        value = entry.value.get(table)
        if not isinstance(value, pd.DataFrame):
            raise UserInputError("Tabla de resultado no encontrada.",
                                 details={"table": table})
        page = value.iloc[offset:offset + limit]
        return {"result_id": result_id, "table": table, "offset": offset,
                "limit": limit, "total_rows": len(value),
                "columns": [str(column) for column in value.columns],
                "rows": frame_records(page)}

    def export_result(self, result_id: str, table: str,
                      path: str | Path, fmt: str) -> str:
        from core.exporter import export_dataframe
        entry = self._result(result_id)
        value = entry.value.get(table)
        if not isinstance(value, pd.DataFrame):
            raise UserInputError("Tabla de resultado no encontrada.")
        try:
            exported = export_dataframe(value, Path(path), fmt)
            return str(exported or path)
        except (OSError, ValueError) as exc:
            raise ExportError("No se pudo exportar el resultado.",
                              details={"reason": str(exc)}) from exc

    def shutdown(self) -> None:
        self.jobs.shutdown(wait=True)

    def _dataset(self, dataset_id: str) -> _DatasetEntry:
        with self._lock:
            entry = self._datasets.get(dataset_id)
        if entry is None:
            raise DataValidationError("Dataset no encontrado.",
                                      details={"dataset_id": dataset_id})
        return entry

    def _analysis(self, analysis_id: str) -> dict[str, Any]:
        item = self._analyses.get(analysis_id)
        if item is None:
            raise AnalysisValidationError("Análisis no encontrado.",
                                          details={"analysis_id": analysis_id})
        return item

    def _result(self, result_id: str) -> _ResultEntry:
        with self._lock:
            entry = self._results.get(result_id)
        if entry is None:
            raise AnalysisValidationError("Resultado no encontrado.",
                                          details={"result_id": result_id})
        return entry


def _analysis_id(item: dict[str, Any]) -> str:
    stem = Path(item["file"]).stem.casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    if slug:
        return slug
    return hashlib.sha256(item["file"].encode()).hexdigest()[:12]


def _analysis_caution(analysis_id: str) -> str | None:
    if "causal" in analysis_id:
        return "La validez depende de controles no afectados y supuestos PRE/POST."
    if "geox" in analysis_id or "geo-test" in analysis_id:
        return "Diseño PRE-test; no estima por sí solo un efecto posterior."
    if "regression" in analysis_id:
        return "La atribución predictiva no implica causalidad."
    return None
