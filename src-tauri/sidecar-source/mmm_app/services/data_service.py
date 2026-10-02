"""Operaciones de datos sin dependencias de la interfaz.

Las funciones de este módulo conservan las mismas garantías de carga,
conversión y cancelación que usaba ``MMMApp``. Los callbacks recibidos son
solo para informar de progreso o registro desde el borde de la aplicación.
"""

from pathlib import Path
import time

import pandas as pd

from core import memory_budget
from core.diagnostics import debug_enabled, log_debug
from core.loader import (
    _DATE_KEYWORDS,
    _lossless_numeric,
    coerce_date_column,
    get_date_columns,
    load_file,
)
from core.tasks import TaskCancelled
from services.active_dataset import ActiveDataset


def preview_page(df: pd.DataFrame, page: int, size: int = 200):
    """Select one bounded, zero-based page without copying the full input."""
    if size < 1:
        raise ValueError("Page size must be positive")
    total_pages = max(1, (len(df) + size - 1) // size)
    page = min(max(0, page), total_pages - 1)
    return df.iloc[page * size:(page + 1) * size], page, total_pages


def limited_unique_strings(series: pd.Series, limit: int = 500,
                           cancel=None) -> list[str | None] | None:
    """Devuelve valores de filtro hasta el límite visual, sin materializar
    una columna completa de alta cardinalidad como strings."""
    values = set()
    for start in range(0, len(series), 50_000):
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        block = series.iloc[start:start + 50_000]
        if block.isna().any():
            values.add(None)
        values.update("" if not value.strip() else value
                      for value in block.dropna().astype(str).unique())
        if len(values) > limit:
            return None
    return sorted(values, key=lambda value: (value is not None, value or ""))


def read_dataset(path: Path, retained: int, log, *,
                 ensure_load_fits=memory_budget.ensure_load_fits,
                 loader=load_file,
                 lazy_opener=ActiveDataset.open_file) -> pd.DataFrame | ActiveDataset:
    """Carga un archivo sin iniciar una materialización que no cabe en RAM.

    Los formatos con backend lazy pasan directamente a DuckDB cuando el
    preflight los considera demasiado grandes. Si la RAM cambia entre el
    preflight y la lectura, se recupera igualmente en esa misma ruta.
    """
    path = Path(path)
    ext = path.suffix.lower()
    plan = memory_budget.plan_load(path, retained)

    def open_from_disk(reason: str) -> ActiveDataset:
        log(reason)
        try:
            result = lazy_opener(path)
        except (MemoryError, OSError) as exc:
            raise MemoryError(
                "No se pudo activar la lectura desde disco. Libera recursos "
                "y vuelve a intentarlo; el archivo no se ha cargado de forma "
                "parcial.") from exc
        if debug_enabled():
            log_debug("ENGINE", "lazy file open", backend=result.backend,
                      source=path.name, columns=len(result.columns))
        return result

    if plan.uses_disk:
        return open_from_disk(f"[DATA] {plan.message}")
    if plan.mode == "unavailable":
        raise MemoryError(plan.message)

    try:
        ensure_load_fits(path, retained)
        result = loader(path, log=log)
    except MemoryError as exc:
        if ext in memory_budget.LAZY_FILE_EXTENSIONS:
            fallback = memory_budget.plan_load(path, retained)
            message = fallback.message if fallback.uses_disk else (
                "La RAM cambió durante la carga. Se trabajará desde disco "
                "con DuckDB; algunas consultas pueden tardar más.")
            return open_from_disk(f"[DATA] {message}")
        raise MemoryError(plan.message) from exc
    if debug_enabled():
        log_debug("ENGINE", "Pandas full read", source=path.name,
                  rows=len(result), columns=len(result.columns),
                  format=ext)
    return result


def ensure_date_sorted(df: pd.DataFrame, name: str, *, log=print):
    """Normaliza la primera fecha y ordena establemente si hace falta."""
    if df is None or len(df) == 0:
        return df

    try:
        date_cols = get_date_columns(df)
    except Exception:
        date_cols = []
    if not date_cols:
        return df

    primary = date_cols[0]
    if primary not in df.columns:
        return df

    try:
        series = df[primary]
        if not pd.api.types.is_datetime64_any_dtype(series):
            try:
                if getattr(series.dt, "tz", None) is not None:
                    series = series.dt.tz_convert(None)
            except Exception:
                pass
            series = pd.to_datetime(series, errors="coerce")
            try:
                df[primary] = series
            except Exception:
                # El fallback mantiene el único copy que ya existía.
                df = df.copy()
                df[primary] = series

        head = df[primary].head(2000).dropna()
        tail = df[primary].tail(2000).dropna()
        if head.is_monotonic_increasing and tail.is_monotonic_increasing:
            return df

        log(f"[activate] Ordenando '{name}' por '{primary}'...")
        started = time.time()
        df = df.sort_values(primary, ascending=True, kind="mergesort")
        df = df.reset_index(drop=True)
        log(f"[activate] Ordenado en {time.time() - started:.2f}s")
        return df
    except Exception as exc:
        log(f"[activate] Fallo ordenando '{primary}': {exc}")
        return df


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def filter_values(df: pd.DataFrame, filters):
    """Aplica filtros preservando el DataFrame fuente."""
    for col, allowed in filters.items():
        if col not in df.columns or allowed is None:
            continue
        try:
            if pd.api.types.is_numeric_dtype(df[col]):
                allowed_num = {float(value) for value in allowed
                               if _is_number(value)}
                if allowed_num:
                    df = df[df[col].isin(allowed_num)]
                continue
            if isinstance(df[col].dtype, pd.CategoricalDtype):
                df = df[df[col].astype(object).isin(allowed)]
                continue
            df = df[df[col].isin(allowed)]
        except Exception:
            df = df[df[col].astype(str).isin(
                [str(value) for value in allowed])]
    return df


def prepare_df_for_analysis(df: pd.DataFrame, column_types=None) -> pd.DataFrame:
    """Prepara una copia local solo cuando alguna columna debe convertirse."""
    if df is None or len(df) == 0:
        return df
    column_types = column_types or {}

    needs_copy = False
    for col, kind in column_types.items():
        if col not in df.columns:
            continue
        if kind == "fecha" and not pd.api.types.is_datetime64_any_dtype(df[col]):
            needs_copy = True
            break
        if kind == "numero" and not pd.api.types.is_numeric_dtype(df[col]):
            needs_copy = True
            break
    if not needs_copy:
        return df

    out = df.copy()
    for col, kind in column_types.items():
        if col not in out.columns:
            continue
        try:
            if kind == "fecha" and not pd.api.types.is_datetime64_any_dtype(out[col]):
                out[col] = coerce_date_column(out[col], col)
            elif kind == "numero" and not pd.api.types.is_numeric_dtype(out[col]):
                converted = _lossless_numeric(out[col])
                if converted is None:
                    raise ValueError("contiene valores no numéricos")
                out[col] = converted
        except Exception as exc:
            print(f"[prepare] {col} → {kind}: {exc}")
    return out


def detect_types(df: pd.DataFrame):
    """Infiere tipos con una muestra de hasta 50.000 filas."""
    sample = df if len(df) <= 50_000 else df.head(50_000)
    types = {}
    for col in df.columns:
        series = sample[col]
        kind = "texto"
        if pd.api.types.is_datetime64_any_dtype(series):
            kind = "fecha"
        elif pd.api.types.is_bool_dtype(series):
            kind = "categorica"
        elif pd.api.types.is_numeric_dtype(series):
            kind = "numero"
        elif isinstance(series.dtype, pd.CategoricalDtype):
            kind = "categorica"
        else:
            name = str(col).lower()
            if any(keyword in name for keyword in _DATE_KEYWORDS):
                try:
                    pd.to_datetime(series, errors="raise")
                    kind = "fecha"
                except Exception:
                    pass
            if kind == "texto":
                try:
                    unique = series.nunique(dropna=True)
                    ratio = unique / max(len(sample), 1)
                    if unique <= 50 or ratio < 0.2:
                        kind = "categorica"
                except Exception:
                    pass
        types[col] = kind
    return types


def convert_types(source: pd.DataFrame, types, cancel=None) -> pd.DataFrame:
    """Convierte sobre una copia y aborta cooperativamente si se solicita."""
    df = source.copy()
    for col, kind in types.items():
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
        if col not in df.columns:
            continue
        try:
            if kind == "fecha":
                df[col] = coerce_date_column(df[col], col)
            elif kind == "numero":
                converted = _lossless_numeric(df[col])
                if converted is None:
                    raise ValueError(f"'{col}' contiene valores no numéricos")
                df[col] = converted
            elif kind == "categorica":
                df[col] = df[col].astype("category")
        except Exception as exc:
            raise ValueError(
                f"No se pudo convertir '{col}' a {kind}: {exc}") from exc
    return df
