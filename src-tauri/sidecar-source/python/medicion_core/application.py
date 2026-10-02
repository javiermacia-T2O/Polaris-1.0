"""Typed application service used by Tkinter today and Tauri next."""

from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
import hashlib
import importlib.metadata
import importlib
from pathlib import Path
import re
import threading
import time
import uuid
import platform
import sys
from typing import Any

import pandas as pd

from core.plugin_loader import discover_analyses, load_analysis, read_table_format
from services.active_dataset import ActiveDataset
from services.analysis_service import run_analysis as execute_analysis
from services.data_service import read_dataset

from .errors import (AnalysisExecutionError, AnalysisValidationError,
                     DataValidationError, ExportError, MemoryBudgetError,
                     UserInputError)
from .jobs import JobManager
from .observability import event
from .schemas import (AnalysisManifest, DatasetMetadata, FilterSpec, JobState,
                      JobStatus, ResultSummary, SortSpec, TableFormat, TablePage)
from .serialization import frame_records, scalar_value


@dataclass
class _DatasetEntry:
    dataset_id: str
    name: str
    original: ActiveDataset
    active: ActiveDataset
    source_path: Path | None
    resident_bytes: int = 0


@dataclass
class _ResultEntry:
    result_id: str
    analysis_id: str
    value: dict[str, Any]


