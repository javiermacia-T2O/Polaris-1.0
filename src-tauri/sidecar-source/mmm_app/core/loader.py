"""Carga de archivos, parseo inteligente y utilidades de fecha."""

from pathlib import Path
import pandas as pd
import numpy as np

from core import engine as _engine
from core import memory_budget

SUPPORTED_EXTENSIONS = {".xlsx", ".xlsm", ".xls",
                         ".csv", ".tsv", ".txt", ".parquet"}
_DATE_KEYWORDS = ("fecha", "date", "dia", "day", "week",
                  "semana", "mes", "month", "año", "ano", "year")


# ------------------------------------------------------------------
# CARGA
# ------------------------------------------------------------------
def load_file(path, log=None):
    """Carga un archivo como DataFrame solo cuando el presupuesto lo permite.

    Los llamadores que admiten el modo en disco deben usar
    :func:`services.data_service.read_dataset`; esta función conserva su
    contrato de devolver siempre un DataFrame.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No existe: {path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Formato no soportado: {ext}")

    plan = memory_budget.plan_load(path)
    if plan.mode != "pandas":
        raise MemoryError(f"Carga completa rechazada por memoria: {plan.message}")

    import time as _t
    _t0 = _t.time()

    def _phase(name):
        nonlocal _t0
        dt = _t.time() - _t0
        msg = f"[loader] {name}: {dt:.2f}s"
        (log or print)(msg)
        _t0 = _t.time()

    # --- 1 · Lectura vía engine (Arrow + Parquet cache) ---
    df = _engine.load_dataframe(path, log=log)
    _phase("lectura archivo")

    # --- 2 · Limpieza numérica ---
    df = _clean_numeric_columns(df)
    _phase("limpieza numérica")

    # --- 3 · Parseo de fechas ---
    df = _try_parse_dates(df)
    _phase("parseo fechas")

    # --- 4 · Reordenar columnas ---
    df = _reorder_date_first(df)
    _phase("reorden columnas")

    # --- 5 · Ordenar por fecha (solo DFs manejables) ---
    if len(df) < _engine.UMBRAL_PANDAS:
        date_cols = _find_all_date_columns(df)
        if date_cols:
            primary_date = date_cols[0]
            try:
                df = (df.sort_values(primary_date, ascending=True,
                                      kind="mergesort")
                        .reset_index(drop=True))
            except Exception as e:
                print(f"[loader] No se pudo ordenar: {e}")
        _phase("ordenar por fecha")
    else:
        (log or print)(f"[loader] DF grande ({len(df):,} filas) → "
                       f"se omite sort inicial".replace(",", "."))

    (log or print)(f"[loader] Total: {_t.time()-_t0:.2f}s".replace(",", "."))

    df = _force_numeric_coercion(df)

    return df

def _detect_number_format(sample: pd.Series) -> str:
    """
    Detecta el formato numérico dominante en una muestra.

    Devuelve:
      "euro"           → 1.234,56 / 1.234
      "us"             → 1,234.56 / 1,234
      "comma_decimal"  → 12,34
      "plain"          → 1234 / 1234.56
      "ambiguous"      → no se puede determinar con seguridad
    """
    s = sample.astype(str).str.strip()
    s = s.str.replace(r"[€$£¥\s]", "", regex=True)
    s = s[s.str.match(r"^-?[\d.,]+$", na=False)]
    n = len(s)
    if n == 0:
        return "plain"

    euro_full = s.str.match(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$",
                             na=False).mean()
    us_full = s.str.match(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$",
                           na=False).mean()
    euro_k = s.str.match(r"^-?\d{1,3}(\.\d{3})+$", na=False).mean()
    us_k = s.str.match(r"^-?\d{1,3}(,\d{3})+$", na=False).mean()
    comma_dec = s.str.match(r"^-?\d+,\d+$", na=False).mean()
    plain = s.str.match(r"^-?\d+(\.\d+)?$", na=False).mean()

    if euro_full >= 0.7 or euro_k >= 0.7:
        return "euro"
    if us_full >= 0.7 or us_k >= 0.7:
        return "us"
    if comma_dec >= 0.7:
        return "comma_decimal"
    if plain >= 0.9:
        return "plain"
    return "ambiguous"


def _to_numeric_smart(series: pd.Series) -> pd.Series:
    """
    Convierte a numérico detectando el formato por patrón.

    Preserva los datos: no inventa nada, solo interpreta según lo
    que ve en la muestra.
    """
    if pd.api.types.is_numeric_dtype(series):
        return series

    s = series.astype(str).str.strip()
    s = s.str.replace(r"[€$£¥\s]", "", regex=True)
    s = s.str.replace(r"^\((.+)\)$", r"-\1", regex=True)
    s = s.replace({
        "": np.nan, "nan": np.nan, "NaN": np.nan, "None": np.nan,
        "-": np.nan, "—": np.nan, "N/A": np.nan, "n/a": np.nan,
        "null": np.nan, "NULL": np.nan,
    })

    sample = s.dropna().head(500)
    if sample.empty:
        return pd.to_numeric(s, errors="coerce")

    fmt = _detect_number_format(sample)

    if fmt == "euro":
        s = (s.str.replace(".", "", regex=False)
               .str.replace(",", ".", regex=False))
    elif fmt == "us":
        s = s.str.replace(",", "", regex=False)
    elif fmt == "comma_decimal":
        s = s.str.replace(",", ".", regex=False)
    # "plain" y "ambiguous" → sin cambios

    return pd.to_numeric(s, errors="coerce")


def _lossless_numeric(series: pd.Series, smart: bool = False) -> pd.Series | None:
    """Convierte solo si no aparecen nulos nuevos ni se pierden ceros iniciales."""
    try:
        # Validar por bloques evita crear una copia de texto de toda la columna
        # antes de construir el resultado numérico final.
        for start in range(0, len(series), 100_000):
            block = series.iloc[start:start + 100_000]
            text = block.dropna().astype(str).str.strip()
            if text.str.fullmatch(r"[+-]?0\d+").any():
                return None
            parsed = (_to_numeric_smart(block) if smart else
                      pd.to_numeric(block, errors="coerce"))
            if parsed.notna().sum() != block.notna().sum():
                return None
        return (_to_numeric_smart(series) if smart else
                pd.to_numeric(series, errors="coerce"))
    except Exception:
        return None


def _clean_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convierte a numérico columnas interpretables sin ambigüedad.

    Estrategia:
      - Ya numérica              → no tocar
      - Números planos (1234.56) → pd.to_numeric directo (rápido)
      - Con separadores (1.234)  → _to_numeric_smart (regex)
      - No numérica              → dejar string + avisar
    """
    import time as _t
    sospechosas = []

    for col in df.columns:
        # --- Filtros baratos ---
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        if any(k in str(col).lower() for k in _DATE_KEYWORDS):
            continue

        dtype = df[col].dtype
        if not (pd.api.types.is_string_dtype(dtype)
                or isinstance(dtype, pd.CategoricalDtype)):
            continue

        # --- Muestra (500 valores) ---
        try:
            sample = df[col].head(500).dropna()
        except Exception:
            continue
        if sample.empty:
            continue

        sample_str = sample.astype(str).str.strip()
        sample_clean = sample_str.str.replace(
            r"[€$£¥\s]", "", regex=True)

        # --- ¿Números planos? vía rápida ---
        plain = pd.to_numeric(sample_clean, errors="coerce")
        ratio_plain = plain.notna().sum() / len(sample_clean)

        if ratio_plain >= 0.9:
            t0 = _t.time()
            converted = _lossless_numeric(df[col])
            if converted is not None:
                df[col] = converted
                print(f"[loader] '{col}' → numérico en "
                      f"{_t.time()-t0:.2f}s (plain)")
                continue

        # --- ¿Con separadores? vía regex ---
        try:
            smart = _to_numeric_smart(sample)
            ratio_smart = smart.notna().sum() / len(sample)
        except Exception:
            ratio_smart = 0.0

        if ratio_smart >= 0.9:
            t0 = _t.time()
            converted = _lossless_numeric(df[col], smart=True)
            if converted is not None:
                df[col] = converted
                print(f"[loader] '{col}' → numérico en "
                      f"{_t.time()-t0:.2f}s (separadores)")
                continue

        # --- No convirtió: aviso ---
        has_digit = sample_str.str.contains(r"\d", regex=True).mean()
        if has_digit >= 0.9:
            sospechosas.append(
                (str(col), sample_str.head(3).tolist()))

    if sospechosas:
        print("\n" + "=" * 62)
        print("[loader] ⚠ Columnas con pinta numérica NO convertidas:")
        print("-" * 62)
        for name, ejemplos in sospechosas:
            txt = " | ".join(f"'{v}'" for v in ejemplos)
            print(f"   · {name}: {txt}")
        print("=" * 62 + "\n")

    return df

