"""A logical dataset that can stay on disk instead of becoming a DataFrame."""

from dataclasses import dataclass, replace
import hashlib
from collections import OrderedDict
from pathlib import Path
from typing import Any
import shutil
import time

import pandas as pd

from core import engine
from core.atomic import atomic_output, write_json_atomic
from core.tasks import TaskCancelled
from core.diagnostics import debug_enabled, log_debug, memory_snapshot


def _ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


_STATS_CACHE = OrderedDict()


def _is_memory_error(exc: BaseException) -> bool:
    """DuckDB versions do not expose a stable public OOM exception class."""
    if isinstance(exc, MemoryError):
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return ("outofmemory" in name or "out of memory" in text
            or "memory limit" in text)


@dataclass(frozen=True)
class ActiveDataset:
    source: Path | pd.DataFrame | str
    backend: str
    columns: tuple[str, ...]
    types: tuple[str, ...] = ()
    file_size: int = 0
    file_mtime_ns: int = 0
    filters: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    projection: tuple[str, ...] | None = None
    date_bounds: tuple[str, str | None, str | None] | None = None
    origin: Path | None = None
    origin_size: int = 0
    origin_mtime_ns: int = 0
    source_params: tuple[Any, ...] = ()
    parent: "ActiveDataset | None" = None
    version_hint: str | None = None

    @classmethod
    def open_file(cls, path: Path) -> "ActiveDataset":
        path = Path(path).resolve()
        if path.suffix.lower() not in {".csv", ".tsv", ".txt", ".parquet"}:
            raise ValueError("Backend lazy disponible para CSV, TSV y Parquet")
        stat = path.stat()
        original = path
        if path.suffix.lower() != ".parquet" and engine.is_cached(path):
            path = engine.parquet_path_for(path)
        backend = "parquet" if path.suffix.lower() == ".parquet" else "duckdb_csv"
        source_stat = path.stat()
        probe = cls(path, backend, (), file_size=source_stat.st_size,
                    file_mtime_ns=source_stat.st_mtime_ns,
                    origin=original if original != path else None,
                    origin_size=stat.st_size, origin_mtime_ns=stat.st_mtime_ns)
        try:
            rows = engine.get_conn().execute(
                f"DESCRIBE SELECT * FROM {probe._source_sql()}").fetchall()
        except Exception as exc:
            if _is_memory_error(exc):
                raise MemoryError(
                    "No hay recursos suficientes para preparar la lectura "
                    "desde disco. Cierra otras aplicaciones y vuelve a "
                    "intentarlo; el dataset no se ha cargado parcialmente.") from exc
            raise
        return replace(probe,
                       columns=tuple(str(row[0]) for row in rows),
                       types=tuple(str(row[1]) for row in rows))

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> "ActiveDataset":
        return cls(frame, "pandas", tuple(str(c) for c in frame.columns),
                   tuple(str(dtype) for dtype in frame.dtypes))

    @classmethod
    def from_query(cls, sql: str, params: tuple, parent: "ActiveDataset",
                   version_hint: str) -> "ActiveDataset":
        parent._check_source()
        cursor = engine.get_conn().execute(
            f"SELECT * FROM ({sql}) AS _table_result LIMIT 0", params)
        description = cursor.description
        return cls(sql, "query", tuple(str(item[0]) for item in description),
                   tuple(str(item[1]) for item in description),
                   source_params=tuple(params), parent=parent,
                   version_hint=version_hint)

    def version_token(self) -> str:
        if self.backend == "pandas":
            identity = (id(self.source), self.source.shape, self.types)
        elif self.backend == "registered":
            identity = (self.version_hint or self.source, self.columns, self.types)
        elif self.backend == "query":
            identity = (self.version_hint, self.parent.version_token(),
                        self.source_params)
        else:
            identity = (str(self.source), self.file_size, self.file_mtime_ns,
                        str(self.origin), self.origin_size, self.origin_mtime_ns)
        state = (identity, self.filters, self.projection, self.date_bounds)
        return hashlib.sha256(repr(state).encode("utf-8")).hexdigest()

    def resource_status(self) -> dict[str, str | bool]:
        """Estado apto para la interfaz, sin forzar lectura de datos.

        El controlador puede mostrar ``message`` al activar un dataset lazy;
        no debe interpretar el backend ni consultar filas para hacerlo.
        """
        if self.backend == "pandas":
            return {"mode": "memory", "uses_disk": False,
                    "message": "Dataset cargado en memoria."}
        source_name = (self.origin or self.source)
        return {
            "mode": "disk", "uses_disk": True,
            "message": (
                f"'{Path(source_name).name}' se consulta desde disco con "
                "DuckDB. El preview y las consultas pueden tardar más si "
                "la RAM está saturada."),
        }

    def _params(self, outer=()):
        return [*self.source_params, *outer]

    def _source_sql(self) -> str:
        if self.backend == "pandas":
            raise ValueError("A Pandas dataset has no SQL source")
        if self.backend == "registered":
            return _ident(self.source)
        if self.backend == "query":
            return f"({self.source})"
        path = str(self.source).replace("'", "''")
        if self.backend == "parquet":
            return f"read_parquet('{path}')"
        return f"read_csv_auto('{path}', sample_size=20480, ignore_errors=false)"

    def _check_source(self) -> None:
        if self.backend == "query":
            self.parent._check_source()
            return
        if self.backend in {"pandas", "registered"}:
            return
        stat = self.source.stat()
        if stat.st_size != self.file_size or stat.st_mtime_ns != self.file_mtime_ns:
            raise RuntimeError("El archivo de origen cambió; vuelve a cargarlo")
        if self.origin is not None:
            original = self.origin.stat()
            if (original.st_size != self.origin_size or
                    original.st_mtime_ns != self.origin_mtime_ns):
                raise RuntimeError("El archivo de origen cambió; vuelve a cargarlo")

    def cache_as_parquet(self, cancel=None, progress=None) -> Path:
        """Stream CSV/TSV to a validated Parquet cache, with atomic writes."""
        if self.backend != "duckdb_csv":
            raise ValueError("Solo CSV/TSV directos se convierten a caché Parquet")
        self._check_source()
        source = Path(self.source)
        cache = engine.parquet_path_for(source)
        cache.parent.mkdir(parents=True, exist_ok=True)
        # Reserve for the source-sized temporary file plus free space for OS.
        free = shutil.disk_usage(cache.parent).free
        if free < source.stat().st_size * 2:
            raise OSError("Espacio insuficiente para convertir CSV a Parquet")
        before = engine._source_metadata(source)
        self.export(cache, "parquet", cancel, progress)
        if engine._source_metadata(source) != before:
            cache.unlink(missing_ok=True)
            raise RuntimeError("El origen cambió durante la conversión")
        write_json_atomic(engine._cache_metadata_path(cache), before)
        return cache

    def with_filters(self, filters: dict[str, set | list | tuple]) -> "ActiveDataset":
        for column in filters:
            if column not in self.columns:
                raise KeyError(column)
        normalized = tuple((column, tuple(sorted(values, key=str)))
                           for column, values in sorted(filters.items()))
        return replace(self, filters=normalized)

    def with_columns(self, columns: list[str]) -> "ActiveDataset":
        if any(col not in self.columns for col in columns):
            raise KeyError("Columna no encontrada")
        return replace(self, projection=tuple(columns))

    def reset(self) -> "ActiveDataset":
        return replace(self, filters=(), projection=None, date_bounds=None)

    def with_date_range(self, column: str, start: str | None,
                        end: str | None) -> "ActiveDataset":
        if column not in self.columns:
            raise KeyError(column)
        start = str(pd.Timestamp(start).date()) if start else None
        end = str(pd.Timestamp(end).date()) if end else None
        if start and end and start > end:
            raise ValueError("El inicio del rango es posterior al fin")
        return replace(self, date_bounds=(column, start, end))

    def _where(self):
        clauses, params = [], []
        for column, values in self.filters:
            if not values:
                clauses.append("FALSE")
            else:
                clauses.append(f"{_ident(column)} IN ({','.join('?' for _ in values)})")
                params.extend(values)
        if self.date_bounds:
            column, start, end = self.date_bounds
            date_sql = f"TRY_CAST({_ident(column)} AS DATE)"
            if start:
                clauses.append(f"{date_sql} >= CAST(? AS DATE)")
                params.append(start)
            if end:
                clauses.append(f"{date_sql} <= CAST(? AS DATE)")
                params.append(end)
        return (" WHERE " + " AND ".join(clauses) if clauses else "", params)

    def _frame(self) -> pd.DataFrame:
        frame = self.source
        for column, values in self.filters:
            frame = frame.loc[frame[column].isin(values)]
        if self.date_bounds:
            column, start, end = self.date_bounds
            dates = pd.to_datetime(frame[column], errors="coerce")
            if start:
                frame = frame.loc[dates >= pd.Timestamp(start)]
                dates = dates.loc[frame.index]
            if end:
                frame = frame.loc[dates <= pd.Timestamp(end)]
        if self.projection is not None:
            frame = frame.loc[:, list(self.projection)]
        return frame

    def row_count(self, cancel=None) -> int:
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        if self.backend == "pandas":
            return len(self._frame())
        self._check_source()
        where, params = self._where()
        count = engine.get_conn().execute(
            f"SELECT COUNT(*) FROM {self._source_sql()}{where}", self._params(params)
        ).fetchone()[0]
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        return int(count)

    def preview(self, limit: int = 200, offset: int = 0, cancel=None) -> pd.DataFrame:
        if not 0 <= limit <= 1000 or offset < 0:
            raise ValueError("Preview limitado a 1000 filas por página")
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        if self.backend == "pandas":
            return self._frame().iloc[offset:offset + limit]
        self._check_source()
        selected = self.projection or self.columns
        if not selected:
            return pd.DataFrame()
        where, params = self._where()
        sql = (f"SELECT {', '.join(_ident(c) for c in selected)} "
               f"FROM {self._source_sql()}{where} LIMIT {limit} OFFSET {offset}")
        started = time.perf_counter()
        result = engine.get_conn().execute(sql, self._params(params)).fetchdf()
        if debug_enabled():
            log_debug("QUERY", "preview", backend=self.backend,
                      version=self.version_token()[:12],
                      query=hashlib.sha256(sql.encode()).hexdigest()[:12],
                      elapsed_ms=round((time.perf_counter()-started)*1000, 1),
                      output_rows=len(result), output_cols=len(result.columns),
                      **memory_snapshot())
            log_debug("MATERIALIZE", "fetchdf bounded preview",
                      rows=len(result), columns=len(result.columns), limit=limit)
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        return result

    def stats(self, cancel=None, date_column: str | None = None) -> dict:
        """Whole-active-dataset row count and column null counts."""
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        if self.backend == "pandas":
            frame = self._frame()
            return {"rows": len(frame), "columns": len(frame.columns),
                    "nulls": {str(c): int(frame[c].isna().sum())
                              for c in frame.columns}}
        self._check_source()
        if date_column is not None and date_column not in self.columns:
            raise KeyError(date_column)
        cache_key = (self.version_token(), date_column)
        if cache_key in _STATS_CACHE:
            _STATS_CACHE.move_to_end(cache_key)
            log_debug("QUERY", "stats cache HIT", version=cache_key[0][:12])
            cached = _STATS_CACHE[cache_key]
            return {**cached, "nulls": dict(cached["nulls"])}
        log_debug("QUERY", "stats cache MISS", version=cache_key[0][:12])
        selected = self.projection or self.columns
        where, params = self._where()
        expressions = ["COUNT(*)"] + [
            f"COUNT(*) - COUNT({_ident(c)})" for c in selected]
        if date_column:
            date_sql = f"TRY_CAST({_ident(date_column)} AS DATE)"
            expressions.extend([f"MIN({date_sql})", f"MAX({date_sql})"])
        started = time.perf_counter()
        conn = engine.get_conn()
        source_sql = f" FROM {self._source_sql()}{where}"
        query_params = self._params(params)

        def fetch(aggregates):
            if cancel is not None and cancel.is_set():
                raise TaskCancelled()
            return conn.execute(
                f"SELECT {', '.join(aggregates)}{source_sql}",
                query_params).fetchone()

        try:
            values = fetch(expressions)
        except Exception as exc:
            if not _is_memory_error(exc):
                raise
            # Una agregación de decenas de columnas puede agotar el límite
            # conjunto aun cuando cada columna individual cabe. Mantener el
            # resultado exacto y reducir buffers/threads antes de reintentar.
            log_debug("MEM", "stats retry in column batches",
                      columns=len(selected), error=type(exc).__name__)
            previous_threads = None
            try:
                previous_threads = conn.execute(
                    "SELECT current_setting('threads')").fetchone()[0]
                conn.execute("SET threads=1")
            except Exception:
                previous_threads = None
            try:
                row_count = int(fetch(["COUNT(*)"])[0])
                null_values = []
                offset = 0
                batch_size = 4
                while offset < len(selected):
                    columns = selected[offset:offset + batch_size]
                    try:
                        batch = fetch([
                            f"COUNT(*) - COUNT({_ident(column)})"
                            for column in columns])
                    except Exception as batch_exc:
                        if (not _is_memory_error(batch_exc)
                                or batch_size == 1):
                            raise MemoryError(
                                "No hay memoria suficiente para calcular las "
                                "estadísticas. Cierra otras aplicaciones o "
                                "reduce los filtros activos.") from batch_exc
                        batch_size = max(1, batch_size // 2)
                        continue
                    null_values.extend(int(value) for value in batch)
                    offset += len(columns)
                date_values = (fetch([
                    f"MIN({date_sql})", f"MAX({date_sql})"])
                    if date_column else ())
                values = (row_count, *null_values, *date_values)
            finally:
                if previous_threads is not None:
                    conn.execute(f"SET threads={int(previous_threads)}")
        log_debug("QUERY", "active stats", backend=self.backend,
                  seconds=round(time.perf_counter() - started, 3),
                  rows=int(values[0]), columns=len(selected))
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        result = {"rows": int(values[0]), "columns": len(selected),
                  "nulls": dict(zip(selected, map(int, values[1:1+len(selected)])))}
        if date_column:
            result["date_range"] = (values[-2], values[-1])
        _STATS_CACHE[cache_key] = result
        if len(_STATS_CACHE) > 32:
            _STATS_CACHE.popitem(last=False)
        return {**result, "nulls": dict(result["nulls"])}

    def date_range(self, column: str, cancel=None):
        if column not in self.columns:
            raise KeyError(column)
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        if self.backend == "pandas":
            dates = pd.to_datetime(self._frame()[column], errors="coerce")
            return dates.min(), dates.max()
        self._check_source()
        where, params = self._where()
        date_sql = f"TRY_CAST({_ident(column)} AS DATE)"
        result = engine.get_conn().execute(
            f"SELECT MIN({date_sql}), MAX({date_sql}) "
            f"FROM {self._source_sql()}{where}", self._params(params)).fetchone()
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        return result

    def analysis_frame(self, cancel=None, reserved_bytes: int = 0) -> pd.DataFrame:
        """Materialize an active subset only when analysis copies fit RAM."""
        if self.backend == "pandas":
            return self._frame()
        from core import memory_budget
        count = self.row_count(cancel)
        if not count:
            return self.preview(limit=0, cancel=cancel)
        sample = self.preview(limit=min(1000, count), cancel=cancel)
        row_bytes = max(1, int(sample.memory_usage(deep=True).sum()
                               / max(len(sample), 1)))
        # Dialog/model preparation can retain several copies. Reserve half of
        # Pandas' joint budget for other datasets and running Windows apps.
        estimated_peak = row_bytes * count * 6
        available = max(0, memory_budget.pandas_limit_bytes() // 2
                        - reserved_bytes)
        log_debug("DATA", "active analysis", backend=self.backend,
                  rows=count, columns=len(self.projection or self.columns))
        log_debug("MEM", "analysis preflight", estimated_peak=estimated_peak,
                  pandas_budget=available)
        if estimated_peak > available:
            raise MemoryError(
                f"Análisis del dataset activo rechazado por memoria: "
                f"pico estimado {estimated_peak / 1024**3:.1f} GiB; "
                f"presupuesto {available / 1024**3:.1f} GiB. "
                "Filtra o agrega antes de analizar.")
        self._check_source()
        selected = self.projection or self.columns
        where, params = self._where()
        sql = (f"SELECT {', '.join(_ident(c) for c in selected)} "
               f"FROM {self._source_sql()}{where}")
        started = time.perf_counter()
        try:
            result = engine.get_conn().execute(
                sql, self._params(params)).fetchdf()
        except Exception as exc:
            if _is_memory_error(exc):
                raise MemoryError(
                    "La tabla sigue disponible desde disco, pero este análisis "
                    "necesita más RAM para materializar sus datos. Filtra o "
                    "agrega antes de analizar.") from exc
            raise
        log_debug("QUERY", "analysis materialize",
                  elapsed_ms=round((time.perf_counter()-started)*1000, 1),
                  query=hashlib.sha256(sql.encode()).hexdigest()[:12])
        if debug_enabled():
            log_debug("MATERIALIZE", "fetchdf complete active subset",
                      rows=len(result), columns=len(result.columns),
                      bytes=int(result.memory_usage(deep=True).sum()))
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        return result

    def export(self, path: Path, fmt: str, cancel=None, progress=None) -> Path:
        """Stream a filtered file without materializing all rows in Pandas."""
        if fmt not in {"csv", "parquet"}:
            raise ValueError("Export lazy disponible como CSV o Parquet")
        if self.backend == "pandas":
            from core.exporter import export_dataframe
            export_dataframe(self._frame(), path, fmt, cancel=cancel,
                             progress=progress)
            return Path(path)
        self._check_source()
        selected = self.projection or self.columns
        if not selected:
            raise ValueError("No hay columnas para exportar")
        where, params = self._where()
        sql = (f"SELECT {', '.join(_ident(c) for c in selected)} "
               f"FROM {self._source_sql()}{where}")
        import pyarrow.csv as pacsv
        import pyarrow.parquet as pq
        reader = engine.get_conn().execute(
            sql, self._params(params)).to_arrow_reader(100_000)
        path = Path(path)
        with atomic_output(path) as temporary:
            with temporary.open("wb") as stream:
                if fmt == "parquet":
                    writer = pq.ParquetWriter(stream, reader.schema,
                                              compression="zstd")
                else:
                    writer = pacsv.CSVWriter(stream, reader.schema)
                written = 0
                try:
                    for batch in reader:
                        if cancel is not None and cancel.is_set():
                            raise TaskCancelled()
                        writer.write_batch(batch)
                        written += batch.num_rows
                        if progress is not None:
                            progress(f"Exportadas {written:,} filas")
                    if cancel is not None and cancel.is_set():
                        raise TaskCancelled()
                finally:
                    writer.close()
        return path

    def profile_rows(self, date_column: str | None = None, cancel=None) -> dict:
        """Exact active-row and any-null counts; optional full date range."""
        if self.backend == "pandas":
            frame = self._frame()
            result = {"rows": len(frame),
                      "rows_with_null": int(frame.isna().any(axis=1).sum())}
            if date_column:
                result["date_range"] = self.date_range(date_column, cancel)
            return result
        self._check_source()
        cols = self.projection or self.columns
        where, params = self._where()
        null_condition = " OR ".join(f"{_ident(c)} IS NULL" for c in cols)
        expressions = ["COUNT(*)",
                       f"COUNT(*) FILTER (WHERE {null_condition})"
                       if null_condition else "0"]
        if date_column:
            if date_column not in self.columns:
                raise KeyError(date_column)
            date_sql = f"TRY_CAST({_ident(date_column)} AS DATE)"
            expressions.extend([f"MIN({date_sql})", f"MAX({date_sql})"])
        values = engine.get_conn().execute(
            f"SELECT {', '.join(expressions)} FROM {self._source_sql()}{where}",
            self._params(params)).fetchone()
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        result = {"rows": int(values[0]), "rows_with_null": int(values[1])}
        if date_column:
            result["date_range"] = values[2], values[3]
        return result

    def profile_columns(self, kind: str, cancel=None) -> list[dict]:
        """Full-source numeric summaries or approximate category cardinality."""
        if kind not in {"numeric", "categorical"}:
            raise ValueError(kind)
        selected = self.projection or self.columns
        numeric_tokens = ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "HUGEINT")
        columns = [c for c in selected if
                   (any(token in self.types[self.columns.index(c)].upper()
                        for token in numeric_tokens)) == (kind == "numeric")]
        if self.backend == "pandas":
            frame = self._frame()
            result = []
            for column in columns:
                series = frame[column]
                item = {"column": column, "nulls": int(series.isna().sum())}
                if kind == "numeric":
                    item.update(min=series.min(), max=series.max(),
                                avg=series.mean())
                else:
                    item["distinct_approx"] = int(series.nunique(dropna=True))
                result.append(item)
            return result
        self._check_source()
        if not columns:
            return []
        expressions = []
        for column in columns:
            quoted = _ident(column)
            expressions.append(f"COUNT(*) - COUNT({quoted})")
            if kind == "numeric":
                expressions.extend([f"MIN({quoted})", f"MAX({quoted})",
                                    f"AVG(TRY_CAST({quoted} AS DOUBLE))"])
            else:
                expressions.append(f"APPROX_COUNT_DISTINCT({quoted})")
        where, params = self._where()
        values = engine.get_conn().execute(
            f"SELECT {', '.join(expressions)} FROM {self._source_sql()}{where}",
            self._params(params)).fetchone()
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        result, index = [], 0
        for column in columns:
            item = {"column": column, "nulls": int(values[index])}
            index += 1
            if kind == "numeric":
                item.update(min=values[index], max=values[index + 1],
                            avg=values[index + 2])
                index += 3
            else:
                item["distinct_approx"] = int(values[index])
                index += 1
            result.append(item)
        return result

    def distinct_values(self, column: str, limit: int = 500,
                        cancel=None) -> list[str] | None:
        """Read at most limit+1 distinct values from the complete source."""
        if column not in self.columns:
            raise KeyError(column)
        if limit < 1:
            raise ValueError("El límite debe ser positivo")
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        if self.backend == "pandas":
            from services.data_service import limited_unique_strings
            return limited_unique_strings(self._frame()[column], limit, cancel)
        self._check_source()
        where, params = self._where()
        quoted = _ident(column)
        rows = engine.get_conn().execute(
            f"SELECT DISTINCT CAST({quoted} AS VARCHAR) "
            f"FROM {self._source_sql()}{where} "
            f"AND {quoted} IS NOT NULL LIMIT {limit + 1}"
            if where else
            f"SELECT DISTINCT CAST({quoted} AS VARCHAR) "
            f"FROM {self._source_sql()} WHERE {quoted} IS NOT NULL "
            f"LIMIT {limit + 1}", self._params(params)).fetchall()
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        if len(rows) > limit:
            return None
        return sorted(str(row[0]) for row in rows)


@dataclass(frozen=True)
class LazyLoaded:
    dataset: ActiveDataset
    stats: dict
    preview: pd.DataFrame