class MedicionApplication:
    """Owns lightweight handles; heavy data remains in Python/DuckDB."""

    # A full COUNT(*) over a CSV larger than this is deferred to a background
    # thread so the UI never blocks on a multi-gigabyte scan.
    _ASYNC_COUNT_BYTES = 16 * 1024 * 1024

    def __init__(self, *, max_workers: int = 2):
        self._datasets: dict[str, _DatasetEntry] = {}
        self._results: dict[str, _ResultEntry] = {}
        self._lock = threading.RLock()
        self.jobs = JobManager(max_workers=max_workers)
        self._analyses = {_analysis_id(item): item
                          for item in discover_analyses()}
        # Row counts are expensive on multi-million-row sources (a full
        # COUNT(*) scan).  Cache them per dataset version so metadata and
        # column listings stay instant while the underlying data is unchanged.
        self._row_counts: dict[str, tuple[str, int]] = {}
        self._row_count_estimates: dict[str, tuple[str, int]] = {}
        self._column_values_cache: OrderedDict[tuple, list[str | None] | None] = OrderedDict()
        # Version tokens whose count is being computed in the background, so
        # concurrent metadata polls never launch duplicate scans.
        self._row_count_pending: set[tuple[str, str]] = set()

    def load_dataset(self, path: str | Path) -> DatasetMetadata:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise DataValidationError(
                "El archivo no existe.", details={"path": str(source)})
        try:
            # Count every resident dataset/result so the load preflight never
            # ignores memory already held by the session.
            from core.resource_manager import get_manager

            retained = get_manager().resident_bytes()
            loaded = read_dataset(source, retained, lambda _message: None)
            dataset = (loaded if isinstance(loaded, ActiveDataset)
                       else ActiveDataset.from_frame(loaded))
            if dataset.backend == "pandas":
                resident = int(dataset.source.memory_usage(deep=True).sum())
                get_manager().note_resident(resident)
            else:
                resident = 0
            dataset_id = uuid.uuid4().hex
            entry = _DatasetEntry(dataset_id, source.stem, dataset, dataset,
                                  source, resident)
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
            entry = self._datasets.pop(dataset_id, None)
            if entry is None:
                raise DataValidationError("Dataset no encontrado.",
                                          details={"dataset_id": dataset_id})
            self._row_counts.pop(dataset_id, None)
            self._row_count_estimates.pop(dataset_id, None)
            self._column_values_cache = OrderedDict(
                (key, value) for key, value in self._column_values_cache.items()
                if key[0] != dataset_id)
            self._row_count_pending = {
                key for key in self._row_count_pending
                if key[0] != dataset_id}
        if entry.resident_bytes:
            from core.resource_manager import get_manager

            get_manager().note_resident(-entry.resident_bytes)

    def list_datasets(self) -> list[DatasetMetadata]:
        with self._lock:
            ids = list(self._datasets)
        return [self.get_dataset_metadata(dataset_id) for dataset_id in ids]

    def _row_count(self, dataset_id: str, active: ActiveDataset) -> int:
        """Return the row count, reusing the cached value for this version.

        A full ``COUNT(*)`` over tens of millions of rows is the single most
        expensive call the UI can trigger, and it is requested by metadata,
        column listings and every preview page.  The count only changes when
        the dataset version changes (filters, projection, date range), so it
        is memoised against ``version_token``.
        """
        token = active.version_token()
        cached = self._row_counts.get(dataset_id)
        if cached is not None and cached[0] == token:
            return cached[1]
        rows = active.row_count()
        self._row_counts[dataset_id] = (token, rows)
        return rows

    def _row_count_or_defer(self, dataset_id: str,
                            active: ActiveDataset) -> tuple[int, bool]:
        """Return ``(rows, approximate)`` without blocking on a huge scan.

        The count is a property of the source file, so a previously seen file
        answers instantly from the persisted cache.  When the file is large
        and has never been counted, the exact total is computed in a
        background thread and the caller gets a provisional ``0`` marked as
        approximate; the UI polls metadata until the real value arrives.
        """
        token = active.version_token()
        cached = self._row_counts.get(dataset_id)
        if cached is not None and cached[0] == token:
            return cached[1], False
        known = active.row_count_cached()
        if known is not None:
            self._row_counts[dataset_id] = (token, known)
            return known, False
        if active.backend != "pandas" and active.file_size > self._ASYNC_COUNT_BYTES:
            estimate = self._row_count_estimates.get(dataset_id)
            if estimate is not None and estimate[0] == token:
                return estimate[1], True
            estimate_rows = active.row_count_estimate()
            estimate_rows = estimate_rows or 0
            self._row_count_estimates[dataset_id] = (token, estimate_rows)
            self._schedule_row_count(dataset_id, active, token)
            return estimate_rows, True
        rows = self._row_count(dataset_id, active)
        return rows, False

    def _schedule_row_count(self, dataset_id: str, active: ActiveDataset,
                            token: str) -> None:
        """Compute a large row count in the background, once per version."""
        key = (dataset_id, token)
        scheduled_at = time.perf_counter()
        with self._lock:
            if key in self._row_count_pending:
                return
            self._row_count_pending.add(key)

        def work(_cancel, _progress):
            started_at = time.perf_counter()
            try:
                rows = active.row_count()
            except Exception as exc:
                with self._lock:
                    self._row_count_pending.discard(key)
                finished_at = time.perf_counter()
                total_ms = round((finished_at - scheduled_at) * 1000, 2)
                event("error", "application", "row_count",
                      dataset_id=dataset_id,
                      queue_ms=round((started_at - scheduled_at) * 1000, 2),
                      execution_ms=round((finished_at - started_at) * 1000, 2),
                      total_ms=total_ms, duration_ms=total_ms,
                      error_code=type(exc).__name__)
                return None
            with self._lock:
                # Only publish the result while the dataset is still open and
                # its version has not changed under us.
                if dataset_id in self._datasets:
                    self._row_counts[dataset_id] = (token, rows)
                self._row_count_pending.discard(key)
            finished_at = time.perf_counter()
            total_ms = round((finished_at - scheduled_at) * 1000, 2)
            event("info", "application", "row_count",
                  dataset_id=dataset_id, rows=rows,
                  queue_ms=round((started_at - scheduled_at) * 1000, 2),
                  execution_ms=round((finished_at - started_at) * 1000, 2),
                  total_ms=total_ms, duration_ms=total_ms,
                  status="ok")
            return None

        self.jobs.submit("row_count", work)

    def get_dataset_metadata(self, dataset_id: str) -> DatasetMetadata:
        entry = self._dataset(dataset_id)
        try:
            rows, approximate = self._row_count_or_defer(dataset_id,
                                                         entry.active)
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
            source_path=str(entry.source_path) if entry.source_path else None,
            rows_approximate=approximate)

    def get_columns(self, dataset_id: str) -> list[dict[str, str]]:
        """List columns without paying for a row count.

        The UI asks for columns far more often than for the row total, so this
        reads the schema directly instead of routing through metadata.
        """
        entry = self._dataset(dataset_id)
        columns = list(entry.active.projection or entry.active.columns)
        type_map = dict(zip(entry.active.columns, entry.active.types))
        return [{"name": name, "type": type_map.get(name, "")}
                for name in columns]

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

    def get_column_values(self, dataset_id: str, column: str,
                          limit: int = 500,
                          cancel: threading.Event | None = None,
                          progress: Any = None) -> dict[str, Any]:
        """List the distinct values of one column for filter checkboxes.

        The list is bounded so a high-cardinality column never floods the UI;
        ``truncated`` tells the frontend to fall back to a free-text filter.
        """
        entry = self._dataset(dataset_id)
        if column not in entry.active.columns:
            raise UserInputError("Columna no encontrada.",
                                 details={"column": column})
        cache_key = (dataset_id, entry.active.version_token(), column, limit)
        with self._lock:
            if cache_key in self._column_values_cache:
                cached = self._column_values_cache[cache_key]
                self._column_values_cache.move_to_end(cache_key)
                if progress is not None:
                    progress((100, "Valores recuperados de la caché en memoria"))
                if cached is None:
                    return {"column": column, "values": [], "truncated": True}
                return {"column": column, "values": list(cached),
                        "truncated": False}
        if progress is not None:
            progress((5, "Buscando valores únicos en todo el dataset original..."))
        try:
            values = entry.active.distinct_values(column, limit=limit,
                                                  cancel=cancel)
        except MemoryError as exc:
            raise MemoryBudgetError(str(exc)) from exc
        with self._lock:
            self._column_values_cache[cache_key] = (
                list(values) if values is not None else None)
            self._column_values_cache.move_to_end(cache_key)
            while len(self._column_values_cache) > 128:
                self._column_values_cache.popitem(last=False)
        if progress is not None:
            progress((100, "Valores del dataset completo listos"))
        if values is None:
            return {"column": column, "values": [], "truncated": True}
        return {"column": column, "values": values, "truncated": False}

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

    def get_date_columns(self, dataset_id: str) -> list[dict[str, str]]:
        """List detected date columns with their observed granularity."""
        from core.loader import detect_granularity, get_date_columns

        entry = self._dataset(dataset_id)
        try:
            frame = entry.active.preview(limit=1000)
        except MemoryError as exc:
            raise MemoryBudgetError(str(exc)) from exc
        result = []
        for column in get_date_columns(frame):
            result.append({"column": column,
                           "granularity": detect_granularity(frame, column)})
        return result

    def get_date_range(self, dataset_id: str, column: str,
                       cancel: threading.Event | None = None) -> dict[str, Any]:
        """Return the min/max dates and granularity for a date column."""
        from core.loader import detect_granularity

        entry = self._dataset(dataset_id)
        if column not in entry.active.columns:
            raise UserInputError("Columna de fecha no encontrada.",
                                 details={"column": column})
        try:
            minimum, maximum = entry.active.date_range(column, cancel=cancel)
        except MemoryError as exc:
            raise MemoryBudgetError(str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise UserInputError("No se pudo calcular el rango de fechas.",
                                 details={"reason": str(exc)}) from exc
        granularity = "Original"
        try:
            granularity = detect_granularity(
                entry.active.preview(limit=1000), column)
        except Exception:
            pass
        return {
            "column": column,
            "start": str(pd.Timestamp(minimum).date()) if minimum is not None
            and not pd.isna(minimum) else None,
            "end": str(pd.Timestamp(maximum).date()) if maximum is not None
            and not pd.isna(maximum) else None,
            "granularity": granularity,
        }

    def apply_date_range(self, dataset_id: str, column: str,
                         start: str | None = None,
                         end: str | None = None) -> DatasetMetadata:
        entry = self._dataset(dataset_id)
        try:
            entry.active = entry.active.with_date_range(column, start, end)
        except KeyError as exc:
            raise UserInputError("Columna de fecha no encontrada.",
                                 details={"column": str(exc)}) from exc
        except ValueError as exc:
            raise UserInputError(str(exc),
                                 details={"column": column}) from exc
        return self.get_dataset_metadata(dataset_id)

    def reset_date_range(self, dataset_id: str) -> DatasetMetadata:
        entry = self._dataset(dataset_id)
        entry.active = entry.active.with_date_range(
            entry.active.date_bounds[0], None, None) if entry.active.date_bounds \
            else entry.active
        return self.get_dataset_metadata(dataset_id)

    def get_table_preview(self, dataset_id: str, offset: int = 0,
                          limit: int = 200) -> TablePage:
        return self.get_table_page(dataset_id, offset=offset, limit=limit)

    def get_table_page(self, dataset_id: str, offset: int = 0,
                       limit: int = 200,
                       sort: list[SortSpec | dict[str, Any]] | None = None,
                       cancel: threading.Event | None = None) -> TablePage:
        if limit < 1 or limit > 1000 or offset < 0:
            raise UserInputError("La página debe contener entre 1 y 1000 filas.")
        entry = self._dataset(dataset_id)
        sorts = [item if isinstance(item, SortSpec)
                 else SortSpec.model_validate(item) for item in (sort or [])]
        try:
            frame = entry.active.page(
                limit=limit, offset=offset,
                sort=[(item.column, item.direction == "asc") for item in sorts],
                cancel=cancel)
        except KeyError as exc:
            raise UserInputError("Columna de orden no encontrada.",
                                 details={"column": str(exc)}) from exc
        total_rows, approximate = self._row_count_or_defer(dataset_id,
                                                           entry.active)
        return TablePage(
            dataset_id=dataset_id, offset=offset, limit=limit,
            total_rows=total_rows,
            columns=[str(column) for column in frame.columns],
            rows=frame_records(frame),
            total_rows_approximate=approximate)

    def preview_table(self, dataset_id: str, recipe: dict[str, Any],
                      limit: int = 100,
                      progress: Any = None) -> TablePage:
        """Return bounded result rows; aggregate their values from every source row."""
        from models.table_recipe import TableRecipe
        from services.table_service import (build_table_preview_pandas,
                                            preview_to_pandas)

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
            if progress is not None:
                progress((5, "Preparando hasta 100 filas de la tabla..."))
            if entry.active.backend == "pandas":
                # Register the resident frame and compile the same recipe as
                # the lazy backends; only the returned rows are limited.
                frame = build_table_preview_pandas(
                    entry.active._frame(), table_recipe.rows,
                    table_recipe.columns, table_recipe.values,
                    dict(table_recipe.filters), recipe=table_recipe,
                    progress=progress)
            else:
                frame = preview_to_pandas(entry.active, table_recipe,
                                          limit=limit, progress=progress)
            approximate = bool(frame.attrs.get("approximate", False))
            frame = frame.head(max(1, min(int(limit), 200)))
            if progress is not None:
                progress((100, "Vista previa lista"))
            return TablePage(
                dataset_id=dataset_id, offset=0, limit=len(frame),
                total_rows=len(frame),
                columns=[str(column) for column in frame.columns],
                rows=frame_records(frame),
                approximate=approximate)
        except (KeyError, ValueError) as exc:
            raise UserInputError("La receta de tabla no es válida.",
                                 details={"reason": str(exc)}) from exc

    def build_table(self, dataset_id: str, recipe: dict[str, Any],
                    progress: Any = None) -> str:
        from models.table_recipe import TableRecipe
        from services.table_service import build_table_view

        entry = self._dataset(dataset_id)
        try:
            if progress is not None:
                progress((5, "Preparando la tabla sobre el dataset original..."))
            table_recipe = TableRecipe.from_parts(
                rows=recipe.get("rows", ()), cols=recipe.get("columns", ()),
                val_specs=recipe.get("values", ()),
                filters=recipe.get("filters"),
                pivot=recipe.get("pivot", True),
                selected=recipe.get("selected", ()),
                order=recipe.get("order", ()),
                col_types=recipe.get("column_types"))
            if entry.active.backend == "pandas":
                if progress is not None:
                    progress((20, "Aplicando filtros y agrupaciones a todas las filas..."))
                view = ActiveDataset.from_frame(
                    self._build_pandas_table(entry.active._frame(), table_recipe))
            else:
                view = build_table_view(entry.active, table_recipe)
            table_id = uuid.uuid4().hex
            with self._lock:
                self._datasets[table_id] = _DatasetEntry(
                    table_id, f"{entry.name} · tabla", view, view, None)
            if progress is not None:
                progress((100, "Tabla lista"))
            return table_id
        except (KeyError, ValueError) as exc:
            raise UserInputError("La receta de tabla no es válida.",
                                 details={"reason": str(exc)}) from exc

    def _build_pandas_table(self, frame: pd.DataFrame,
                            recipe: Any) -> pd.DataFrame:
        from core.memory_budget import ensure_dataframe_operation_fits

        ensure_dataframe_operation_fits(frame, 2, "Constructor de tablas")
        working = frame.copy()
        for column, kind in recipe.column_types:
            if column not in working.columns:
                raise KeyError(column)
            if kind == "numero":
                working[column] = pd.to_numeric(working[column], errors="coerce")
            elif kind == "fecha":
                working[column] = pd.to_datetime(working[column], errors="coerce")
            elif kind == "ignorar":
                continue

        ignored = {column for column, kind in recipe.column_types
                   if kind == "ignorar"}
        for column, allowed in recipe.filters:
            if column not in working.columns:
                raise KeyError(column)
            from services.active_dataset import value_filter_mask
            working = working.loc[value_filter_mask(working[column], allowed)]

        values = list(recipe.values)
        if not values:
            # Without a metric the table shows the chosen dimensions only.
            # Selecting a variable as Fila/Columna must never dump the whole
            # dataset: fall back to the assigned dimensions, de-duplicated.
            dimensions = [column for column in [*recipe.rows, *recipe.columns]
                          if column not in ignored]
            if dimensions:
                missing = [column for column in dimensions
                           if column not in working.columns]
                if missing:
                    raise KeyError(missing[0])
                return working.loc[:, dimensions].drop_duplicates().reset_index(drop=True)
            selected = list(recipe.selected) or [
                column for column in working.columns if column not in ignored]
            missing = [column for column in selected if column not in working.columns]
            if missing:
                raise KeyError(missing[0])
            return working.loc[:, selected].copy()

        for column in [*recipe.rows, *recipe.columns,
                       *(metric for metric, _ in values)]:
            if column not in working.columns:
                raise KeyError(column)

        pivots = recipe.pivots_for_values()

        aggregation_names = {
            "sum", "mean", "median", "min", "max", "count", "nunique",
            "std", "first", "last",
        }
        for _, aggregation in values:
            if aggregation not in aggregation_names:
                raise ValueError(f"Agregación no soportada: {aggregation}")

        names = []
        counts: dict[str, int] = {}
        for metric, aggregation in values:
            counts[metric] = counts.get(metric, 0) + 1
            names.append(metric if counts[metric] == 1
                         else f"{metric} · {aggregation}")

        def aggregate(source: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
            pieces = []
            if group_columns:
                grouped = source.groupby(group_columns, dropna=False, sort=False)
                for (metric, aggregation), name in zip(values, names):
                    piece = grouped[metric].agg(aggregation).rename(name).reset_index()
                    pieces.append(piece)
                result = pieces[0]
                for piece in pieces[1:]:
                    result = result.merge(piece, on=group_columns, how="outer",
                                          validate="one_to_one")
                return result

            return pd.DataFrame([{
                name: source[metric].agg(aggregation)
                for (metric, aggregation), name in zip(values, names)
            }])

        if any(pivots) and recipe.columns:
            pivot_columns = list(recipe.columns)
            categories = working[pivot_columns].dropna().drop_duplicates()
            if len(categories) > 50:
                raise ValueError("Pivot con más de 50 columnas; filtra sus valores")
            group_columns = list(dict.fromkeys([*recipe.rows, *pivot_columns]))
            long = aggregate(working, group_columns)
            index_column = "__table_row__"
            index = list(recipe.rows)
            if not index:
                long[index_column] = 0
                index = [index_column]
            pieces = []
            pivoted_metric_count = sum(pivots)
            for (metric, aggregation), name, pivot in zip(values, names, pivots):
                if not pivot:
                    continue
                piece = long.pivot(index=index, columns=pivot_columns,
                                   values=name)
                if aggregation in {"sum", "count", "nunique"}:
                    piece = piece.fillna(0)
                piece.columns = [
                    ((f"{name} · " if pivoted_metric_count > 1 else "") +
                     (" | ".join(str(part) for part in value)
                      if isinstance(value, tuple) else str(value)))
                    for value in piece.columns]
                pieces.append(piece)
            result = pd.concat(pieces, axis=1).reset_index()
            flat_names = [name for name, pivot in zip(names, pivots)
                          if not pivot]
            if flat_names:
                flat = aggregate(working, list(recipe.rows))
                if not recipe.rows:
                    flat[index_column] = 0
                result = result.merge(flat.loc[:, [*index, *flat_names]], on=index, how="left",
                                      validate="one_to_one")
            if index_column in result.columns:
                result = result.drop(columns=[index_column])
        else:
            groups = list(dict.fromkeys([*recipe.rows, *recipe.columns]))
            result = aggregate(working, groups)

        for column, ascending in recipe.order:
            if column not in result.columns:
                raise KeyError(column)
            result = result.sort_values(column, ascending=ascending,
                                        kind="mergesort")
        return result.reset_index(drop=True)

    def list_analyses(self) -> list[AnalysisManifest]:
        return [self.get_analysis_manifest(analysis_id)
                for analysis_id in self._analyses]

    def get_analysis_manifest(self, analysis_id: str,
                              dataset_id: str | None = None) -> AnalysisManifest:
        item = self._analysis(analysis_id)
        parameter_schema = None
        # ``get_table_format`` is a pure literal function: read it statically so
        # listing analyses never imports heavy plugins (jax/geox/statsmodels).
        format_data = read_table_format(item)
        table_format = TableFormat(**format_data) if format_data else None
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
            table_format=table_format,
            caution=_analysis_caution(analysis_id))

    def regression_preview(self, dataset_id: str, date_column: str,
                           columns: list[str], limit: int = 1200) -> TablePage:
        """Build a bounded temporal series from every active row.

        Values belonging to the same calendar day are summed before any point
        reduction. This keeps sparse, wide tables usable in the workbench and
        avoids a random row sample hiding most explanatory variables.
        """
        entry = self._dataset(dataset_id)
        active = entry.active
        limit = max(100, min(int(limit), 2000))
        available = tuple(active.projection or active.columns)
        if not date_column or date_column not in available:
            from core.loader import detect_date_column
            sample = active.preview(limit=1000)
            date_column = detect_date_column(sample) or ""
        if not date_column or date_column not in available:
            raise UserInputError("No se encontró una columna temporal válida en la tabla.")
        requested = list(dict.fromkeys([date_column, *(
            column for column in columns if column in available and column != date_column)]))
        if len(requested) < 2:
            raise UserInputError("Selecciona una variable numérica disponible para el gráfico.")
        if active.backend == "pandas":
            import pandas as pd
            frame = active._frame().loc[:, requested].copy()
            parsed = pd.to_datetime(frame[date_column], errors="coerce")
            if parsed.notna().any():
                frame[date_column] = parsed.dt.strftime("%Y-%m-%d")
            for column in requested[1:]:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frame = (frame.dropna(subset=[date_column])
                     .groupby(date_column, sort=True, dropna=True)[requested[1:]]
                     .sum(min_count=1).reset_index())
            total_days = len(frame)
            if len(frame) > limit:
                frame = frame.iloc[
                    [round(index * (len(frame) - 1) / (limit - 1))
                     for index in range(limit)]]
        else:
            from core import engine as db_engine
            quoted = lambda value: '"' + str(value).replace('"', '""') + '"'
            where, params = active._where()
            date = quoted(date_column)
            day = (f"COALESCE(CAST(TRY_CAST({date} AS DATE) AS VARCHAR), "
                   f"CAST({date} AS VARCHAR))")
            measures = ", ".join(
                f"SUM(TRY_CAST({quoted(column)} AS DOUBLE)) AS {quoted(column)}"
                for column in requested[1:])
            query = (
                f"WITH daily AS (SELECT {day} AS {date}, {measures} "
                f"FROM {active._source_sql()}{where} "
                f"GROUP BY 1), numbered AS ("
                f"SELECT *, ROW_NUMBER() OVER (ORDER BY {date}) AS _rn, "
                f"COUNT(*) OVER () AS _total FROM daily), bucketed AS ("
                f"SELECT *, ROW_NUMBER() OVER ("
                f"PARTITION BY FLOOR((_rn - 1) * ({limit} - 1)::DOUBLE "
                f"/ GREATEST(1, _total - 1)) "
                f"ORDER BY CASE WHEN _rn = _total THEN 0 ELSE _rn END) AS _pick "
                f"FROM numbered) "
                f"SELECT * EXCLUDE (_rn, _pick) FROM bucketed "
                f"WHERE _pick = 1 "
                f"ORDER BY {date}")
            frame = db_engine.get_conn().execute(
                query, active._params(params)).fetchdf()
            total_days = int(frame.pop("_total").max()) if len(frame) else 0
        records = frame_records(frame.reset_index(drop=True))
        return TablePage(
            dataset_id=dataset_id, offset=0, limit=len(records),
            total_rows=len(records),
            columns=[str(column) for column in frame.columns],
            rows=records, approximate=total_days > len(records),
            total_rows_approximate=total_days > len(records))

    def run_analysis(self, analysis_id: str, dataset_id: str,
                     parameters: dict[str, Any] | None = None,
                     cancel: threading.Event | None = None) -> str:
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
                state = str(result.get("Estado", ""))
                if state.upper().startswith("ERROR"):
                    raise AnalysisValidationError(
                        state, details={"reason": str(result.get("Detalle", ""))})
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
            except AnalysisValidationError:
                raise
            except Exception as exc:
                raise AnalysisExecutionError(
                    "El análisis no pudo completarse.",
                    details={"type": type(exc).__name__,
                             "reason": str(exc)}) from exc

        job_id = self.jobs.submit(f"analysis:{analysis_id}", work)
        if cancel is not None:
            self._bridge_cancel(job_id, cancel)
        return job_id

    def _bridge_cancel(self, job_id: str,
                       cancel: threading.Event) -> None:
        """Cancel a background job when its IPC request is cancelled."""
        terminal = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}

        def watch() -> None:
            while not cancel.wait(0.05):
                try:
                    if self.jobs.status(job_id).state in terminal:
                        return
                except KeyError:
                    return
            try:
                self.jobs.cancel(job_id)
            except KeyError:
                pass

        threading.Thread(target=watch, name="medicion-cancel-bridge",
                         daemon=True).start()

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
            elif isinstance(value, dict) and value.get("plot") is not None:
                charts.append(str(key))
            elif key not in {"Diagnóstico de ejecución", "Resumen statsmodels"} and not isinstance(value, (dict, list, tuple)):
                scalars[str(key)] = scalar_value(value)
        return ResultSummary(result_id=result_id,
                             analysis_id=entry.analysis_id,
                             tables=tables, charts=charts, scalars=scalars)

    def get_result_chart(self, result_id: str, chart: str) -> dict[str, str]:
        entry = self._result(result_id)
        item = entry.value.get(chart)
        figure = item.get("plot") if isinstance(item, dict) else None
        if figure is None:
            raise UserInputError("Gráfico de resultado no encontrado.",
                                 details={"chart": chart})

        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from ui.figure_utils import _is_matplotlib, _is_plotly, _plotly_to_matplotlib

        if _is_plotly(figure):
            figure = _plotly_to_matplotlib(figure)
        if not _is_matplotlib(figure):
            raise UserInputError("El gráfico no se puede representar.",
                                 details={"chart": chart})

        import base64
        from io import BytesIO

        output = BytesIO()
        FigureCanvasAgg(figure).print_png(output)
        return {"result_id": result_id, "chart": chart,
                "mime_type": "image/png",
                "data_base64": base64.b64encode(output.getvalue()).decode("ascii")}

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
                      path: str | Path, fmt: str,
                      cancel: threading.Event | None = None,
                      progress: Any = None) -> str:
        from core.exporter import export_dataframe
        entry = self._result(result_id)
        value = entry.value.get(table)
        if not isinstance(value, pd.DataFrame):
            raise UserInputError("Tabla de resultado no encontrada.")
        try:
            exported = export_dataframe(value, Path(path), fmt, cancel=cancel,
                                        progress=progress)
            return str(exported or path)
        except (OSError, ValueError) as exc:
            raise ExportError("No se pudo exportar el resultado.",
                              details={"reason": str(exc)}) from exc

    def export_analysis_bundle(self, result_id: str, destination: str,
                               run_name: str, kind: str, name: str = "",
                               existing_root: str = "") -> dict[str, Any]:
        """Save selected analysis artifacts under date/run/analysis folders."""
        import base64
        from datetime import date

        entry = self._result(result_id)
        base = Path(destination).expanduser().resolve()
        if not base.is_dir():
            raise UserInputError("Selecciona una carpeta de destino válida.")
        run_label = _safe_export_name(run_name)
        if not run_label:
            raise UserInputError("Escribe el nombre del análisis o cliente.")
        if kind not in {"metrics", "table", "all_results", "chart", "all_charts"}:
            raise UserInputError("Tipo de exportación no válido.")

        day_dir = base / date.today().isoformat()
        if existing_root:
            root = Path(existing_root).expanduser().resolve()
            if not root.is_relative_to(day_dir) or root.parent.parent != day_dir:
                raise UserInputError("La carpeta de guardado ya no es válida.")
            if root.parent.name != run_label and not re.fullmatch(
                    re.escape(run_label) + r" \([2-9][0-9]*\)", root.parent.name):
                raise UserInputError("El nombre de la carpeta ha cambiado.")
            if not root.is_dir():
                raise UserInputError("La carpeta de guardado ya no existe.")
        else:
            analysis_label = "Regresión" if entry.analysis_id == "regression" else \
                _safe_export_name(entry.analysis_id) or "Análisis"
            day_dir.mkdir(exist_ok=True)
            run_dir = day_dir / run_label
            if run_dir.exists():
                counter = 2
                while (day_dir / f"{run_label} ({counter})").exists():
                    counter += 1
                run_dir = day_dir / f"{run_label} ({counter})"
            root = run_dir / analysis_label
            root.mkdir(parents=True)

        result_dir = root / "Resultados"
        chart_dir = root / "Gráficos"
        result_dir.mkdir(exist_ok=True)
        chart_dir.mkdir(exist_ok=True)
        saved: list[str] = []
        try:
            if kind in {"metrics", "all_results"}:
                scalars = self.get_analysis_result(result_id).scalars
                keys = [key for key, value in scalars.items()
                        if isinstance(value, (int, float)) or key in {
                            "Estado", "Modelo solicitado", "Modelo ajustado",
                            "quality_status", "Correlación real-predicho"}]
                metrics = pd.DataFrame([{"Métrica": key, "Valor": scalars[key]}
                                        for key in keys])
                path = result_dir / "Resumen de métricas.csv"
                metrics.to_csv(path, index=False, encoding="utf-8-sig")
                saved.append(str(path))

            table_names = ([name] if kind == "table" else
                           [key for key, value in entry.value.items()
                            if isinstance(value, pd.DataFrame)]
                           if kind == "all_results" else [])
            for table_name in table_names:
                value = entry.value.get(table_name)
                if not isinstance(value, pd.DataFrame):
                    raise UserInputError("Tabla de resultado no encontrada.")
                path = result_dir / f"{_safe_export_name(table_name)}.csv"
                value.to_csv(path, index=False, encoding="utf-8-sig")
                saved.append(str(path))

            chart_names = ([name] if kind == "chart" else
                           [key for key, value in entry.value.items()
                            if isinstance(value, dict) and value.get("plot") is not None]
                           if kind == "all_charts" else [])
            for chart_name in chart_names:
                artifact = self.get_result_chart(result_id, chart_name)
                path = chart_dir / f"{_safe_export_name(chart_name)}.png"
                path.write_bytes(base64.b64decode(artifact["data_base64"]))
                saved.append(str(path))
        except (OSError, ValueError) as exc:
            raise ExportError("No se pudieron guardar los resultados.",
                              details={"reason": str(exc)}) from exc
        return {"root_dir": str(root), "saved": saved}

    def export_dataset(self, dataset_id: str, path: str | Path,
                       fmt: str, cancel: threading.Event | None = None,
                       progress: Any = None) -> str:
        """Export the active (filtered) dataset without loading it fully."""
        entry = self._dataset(dataset_id)
        try:
            exported = entry.active.export(Path(path), fmt, cancel=cancel,
                                           progress=progress)
            return str(exported or path)
        except (OSError, ValueError) as exc:
            raise ExportError("No se pudo exportar el dataset.",
                              details={"reason": str(exc)}) from exc

    def merge_datasets(self, dataset_ids: list[str], name: str = "",
                       operation: str = "concat", mode: str = "all",
                       keys: list[str] | None = None,
                       how: str = "inner",
                       cancel: threading.Event | None = None) -> DatasetMetadata:
        """Combine two or more datasets into a new pooled dataset.

        ``operation`` is ``concat`` (stack rows) or ``merge`` (join on keys).
        ``mode`` selects ``all`` columns or only ``common`` ones for concat.
        """
        from services.merge_service import concat_datasets, merge_datasets

        if len(dataset_ids) < 2:
            raise UserInputError("Selecciona al menos 2 datasets para unir.")
        entries = [self._dataset(dataset_id) for dataset_id in dataset_ids]
        frames: dict[str, pd.DataFrame] = {}
        for entry in entries:
            try:
                frames[entry.dataset_id] = entry.active.analysis_frame(
                    cancel=cancel)
            except MemoryError as exc:
                raise MemoryBudgetError(str(exc)) from exc
        try:
            if operation == "concat":
                result = concat_datasets(frames, dataset_ids, mode)
            elif operation == "merge":
                result = merge_datasets(frames, dataset_ids, keys or [], how)
            else:
                raise UserInputError("Operación de unión no soportada.",
                                     details={"operation": operation})
        except MemoryError as exc:
            raise MemoryBudgetError(str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise UserInputError("No se pudieron unir los datasets.",
                                 details={"reason": str(exc)}) from exc
        dataset = ActiveDataset.from_frame(result)
        dataset_id = uuid.uuid4().hex
        label = name.strip() or "unido"
        entry = _DatasetEntry(dataset_id, label, dataset, dataset, None)
        with self._lock:
            self._datasets[dataset_id] = entry
        return self.get_dataset_metadata(dataset_id)

    def get_cache_info(self) -> dict[str, Any]:
        """Report the size of the on-disk Parquet cache."""
        from core import engine as db_engine

        try:
            size_mb = float(db_engine.cache_size_mb())
        except Exception:
            size_mb = 0.0
        return {"size_mb": round(size_mb, 2)}

    def clear_cache(self) -> dict[str, Any]:
        """Delete cached Parquet files, protecting active dataset sources."""
        from core import engine as db_engine

        protected: list[Path] = []
        with self._lock:
            entries = list(self._datasets.values())
        for entry in entries:
            if entry.source_path is not None:
                protected.append(entry.source_path)
        try:
            removed = int(db_engine.clear_cache(protected))
        except Exception as exc:
            raise UserInputError("No se pudo limpiar la caché.",
                                 details={"reason": str(exc)}) from exc
        return {"removed": removed}

    def free_memory(self, hard: bool = False) -> dict[str, Any]:
        """Release Python caches; ``hard`` also resets the DuckDB connection."""
        import gc

        from core import engine as db_engine
        from core.memory_budget import system_memory

        if hard:
            try:
                db_engine.reset_conn()
            except Exception:
                pass
        collected = gc.collect()
        if hard:
            collected += gc.collect()
        memory = system_memory()
        return {"collected": int(collected), "hard": bool(hard),
                "available_bytes": memory.available}

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


def _safe_export_name(value: str) -> str:
    """Keep user labels readable and safe as one Windows path component."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(value)).strip(" .")[:100]
    if cleaned.upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                           *[f"LPT{i}" for i in range(1, 10)]}:
        cleaned = f"{cleaned}-análisis"
    return cleaned


def _analysis_caution(analysis_id: str) -> str | None:
    if "causal" in analysis_id:
        return "La validez depende de controles no afectados y supuestos PRE/POST."
    if "geox" in analysis_id or "geo-test" in analysis_id:
        return "Diseño PRE-test; no estima por sí solo un efecto posterior."
    if "regression" in analysis_id:
        return "La atribución predictiva no implica causalidad."
    return None