def _force_numeric_coercion(df: pd.DataFrame) -> pd.DataFrame:
    """
    Última pasada agresiva: intenta convertir CUALQUIER columna
    object/string a numérico si ≥50% de sus valores parsean.

    Se llama al final de load_file para asegurar que ningún
    diálogo se encuentre columnas numéricas disfrazadas de texto.
    """
    convertidas = []
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            continue
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            continue
        if any(k in str(c).lower() for k in _DATE_KEYWORDS):
            continue

        dtype = df[c].dtype
        if not (pd.api.types.is_string_dtype(dtype)
                or isinstance(dtype, pd.CategoricalDtype)):
            continue

        try:
            sample = df[c].dropna().head(1000)
            if sample.empty:
                continue

            # Directo
            direct = pd.to_numeric(sample, errors="coerce")
            ratio_d = direct.notna().sum() / len(sample)
            if ratio_d >= 0.5:
                converted = _lossless_numeric(df[c])
                if converted is not None:
                    df[c] = converted
                    convertidas.append(c)
                    continue

            # Smart (con separadores)
            smart = _to_numeric_smart(sample)
            ratio_s = smart.notna().sum() / len(sample)
            if ratio_s >= 0.5:
                converted = _lossless_numeric(df[c], smart=True)
                if converted is not None:
                    df[c] = converted
                    convertidas.append(c)
        except Exception:
            pass

    if convertidas:
        print(f"[loader] ✅ Coerción final: {convertidas}")
    return df
