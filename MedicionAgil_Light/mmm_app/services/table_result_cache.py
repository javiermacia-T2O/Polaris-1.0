"""Caché Parquet verificable para resultados completos del constructor."""

from __future__ import annotations

import json
from pathlib import Path

from core import engine, memory_budget
from core.atomic import atomic_output, write_json_atomic
from core.diagnostics import log_debug
from core.tasks import TaskCancelled
from services.active_dataset import ActiveDataset


def _file_backed(view: ActiveDataset) -> bool:
    source = view
    while source.backend == "query" and source.parent is not None:
        source = source.parent
    return source.backend in {"parquet", "duckdb_csv"}


def _cache_path(view: ActiveDataset) -> Path:
    return engine.CACHE_DIR / "table_results" / (
        f"table-{view.version_token()}.parquet")


def _valid_cache(path: Path, token: str) -> ActiveDataset | None:
    metadata_path = path.with_suffix(".json")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        stat = path.stat()
        if (metadata.get("version") != token or stat.st_size == 0
                or metadata.get("size") != stat.st_size
                or metadata.get("mtime_ns") != stat.st_mtime_ns):
            return None
        return ActiveDataset.open_file(path)
    except Exception:
        # Una salida Parquet dañada también debe recalcularse. Las excepciones
        # del lector DuckDB no heredan necesariamente de OSError/ValueError.
        return None


def materialize_table_result(view: ActiveDataset, *, cancel=None,
                             progress=None) -> ActiveDataset:
    """Publica una vista de tabla una sola vez sin crear un DataFrame grande.

    El token incluye la versión del origen y la receta. El Parquet solo se
    reutiliza con metadatos coincidentes y origen aún válido. Se escribe en
    una ruta temporal y se publica después de verificar la fuente y la
    cancelación. En caso de OOM se reintenta una vez con un hilo DuckDB.
    """
    if view.backend != "query":
        return view
    if cancel is not None and cancel.is_set():
        raise TaskCancelled()
    view._check_source()
    token = view.version_token()
    path = _cache_path(view)
    cached = _valid_cache(path, token)
    if cached is not None:
        if progress is not None:
            progress((75, "Tabla recuperada de la caché Parquet"))
        return cached

    path.parent.mkdir(parents=True, exist_ok=True)
    limit = memory_budget.table_query_duckdb_limit_bytes()
    if limit < 128 * 1024**2:
        raise MemoryError("Memoria libre insuficiente para construir la tabla. "
                          "Cierra otras aplicaciones y vuelve a intentarlo.")
    conn = engine.get_conn()
    prior_limit = conn.execute(
        "SELECT current_setting('memory_limit')").fetchone()[0]
    prior_threads = int(conn.execute(
        "SELECT current_setting('threads')").fetchone()[0])
    limit_mib = max(128, limit // (1024**2))
    if progress is not None:
        progress((20, "Calculando y guardando la tabla en Parquet..."))
    try:
        conn.execute(f"SET memory_limit='{limit_mib}MB'")
        with atomic_output(path) as pending:
            def report(message):
                if progress is not None:
                    progress((45, str(message)))

            try:
                view.export(pending, "parquet", cancel=cancel,
                            progress=report)
            except Exception as exc:
                if type(exc).__name__ != "OutOfMemoryException":
                    raise
                log_debug("MEM", "table Parquet retry", threads=1,
                          memory_limit_mib=limit_mib)
                conn.execute("SET threads=1")
                if progress is not None:
                    progress((45, "Reintentando con menor uso de memoria..."))
                view.export(pending, "parquet", cancel=cancel,
                            progress=report)
            view._check_source()
            if cancel is not None and cancel.is_set():
                raise TaskCancelled()
        stat = path.stat()
        write_json_atomic(path.with_suffix(".json"), {
            "version": token, "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        })
    finally:
        if _file_backed(view):
            # Libera el hash de agregación antes de leer el Parquet resultante.
            engine.reset_conn()
        else:
            conn.execute(f"SET threads={prior_threads}")
            conn.execute(f"SET memory_limit='{prior_limit}'")
    if progress is not None:
        progress((75, "Tabla guardada; cargando vista previa..."))
    return ActiveDataset.open_file(path)


__all__ = ["materialize_table_result"]