# ------------------------------------------------------------------
# FECHAS
# ------------------------------------------------------------------
def coerce_date_column(series: pd.Series, colname: str) -> pd.Series:
    """
    Convierte una columna en datetime aplicando heurísticas según el nombre
    y el contenido.
    """
    name = str(colname).lower()

    # --- Ya datetime ---
    if pd.api.types.is_datetime64_any_dtype(series):
        try:
            if getattr(series.dt, "tz", None) is not None:
                series = series.dt.tz_convert(None)
        except Exception:
            pass
        return series

    # --- Numérica con nombre temporal ---
    if pd.api.types.is_numeric_dtype(series):
        try:
            s_int = series.astype("Int64")
        except Exception:
            return series

        # Mes
        if any(k in name for k in ("mes", "month")):
            year = pd.Timestamp.now().year
            result = pd.Series(pd.NaT, index=series.index)
            for m in range(1, 13):
                sel = (s_int == m).fillna(False)
                result[sel] = pd.Timestamp(year=year, month=m, day=1)
            return result

        # Año
        if any(k in name for k in ("año", "ano", "anio", "year")):
            result = pd.Series(pd.NaT, index=series.index)
            valid = s_int.between(1900, 2100).fillna(False)
            for y in s_int[valid].dropna().unique():
                sel = (s_int == y).fillna(False)
                result[sel] = pd.Timestamp(year=int(y), month=1, day=1)
            return result

        # Semana
        if any(k in name for k in ("semana", "week")):
            year = pd.Timestamp.now().year
            result = pd.Series(pd.NaT, index=series.index)
            valid = s_int.between(1, 53).fillna(False)
            for w in s_int[valid].dropna().unique():
                try:
                    ts = pd.Timestamp.fromisocalendar(int(year), int(w), 1)
                    sel = (s_int == w).fillna(False)
                    result[sel] = ts
                except Exception:
                    pass
            return result

        # Trimestre
        if any(k in name for k in ("trimestre", "quarter")):
            year = pd.Timestamp.now().year
            result = pd.Series(pd.NaT, index=series.index)
            for q in range(1, 5):
                sel = (s_int == q).fillna(False)
                result[sel] = pd.Timestamp(year=year, month=(q - 1) * 3 + 1, day=1)
            return result

        if any(k in name for k in ("fecha", "date", "timestamp")):
            try:
                return pd.to_datetime(series, unit="s", errors="coerce")
            except Exception:
                pass

        return series

    # --- Texto → probar parseo directo ---
    try:
        parsed = pd.to_datetime(series, errors="coerce")
        try:
            if getattr(parsed.dt, "tz", None) is not None:
                parsed = parsed.dt.tz_convert(None)
        except Exception:
            pass
        return parsed
    except Exception:
        return series

def _try_parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte a datetime naive (sin timezone) columnas que sugieran fecha."""
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            try:
                if getattr(df[col].dt, "tz", None) is not None:
                    df[col] = df[col].dt.tz_convert(None)
            except Exception:
                pass
            continue

        if any(k in str(col).lower() for k in _DATE_KEYWORDS):
            try:
                df[col] = coerce_date_column(df[col], col)
            except Exception:
                pass
    return df

def _find_all_date_columns(df: pd.DataFrame) -> list[str]:
    """
    Devuelve TODAS las columnas que son fecha, en orden original.

    Para DFs grandes usa solo:
      - dtype ya datetime  → sin comprobación
      - nombre con keyword → comprueba con muestra de 100 valores
    """
    found = []
    n = len(df)

    for c in df.columns:
        # Ya datetime
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            found.append(c)
            continue

        # ¿Nombre sugiere fecha?
        name = str(c).lower()
        if not any(k in name for k in _DATE_KEYWORDS):
            continue

        # Comprobar con muestra, no con 22M valores
        try:
            if n > 500:
                sample = df[c].head(500).dropna()
            else:
                sample = df[c].dropna()

            if sample.empty:
                continue
            pd.to_datetime(sample, errors="raise")
            found.append(c)
        except Exception:
            pass

    return found

def _reorder_date_first(df: pd.DataFrame) -> pd.DataFrame:
    """Mueve todas las columnas de fecha al principio."""
    date_cols = _find_all_date_columns(df)
    if not date_cols:
        return df
    rest = [c for c in df.columns if c not in date_cols]
    return df[date_cols + rest]


def detect_date_column(df: pd.DataFrame) -> str | None:
    """Primera columna de fecha detectada (compat con plugins)."""
    cols = _find_all_date_columns(df)
    return cols[0] if cols else None


def get_date_columns(df: pd.DataFrame) -> list[str]:
    """Todas las columnas de fecha detectadas."""
    return _find_all_date_columns(df)


def detect_granularity(df: pd.DataFrame, date_col: str) -> str:
    """
    Devuelve la granularidad observada: 'D', 'W', 'M', 'Q', 'Y' u 'Original'.

    Si el DF es muy grande, usa una muestra para evitar ordenar
    millones de fechas (que tarda segundos).
    """
    if date_col not in df.columns:
        return "Original"

    # --- Muestra si es grande ---
    # --- Muestra si es grande ---
    n = len(df)
    if n > 50_000:
        s_src = df[date_col].head(10_000)
    else:
        s_src = df[date_col]

    try:
        s = pd.to_datetime(s_src, errors="coerce").dropna().sort_values()
    except Exception:
        return "Original"

    if len(s) < 3:
        return "Original"

    diffs = s.diff().dropna()
    if diffs.empty:
        return "Original"

    med = diffs.median()
    if med <= pd.Timedelta(days=2):
        return "D"
    if med <= pd.Timedelta(days=10):
        return "W"
    if med <= pd.Timedelta(days=45):
        return "M"
    if med <= pd.Timedelta(days=120):
        return "Q"
    return "Y"
# ------------------------------------------------------------------
# FILTRADO Y AGREGACIÓN
# ------------------------------------------------------------------
def filter_by_date(df: pd.DataFrame, date_col: str, start, end) -> pd.DataFrame:
    if date_col not in df.columns:
        return df
    try:
        mask = (
            (df[date_col] >= pd.Timestamp(start)) &
            (df[date_col] <= pd.Timestamp(end))
        )
        return df.loc[mask].copy()
    except Exception:
        return df


def aggregate_period(df: pd.DataFrame, date_col: str,
                     freq: str, group_cols: list[str] | None = None) -> pd.DataFrame:
    """
    Agrega el DataFrame a la frecuencia pedida sobre date_col.

    - Para DFs pequeños usa pandas (más simple).
    - Para DFs grandes usa DuckDB (10-30x más rápido).

    freq: 'Original', 'D', 'W', 'M', 'Q', 'Y'
    """
    if not date_col or date_col not in df.columns or freq == "Original":
        return df

    # --- Decidir motor ---
    n = len(df)
    usar_duck = (_engine._DUCKDB_OK and n >= _engine.UMBRAL_PANDAS)

    if usar_duck:
        try:
            return _aggregate_period_duck(df, date_col, freq)
        except Exception as e:
            print(f"[loader] DuckDB agg falló, usando pandas: {e}")

    return _aggregate_period_pandas(df, date_col, freq)


def _aggregate_period_duck(df: pd.DataFrame, date_col: str,
                             freq: str) -> pd.DataFrame:
    """
    Agrega con DuckDB: agrupa por DATE_TRUNC(fecha) + columnas de dimensión
    y suma todas las numéricas.
    """
    freq_map = {
        "D": "day",
        "W": "week",
        "M": "month",
        "Q": "quarter",
        "Y": "year",
    }
    trunc_unit = freq_map.get(freq, "week")

    # --- Identificar columnas numéricas y de dimensión ---
    num_cols = df.select_dtypes("number").columns.tolist()
    if not num_cols:
        return df

    # Columnas de fecha alternativas (Semana, Mes, etc.) que sobran
    date_like_others = []
    for c in df.columns:
        if c == date_col:
            continue
        if any(k in str(c).lower() for k in _DATE_KEYWORDS):
            date_like_others.append(c)

    # Dimensiones: no numéricas, no fecha principal, no fecha alternativa
    dim_cols = [
        c for c in df.columns
        if c not in num_cols
        and c != date_col
        and c not in date_like_others
    ]

    # DuckDB solo necesita estas columnas. Las Series conservan los datos
    # originales; la fecha convertida se sustituye en esta vista, sin copiar
    # todas las medidas ni las fechas auxiliares.
    needed_cols = list(dict.fromkeys([date_col, *dim_cols, *num_cols]))
    df_clean = pd.DataFrame({c: df[c] for c in needed_cols}, copy=False)

    # Asegurar que la fecha es datetime
    if not pd.api.types.is_datetime64_any_dtype(df_clean[date_col]):
        df_clean[date_col] = pd.to_datetime(
            df_clean[date_col], errors="coerce")

    t = _engine.register(df_clean, "_agg_tmp")

    try:
        # SELECT
        select_parts = [
            f"DATE_TRUNC('{trunc_unit}', \"{date_col}\") "
            f"AS \"{date_col}\""
        ]
        for c in dim_cols:
            select_parts.append(f'"{c}"')
        for c in num_cols:
            if c in df_clean.columns:
                select_parts.append(
                    f'SUM(CAST("{c}" AS DOUBLE)) AS "{c}"')

        select_sql = ", ".join(select_parts)

        # GROUP BY: fecha + dimensiones
        group_parts = [
            f"DATE_TRUNC('{trunc_unit}', \"{date_col}\")"
        ]
        for c in dim_cols:
            group_parts.append(f'"{c}"')

        group_sql = ", ".join(group_parts)

        # WHERE: fecha no nula
        where_sql = f'WHERE "{date_col}" IS NOT NULL'

        sql = f"""
            SELECT {select_sql}
            FROM {t}
            {where_sql}
            GROUP BY {group_sql}
            ORDER BY {group_sql}
        """

        grouped = _engine.query(sql)
    finally:
        _engine.unregister(t)

    # Reordenar columnas: fecha primero
    if date_col in grouped.columns:
        cols = [date_col] + [c for c in grouped.columns
                              if c != date_col]
        grouped = grouped[cols]

    return grouped


def _aggregate_period_pandas(df: pd.DataFrame, date_col: str,
                               freq: str) -> pd.DataFrame:
    """Fallback pandas (para DFs pequeños)."""
    # Pandas 3 eliminó M/Q/Y; versiones 2.x antiguas pueden necesitar esos
    # aliases. Conservar el final de mes/trimestre/año en ambas versiones.
    new_alias = {"M": "ME", "Q": "QE", "Y": "YE"}.get(freq, freq)
    try:
        pd.tseries.frequencies.to_offset(new_alias)
        freq = new_alias
    except ValueError:
        pass

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    if df.empty:
        return df

    num_cols = df.select_dtypes("number").columns.tolist()

    date_like_others = []
    for c in df.columns:
        if c == date_col:
            continue
        if any(k in str(c).lower() for k in _DATE_KEYWORDS):
            try:
                pd.to_datetime(df[c], errors="raise")
                date_like_others.append(c)
            except Exception:
                pass

    dim_cols = [
        c for c in df.columns
        if c not in num_cols
        and c != date_col
        and c not in date_like_others
    ]

    if date_like_others:
        df = df.drop(columns=date_like_others, errors="ignore")
        num_cols = [c for c in num_cols if c in df.columns]
        dim_cols = [c for c in dim_cols if c in df.columns]

    if not num_cols:
        return df

    df = df.set_index(date_col)
    agg_map = {c: "sum" for c in num_cols}

    try:
        if dim_cols:
            grouped = (
                df.groupby([pd.Grouper(freq=freq)] + dim_cols,
                           observed=True)
                  .agg(agg_map)
                  .reset_index()
            )
        else:
            grouped = (
                df.groupby(pd.Grouper(freq=freq), observed=True)
                  .agg(agg_map)
                  .reset_index()
            )
    except Exception:
        grouped = (
            df.groupby(pd.Grouper(freq=freq), observed=True)
              .agg(agg_map)
              .reset_index()
        )

    return grouped

def list_available_files(folder: str | Path = "data") -> list[Path]:
    folder = Path(folder)
    if not folder.exists():
        return []
    return sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )

def load_file_lazy(path, log=None):
    """
    Carga un archivo GRANDE sin materializarlo a pandas.

    Devuelve un dict con:
      - "mode": "duckdb_lazy"
      - "duck_view": nombre de la vista en DuckDB
      - "preview": DataFrame head(200)
      - "shape": (n_rows, n_cols)
      - "columns": lista de columnas
      - "dtypes": dict col → tipo
    """
    path = Path(path)
    ext = path.suffix.lower()

    if ext not in (".csv", ".txt", ".parquet"):
        raise ValueError(
            f"load_file_lazy solo soporta CSV/Parquet. Recibido: {ext}"
        )

    if log:
        log(f"[loader-lazy] Registrando {path.name} en DuckDB...")

    import time
    t0 = time.time()

    view = _engine.register_file_in_duckdb(path)

    if log:
        log(f"[loader-lazy] Vista registrada en {time.time()-t0:.2f}s")

    # Contar filas — DuckDB usa metadata para parquet (instantáneo)
    # Para CSV escanea el archivo una vez (rápido, sin materializar)
    try:
        n_rows = int(_engine.query(
            f"SELECT COUNT(*) AS n FROM {view}"
        )["n"].iloc[0])
    except Exception:
        n_rows = -1

    if log:
        log(f"[loader-lazy] Filas: {n_rows:,}".replace(",", "."))

    # Preview de 200 filas
    preview = _engine.peek_duckdb_view(view, n=200)

    return {
        "mode": "duckdb_lazy",
        "duck_view": view,
        "path": str(path),
        "preview": preview,
        "shape": (n_rows, len(preview.columns)),
        "columns": list(preview.columns),
        "dtypes": dict(preview.dtypes.astype(str)),
    }

