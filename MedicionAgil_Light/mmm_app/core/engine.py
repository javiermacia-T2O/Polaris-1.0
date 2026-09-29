"""
Motor de datos columnar estilo Arrow/DuckDB.

Réplica en Python de la metodología que usa R (data.table + Arrow + DuckDB)
para cargar y trabajar con datasets de millones de filas.

Pilares:
  1. Apache Arrow           → lectura paralela multihilo
  2. Parquet                → compresión 10-20x + lectura selectiva
  3. DuckDB                 → motor SQL analítico embebido (out-of-core)
  4. Streaming / Chunking   → leer CSV por bloques
  5. Schema tipado previo   → inferir tipos desde una muestra
  6. Multihilo              → paralelizar agregaciones y joins

Es tolerante a fallos: si DuckDB/Arrow/Polars no están, cae a pandas.
Y funciona tanto en script como empaquetado con PyInstaller.
"""

import os
import sys
import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from core import memory_budget as _memory


# ==================================================================
# DETECCIÓN DE ENTORNO (script vs .exe)
# ==================================================================
def _base_dir() -> Path:
    """
    Devuelve la carpeta base de la app.

    - En script: carpeta del proyecto (mmm_app/).
    - En .exe (PyInstaller): carpeta donde está el .exe,
      para que la caché se guarde junto al ejecutable y no en
      una carpeta temporal que se borra al cerrar.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CACHE_DIR = _base_dir() / "output" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ==================================================================
# MOTORES OPCIONALES (import tolerante)
# ==================================================================
try:
    import duckdb
    _DUCKDB_OK = True
except ImportError:
    _DUCKDB_OK = False

try:
    import pyarrow as pa
    import pyarrow.csv as pacsv
    import pyarrow.parquet as papq
    import pyarrow.dataset as pads
    _ARROW_OK = True
except ImportError:
    _ARROW_OK = False

try:
    import polars as pl
    _POLARS_OK = True
except ImportError:
    _POLARS_OK = False


# ==================================================================
# CONFIGURACIÓN
# ==================================================================
UMBRAL_PANDAS  = 500_000        # a partir de aquí usamos DuckDB
# Al inicio, junto a UMBRAL_PANDAS
UMBRAL_AVISO_GB    = 2.0     # a partir de aquí, avisar
UMBRAL_BLOQUEO_GB  = 20.0    # a partir de aquí, rechazar
UMBRAL_EXCEL_MB    = 500     # Excel por encima de esto: imposible
UMBRAL_PARQUET = 20 * 1024**2   # 20 MB → cachear como Parquet
SAMPLE_PREVIEW = 200            # filas en preview UI
CSV_CHUNK_ROWS = 200_000
N_THREADS      = max(2, (os.cpu_count() or 4) // 2)


# ==================================================================
# CONEXIÓN DUCKDB (singleton)
# ==================================================================
import threading
_CONN_LOCAL = threading.local()
_FILE_VIEWS = {}
_FILE_VIEWS_VERSION = 0

def register_file_in_duckdb(path: Path, name: str = "_src_file") -> str:
    """
    Registra un archivo (CSV/Parquet) directamente en DuckDB como vista,
    sin cargarlo a memoria. DuckDB lo lee lazy on-demand.

    Devuelve el nombre de la vista en DuckDB.
    """
    conn = get_conn()
    path_posix = Path(path).as_posix()
    ext = path.suffix.lower()

    try:
        if ext == ".parquet":
            conn.execute(
                f'CREATE OR REPLACE VIEW {name} AS '
                f"SELECT * FROM read_parquet('{path_posix}')"
            )
        else:  # CSV/TXT
            conn.execute(
                f'CREATE OR REPLACE VIEW {name} AS '
                f"SELECT * FROM read_csv_auto('{path_posix}', "
                f"sample_size=-1, ignore_errors=true)"
            )
        global _FILE_VIEWS_VERSION
        _FILE_VIEWS[name] = (path_posix, ext)
        _FILE_VIEWS_VERSION += 1
        _CONN_LOCAL.views_version = _FILE_VIEWS_VERSION
        return name
    except Exception as e:
        raise RuntimeError(f"No se pudo registrar '{path}' en DuckDB: {e}")


def peek_duckdb_view(name: str, n: int = 200) -> pd.DataFrame:
    """Materializa solo las primeras N filas de una vista DuckDB."""
    return query(f"SELECT * FROM {name} LIMIT {n}")


def get_view_schema(name: str) -> dict:
    """Devuelve columnas + tipos de una vista DuckDB sin leer datos."""
    df = query(f"DESCRIBE {name}")
    return dict(zip(df["column_name"], df["column_type"]))

def _detect_available_ram_gb() -> float:
    """Detecta la RAM total del sistema (GB)."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        c_ulonglong = ctypes.c_ulonglong

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", c_ulonglong),
                ("ullAvailPhys", c_ulonglong),
                ("ullTotalPageFile", c_ulonglong),
                ("ullAvailPageFile", c_ulonglong),
                ("ullTotalVirtual", c_ulonglong),
                ("ullAvailVirtual", c_ulonglong),
                ("ullAvailExtendedVirtual", c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys / (1024 ** 3)
    except Exception:
        pass

    try:
        import os
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return (pages * page_size) / (1024 ** 3)
    except Exception:
        return 16.0  # fallback conservador


def get_conn():
    """Conexión DuckDB independiente para Tk y el worker."""
    if not _DUCKDB_OK:
        raise RuntimeError(
            "DuckDB no está instalado. Ejecuta: pip install duckdb"
        )
    conn = getattr(_CONN_LOCAL, "conn", None)
    if conn is None:
        # Tk y un worker: ambas conexiones juntas respetan el límite DuckDB.
        # El mínimo por conexión es la mitad del suelo conjunto (128 MiB), no
        # el suelo completo: comparar contra 128 MiB rechazaba la lectura
        # desde disco incluso para archivos pequeños cuando la RAM libre era
        # baja. DuckDB admite límites pequeños y desborda a temp_directory.
        memory_limit_mb = _memory.duckdb_limit_bytes() // (2 * 1024**2)
        if memory_limit_mb < 256:
            memory_limit_mb = 256
        conn = duckdb.connect(database=":memory:")
        try:
            print(f"[engine] DuckDB memory_limit={memory_limit_mb} MiB")

            conn.execute(f"PRAGMA threads={min(N_THREADS, 4)}")
            conn.execute(f"PRAGMA memory_limit='{memory_limit_mb}MB'")
            conn.execute(f"PRAGMA temp_directory='{CACHE_DIR.as_posix()}'")
            conn.execute("PRAGMA enable_progress_bar=false")

            # --- Optimizaciones adicionales ---
            # 1. Conservar índices para mejor uso de memoria
            conn.execute("PRAGMA preserve_insertion_order=false")
        except Exception as e:
            conn.close()
            raise RuntimeError(f"No se pudo limitar la memoria de DuckDB: {e}") from e
        _CONN_LOCAL.conn = conn
        _CONN_LOCAL.views_version = -1
    if _CONN_LOCAL.views_version != _FILE_VIEWS_VERSION:
        for name, (path_posix, ext) in _FILE_VIEWS.items():
            escaped = path_posix.replace("'", "''")
            source = (f"read_parquet('{escaped}')" if ext == ".parquet"
                      else f"read_csv_auto('{escaped}', sample_size=-1, "
                           "ignore_errors=true)")
            conn.execute(f'CREATE OR REPLACE VIEW "{name}" AS '
                         f'SELECT * FROM {source}')
        _CONN_LOCAL.views_version = _FILE_VIEWS_VERSION
    return conn


def reset_conn():
    """Cierra la conexión del hilo actual (libera memoria)."""
    conn = getattr(_CONN_LOCAL, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _CONN_LOCAL.conn = None


# ==================================================================
# HELPERS
# ==================================================================
def is_big(df) -> bool:
    return _DUCKDB_OK and len(df) >= UMBRAL_PANDAS


def sample_df(df, n: int = SAMPLE_PREVIEW, seed: int = 42):
    if len(df) <= n:
        return df
    return df.sample(n=n, random_state=seed).reset_index(drop=True)


def fmt_rows(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def memory_usage(df) -> str:
    try:
        mb = df.memory_usage(deep=True).sum() / 1024**2
        if mb > 1024:
            return f"{mb/1024:.2f} GB"
        return f"{mb:.1f} MB"
    except Exception:
        return "—"


# ==================================================================
# LECTURA CSV MULTIHILO CON ARROW
# ==================================================================

def _detect_csv_delimiter(path: Path, sample_lines: int = 20) -> str:
    """
    Detecta el delimitador de un CSV/TSV leyendo solo las primeras
    líneas. Devuelve el delimitador más probable.

    Orden de preferencia (por si empatan):  ,  ;  \\t  |
    """
    candidates = [",", ";", "\t", "|"]

    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
            lines = []
            for _ in range(sample_lines):
                line = f.readline()
                if not line:
                    break
                # Ignorar líneas vacías
                if line.strip():
                    lines.append(line)
    except Exception:
        try:
            with open(path, "r", encoding="latin-1", errors="ignore") as f:
                lines = [f.readline() for _ in range(sample_lines)]
                lines = [l for l in lines if l.strip()]
        except Exception as e:
            print(f"[engine] _detect_csv_delimiter fallo: {e}")
            return ","

    if not lines:
        return ","

    # --- Contar ocurrencias por candidato en cada línea ---
    scores = {c: 0 for c in candidates}
    for line in lines:
        for c in candidates:
            scores[c] += line.count(c)

    # --- Preferir el delimitador que aparezca CONSISTENTEMENTE ---
    # (mismo número de columnas en al menos 80% de las líneas)
    best = ","
    best_score = -1
    for c in candidates:
        # Contar columnas por línea
        counts = [line.count(c) + 1 for line in lines]
        if not counts:
            continue
        # Contar cuántas líneas tienen el máximo de columnas
        from collections import Counter
        cc = Counter(counts)
        # El número de columnas más frecuente
        most_common_n, most_common_freq = cc.most_common(1)[0]
        # Puntuación = columnas × ratio de consistencia
        consistency = most_common_freq / len(counts)
        score = most_common_n * consistency if most_common_n > 1 else 0
        if score > best_score:
            best_score = score
            best = c

    print(f"[engine] Delimitador detectado: {best!r}  "
          f"(scores: {scores})")
    return best

def load_csv_streaming(path: Path, log=None) -> pd.DataFrame:
    """
    Lee CSV con Arrow (multihilo) detectando antes el delimitador.

    Cascada de intentos:
      1. Arrow con el delimitador detectado + auto_dict
      2. Arrow sin auto_dict
      3. Arrow modo básico
      4. Pandas por chunks con el delimitador detectado
    """
    def _log(m):
        if log:
            log(m)

    delimiter = _detect_csv_delimiter(path)

    if _ARROW_OK:
        # --- Intento 1: delimitador conocido + auto_dict ---
        try:
            table = pacsv.read_csv(
                str(path),
                read_options=pacsv.ReadOptions(
                    block_size=1 << 25,
                    use_threads=True,
                ),
                convert_options=pacsv.ConvertOptions(
                    auto_dict_encode=True,
                    auto_dict_max_cardinality=100,
                    strings_can_be_null=True,
                ),
                parse_options=pacsv.ParseOptions(
                    delimiter=delimiter,
                    newlines_in_values=False,
                ),
            )
            _log(f"[engine] Arrow OK (auto_dict · delim={delimiter!r})")
            return table.to_pandas(split_blocks=True, self_destruct=True)
        except Exception as e1:
            _log(f"[engine] Arrow auto_dict falló: {e1}")

        # --- Intento 2: sin auto_dict ---
        try:
            table = pacsv.read_csv(
                str(path),
                read_options=pacsv.ReadOptions(
                    block_size=1 << 25,
                    use_threads=True,
                ),
                convert_options=pacsv.ConvertOptions(
                    auto_dict_encode=False,
                    strings_can_be_null=True,
                ),
                parse_options=pacsv.ParseOptions(
                    delimiter=delimiter,
                    newlines_in_values=False,
                ),
            )
            _log(f"[engine] Arrow OK (sin auto_dict · delim={delimiter!r})")
            return table.to_pandas(split_blocks=True, self_destruct=True)
        except Exception as e2:
            _log(f"[engine] Arrow sin auto_dict falló: {e2}")

        # --- Intento 3: básico ---
        try:
            table = pacsv.read_csv(
                str(path),
                read_options=pacsv.ReadOptions(use_threads=True),
                parse_options=pacsv.ParseOptions(delimiter=delimiter),
            )
            _log(f"[engine] Arrow OK (básico · delim={delimiter!r})")
            return table.to_pandas()
        except Exception as e3:
            _log(f"[engine] Arrow básico falló: {e3}")

    # --- Fallback: pandas por chunks ---
    _log(f"[engine] Usando pandas con chunks "
         f"({CSV_CHUNK_ROWS:,} filas · delim={delimiter!r})"
         .replace(",", "."))
    chunks = []
    for chunk in pd.read_csv(path, sep=delimiter,
                              chunksize=CSV_CHUNK_ROWS,
                              low_memory=False):
        chunks.append(chunk)
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
# ==================================================================
# PARQUET
# ==================================================================
def parquet_path_for(src: Path) -> Path:
    src = Path(src)
    identity = str(src.resolve()).casefold().encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:16]
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "_"
                        for c in src.stem)[:80]
    return CACHE_DIR / f"{safe_stem}-{digest}.parquet"


def _cache_metadata_path(cache: Path) -> Path:
    return cache.with_suffix(".meta.json")


def _source_metadata(src: Path) -> dict:
    stat = src.stat()
    return {"source": str(src.resolve()).casefold(),
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _row_count_path(src: Path) -> Path:
    """Sidecar file that memoises the row count of a source file.

    Counting rows of a multi-gigabyte CSV means scanning the whole file with
    DuckDB (tens of seconds). The count only changes when the source changes,
    so it is persisted next to the Parquet cache and validated against the
    same size/mtime fingerprint used for the cache itself.
    """
    return parquet_path_for(src).with_suffix(".rows.json")


def cached_row_count(src: Path) -> int | None:
    """Return the persisted row count for ``src`` if it is still valid."""
    src = Path(src)
    meta = _row_count_path(src)
    if not meta.exists():
        return None
    try:
        stored = json.loads(meta.read_text(encoding="utf-8"))
        if stored.get("source") != _source_metadata(src):
            return None
        rows = int(stored["rows"])
        return rows if rows >= 0 else None
    except Exception:
        return None


def store_row_count(src: Path, rows: int) -> None:
    """Persist ``rows`` for ``src`` atomically, ignoring write failures."""
    src = Path(src)
    meta = _row_count_path(src)
    try:
        meta.parent.mkdir(parents=True, exist_ok=True)
        payload = {"source": _source_metadata(src), "rows": int(rows)}
        fd, temp_name = tempfile.mkstemp(
            prefix=meta.stem + "-", suffix=".tmp", dir=meta.parent)
        os.close(fd)
        temp = Path(temp_name)
        try:
            temp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(temp, meta)
        finally:
            temp.unlink(missing_ok=True)
    except Exception:
        pass


def _write_source_cache(df: pd.DataFrame, src: Path, cache: Path):
    """Publica Parquet y metadatos completos, sin exponer escrituras parciales."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    before = _source_metadata(src)
    fd, temp_name = tempfile.mkstemp(prefix=cache.stem + "-", suffix=".parquet.tmp",
                                     dir=cache.parent)
    os.close(fd)
    temp = Path(temp_name)
    meta = _cache_metadata_path(cache)
    meta_temp = temp.with_suffix(".meta.tmp")
    try:
        to_parquet(df, temp)
        if _source_metadata(src) != before:
            raise RuntimeError("El archivo de origen cambió durante el cacheo")
        meta_temp.write_text(json.dumps(before), encoding="utf-8")
        os.replace(temp, cache)
        os.replace(meta_temp, meta)
    finally:
        temp.unlink(missing_ok=True)
        meta_temp.unlink(missing_ok=True)


def is_cached(src: Path, verbose: bool = False) -> bool:
    """Comprueba si existe un Parquet cacheado válido para el origen."""
    p = parquet_path_for(src)

    meta = _cache_metadata_path(p)
    if not p.exists() or not meta.exists():
        if verbose:
            print(f"[cache] Parquet NO existe: {p.name}")
        return False

    try:
        current = _source_metadata(Path(src))
        stored = json.loads(meta.read_text(encoding="utf-8"))
        size_mb = p.stat().st_size / (1024 * 1024)

        if verbose:
            print(f"[cache] {p.name}: {size_mb:.1f} MB")

        return p.stat().st_size > 0 and stored == current

    except Exception as e:
        print(f"[cache] Error comprobando: {e}")
        return False


def to_parquet(df: pd.DataFrame, path: Path, compression: str = "snappy"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if _ARROW_OK:
        table = pa.Table.from_pandas(df, preserve_index=False)
        papq.write_table(table, str(path), compression=compression,
                          use_dictionary=True)
    else:
        df.to_parquet(path, engine="pyarrow",
                       compression=compression, index=False)
    return path


def read_parquet(path: Path, columns=None):
    return pd.read_parquet(path, columns=columns, engine="pyarrow")


# ==================================================================
# CARGA PRINCIPAL (todo en uno)
# ==================================================================
def load_dataframe(path, force_reload: bool = False, log=None) -> pd.DataFrame:
    """
    Carga un archivo aplicando toda la metodología:
      1. Parquet → lectura directa
      2. Excel  → pandas (+ caché si grande)
      3. CSV/TXT:
         - Parquet cache disponible → leerlo
         - Grande → Arrow multihilo + cachear a Parquet
         - Pequeño → pandas

    Incluye timings por fase para diagnóstico.
    """
    import time as _t

    path = Path(path)
    ext = path.suffix.lower()
    _memory.ensure_load_fits(path)

    def _log(m):
        if log:
            log(m)
        else:
            print(m)

    # ---------- Parquet ----------
    if ext == ".parquet":
        t0 = _t.time()
        _log(f"[engine] Parquet → lectura directa: {path.name}")
        df = read_parquet(path)
        _log(f"[engine] Parquet leído en {_t.time()-t0:.2f}s "
             f"({len(df):,} × {df.shape[1]})".replace(",", "."))
        return df

    # ---------- Excel ----------
    if ext in (".xlsx", ".xlsm", ".xls"):
        cache = parquet_path_for(path)
        if not force_reload and is_cached(path):
            try:
                t0 = _t.time()
                _log(f"[engine] Usando caché Parquet de Excel: {cache.name}")
                df = read_parquet(cache)
                _log(f"[engine] Cache leído en {_t.time()-t0:.2f}s")
                return df
            except Exception as e:
                _log(f"[engine] Caché inválida; leyendo Excel: {e}")

        t0 = _t.time()
        _log(f"[engine] Excel → pandas (openpyxl)")
        df = pd.read_excel(path, engine="openpyxl")
        _log(f"[engine] Excel leído en {_t.time()-t0:.2f}s "
             f"({len(df):,} × {df.shape[1]})".replace(",", "."))

        if path.stat().st_size > 10 * 1024**2 and _ARROW_OK:
            try:
                t0 = _t.time()
                _write_source_cache(df, path, cache)
                _log(f"[engine] Cacheado: {cache.name} "
                     f"en {_t.time()-t0:.2f}s")
            except Exception:
                pass
        return df

    # ---------- CSV / TSV / TXT ----------
    if ext in (".csv", ".tsv", ".txt"):
        cache = parquet_path_for(path)

        # --- 1 · Caché disponible ---
        if not force_reload:
            if cache.exists():
                size_mb = cache.stat().st_size / (1024 * 1024)
                _log(f"[engine] Caché encontrado: {cache.name} "
                     f"({size_mb:.1f} MB)")
                if is_cached(path, verbose=True):
                    try:
                        t0 = _t.time()
                        _log(f"[engine] Usando caché Parquet: {cache.name}")
                        df = read_parquet(cache)
                        _log(f"[engine] Caché leído en "
                             f"{_t.time()-t0:.2f}s "
                             f"({len(df):,} × {df.shape[1]})"
                             .replace(",", "."))
                        return df
                    except Exception as e:
                        _log(f"[engine] Caché inválida; leyendo CSV: {e}")
                else:
                    _log(f"[engine] Caché descartado por mtime")
            else:
                _log(f"[engine] No existe caché para {path.name}")

        # --- 2 · Detectar delimitador (rápido) ---
        delimiter = _detect_csv_delimiter(path)

        # --- 3 · Lectura CSV ---
        size_mb = path.stat().st_size / (1024 * 1024)
        _log(f"[engine] CSV {path.name} ({size_mb:.1f} MB)  ·  "
             f"delim={delimiter!r}")

        t0 = _t.time()
        if size_mb > 20 and _ARROW_OK:
            _log(f"[engine] Lectura multihilo con Arrow...")
            df = load_csv_streaming(path, log=log)
        else:
            try:
                df = pd.read_csv(path, sep=delimiter,
                                  low_memory=False)
            except Exception as e:
                _log(f"[engine] read_csv falló con {delimiter!r}: {e}")
                # Último intento: dejar que pandas auto-detecte
                df = pd.read_csv(path, sep=None, engine="python",
                                  low_memory=False)

        _log(f"[engine] Lectura CSV en {_t.time()-t0:.2f}s "
             f"({len(df):,} × {df.shape[1]})".replace(",", "."))

        # --- 4 · Cachear como Parquet ---
        if size_mb > 20 and _ARROW_OK:
            try:
                t0 = _t.time()
                cache = parquet_path_for(path)
                _write_source_cache(df, path, cache)
                _log(f"[engine] Cacheado como Parquet: {cache.name} "
                     f"en {_t.time()-t0:.2f}s")
            except Exception as e:
                _log(f"[engine] No se pudo cachear: {e}")

        return df

    return pd.read_csv(path)

# ==================================================================
# DUCKDB · Registro y consultas
# ==================================================================
_TABLE_COUNTER = {"n": 0}


def register(df: pd.DataFrame, name: str | None = None) -> str:
    conn = get_conn()
    if not name:
        _TABLE_COUNTER["n"] += 1
        name = f"_t{_TABLE_COUNTER['n']}"
    conn.register(name, df)
    return name


def unregister(name: str):
    try:
        get_conn().unregister(name)
    except Exception:
        pass


def query(sql: str) -> pd.DataFrame:
    return get_conn().execute(sql).df()


def query_arrow(sql: str):
    """
    Ejecuta SQL y devuelve un Arrow Table.

    Compatible con DuckDB antiguo (.arrow() → Table) y nuevo
    (.arrow() → RecordBatchReader). En el segundo caso hace
    read_all() para obtener la Table.
    """
    result = get_conn().execute(sql)

    # DuckDB antiguo: .arrow() ya devuelve Table
    try:
        reader = result.arrow()
        if hasattr(reader, "read_all"):
            # DuckDB nuevo: RecordBatchReader → Table
            return reader.read_all()
        return reader
    except Exception:
        # Fallback universal
        try:
            return result.fetch_arrow_table()
        except Exception:
            # Último recurso: convertir a pandas y de vuelta a arrow
            import pyarrow as pa
            return pa.Table.from_pandas(result.df())
# ==================================================================
# AGREGACIONES Y JOINS
# ==================================================================
def groupby_agg(df: pd.DataFrame, by: list[str],
                agg_map: dict[str, str]) -> pd.DataFrame:
    if not is_big(df):
        g = df.groupby(by, dropna=False, observed=True)
        return g.agg(agg_map).reset_index()

    t = register(df)
    by_cols = ", ".join(f'"{c}"' for c in by)
    parts = []
    for c, func in agg_map.items():
        f = func.upper()
        if func == "nunique":
            parts.append(f'COUNT(DISTINCT "{c}") AS "{c}"')
        else:
            parts.append(f'{f}("{c}") AS "{c}"')
    aggs = ", ".join(parts)
    sql = f"SELECT {by_cols}, {aggs} FROM {t} GROUP BY {by_cols}"
    out = query(sql)
    unregister(t)
    return out


def groupby_sum(df: pd.DataFrame, by: list[str], value: str):
    return groupby_agg(df, by, {value: "sum"})


def merge_dfs(left: pd.DataFrame, right: pd.DataFrame,
              on: list[str], how: str = "inner") -> pd.DataFrame:
    if not (_DUCKDB_OK and (is_big(left) or is_big(right))):
        return pd.merge(left, right, on=on, how=how)

    t1 = register(left, "_merge_l")
    t2 = register(right, "_merge_r")

    cols_l = list(left.columns)
    cols_r = [c for c in right.columns if c not in on]

    select_parts = [f'l."{c}"' for c in cols_l]
    for c in cols_r:
        if c in cols_l:
            select_parts.append(f'r."{c}" AS "{c}_r"')
        else:
            select_parts.append(f'r."{c}" AS "{c}"')
    select = ", ".join(select_parts)

    cond = " AND ".join([f'l."{c}" = r."{c}"' for c in on])
    how_sql = {"inner": "INNER", "left": "LEFT", "right": "RIGHT",
                "outer": "FULL OUTER"}.get(how, "INNER")

    sql = (f"SELECT {select} FROM {t1} AS l "
           f"{how_sql} JOIN {t2} AS r ON {cond}")
    out = query(sql)
    unregister(t1)
    unregister(t2)
    return out


# ==================================================================
# UTILIDADES
# ==================================================================
def count_rows(df) -> int:
    if not is_big(df):
        return len(df)
    t = register(df)
    n = int(query(f"SELECT COUNT(*) AS n FROM {t}")["n"].iloc[0])
    unregister(t)
    return n


def unique_count(df, col: str) -> int:
    if not is_big(df):
        return int(df[col].nunique(dropna=True))
    t = register(df)
    n = int(query(f'SELECT COUNT(DISTINCT "{col}") AS n FROM {t}')["n"].iloc[0])
    unregister(t)
    return n

# ==================================================================
# ESCRITURA RÁPIDA (DuckDB / Arrow)
# ==================================================================
def write_csv_fast(df: pd.DataFrame, path, compression: str = "none",
                    log=None) -> dict:
    """
    Escribe CSV con DuckDB (monohilo acelerado en C++ + vectorizado).

    compression: 'none' | 'gzip' | 'zstd'
    Devuelve dict con tiempo y tamaño.
    """
    import time as _t
    path = Path(path).as_posix()
    t0 = _t.time()

    def _log(m):
        if log:
            log(m)

    # --- DuckDB ---
    if _DUCKDB_OK:
        try:
            conn = get_conn()
            t = register(df, "_export_tmp")
            comp_sql = {
                "none": "",
                "gzip": ", COMPRESSION GZIP",
                "zstd": ", COMPRESSION ZSTD",
            }.get(compression, "")

            # DuckDB COPY TO es columnar y vectorizado: 5-10x más rápido
            sql = (f"COPY (SELECT * FROM {t}) "
                   f"TO '{path}' (FORMAT CSV, HEADER{comp_sql})")
            conn.execute(sql)
            unregister(t)

            size_mb = Path(path).stat().st_size / (1024**2)
            dt = _t.time() - t0
            _log(f"[export] DuckDB CSV: {dt:.2f}s · {size_mb:.1f} MB")
            return {"engine": "duckdb", "time": dt, "size_mb": size_mb}
        except Exception as e:
            _log(f"[export] DuckDB CSV falló: {e}")

    # --- Arrow ---
    if _ARROW_OK:
        try:
            table = pa.Table.from_pandas(df, preserve_index=False)
            if compression == "gzip":
                # Arrow no soporta gzip directo en CSV, envolvemos
                import gzip
                with gzip.open(path, "wb") as f:
                    pacsv.write_csv(table, f)
            else:
                pacsv.write_csv(table, path)

            size_mb = Path(path).stat().st_size / (1024**2)
            dt = _t.time() - t0
            _log(f"[export] Arrow CSV: {dt:.2f}s · {size_mb:.1f} MB")
            return {"engine": "arrow", "time": dt, "size_mb": size_mb}
        except Exception as e:
            _log(f"[export] Arrow CSV falló: {e}")

    # --- pandas fallback ---
    try:
        if compression == "gzip":
            df.to_csv(path, index=False, encoding="utf-8-sig",
                       compression="gzip")
        else:
            df.to_csv(path, index=False, encoding="utf-8-sig")

        size_mb = Path(path).stat().st_size / (1024**2)
        dt = _t.time() - t0
        _log(f"[export] pandas CSV: {dt:.2f}s · {size_mb:.1f} MB")
        return {"engine": "pandas", "time": dt, "size_mb": size_mb}
    except Exception as e:
        raise RuntimeError(f"Error al exportar CSV: {e}")


def write_parquet_fast(df: pd.DataFrame, path,
                        compression: str = "zstd",
                        log=None) -> dict:
    """
    Escribe Parquet con DuckDB (más rápido que pyarrow en muchos casos).

    compression: 'snappy' | 'zstd' | 'gzip' | 'lz4' | 'none'
    """
    import time as _t
    path = Path(path).as_posix()
    t0 = _t.time()

    def _log(m):
        if log:
            log(m)

    # --- DuckDB ---
    if _DUCKDB_OK:
        try:
            conn = get_conn()
            t = register(df, "_export_tmp_parquet")
            comp = compression.upper()
            sql = (f"COPY (SELECT * FROM {t}) "
                   f"TO '{path}' (FORMAT PARQUET, COMPRESSION {comp})")
            conn.execute(sql)
            unregister(t)

            size_mb = Path(path).stat().st_size / (1024**2)
            dt = _t.time() - t0
            _log(f"[export] DuckDB Parquet: {dt:.2f}s · {size_mb:.1f} MB")
            return {"engine": "duckdb", "time": dt, "size_mb": size_mb}
        except Exception as e:
            _log(f"[export] DuckDB Parquet falló: {e}")

    # --- Arrow ---
    if _ARROW_OK:
        try:
            table = pa.Table.from_pandas(df, preserve_index=False)
            papq.write_table(table, path, compression=compression)
            size_mb = Path(path).stat().st_size / (1024**2)
            dt = _t.time() - t0
            _log(f"[export] Arrow Parquet: {dt:.2f}s · {size_mb:.1f} MB")
            return {"engine": "arrow", "time": dt, "size_mb": size_mb}
        except Exception as e:
            _log(f"[export] Arrow Parquet falló: {e}")

    # --- pandas ---
    df.to_parquet(path, index=False, compression=compression,
                   engine="pyarrow")
    size_mb = Path(path).stat().st_size / (1024**2)
    dt = _t.time() - t0
    _log(f"[export] pandas Parquet: {dt:.2f}s · {size_mb:.1f} MB")
    return {"engine": "pandas", "time": dt, "size_mb": size_mb}

def clear_cache(protected_paths=()):
    """Elimina los Parquet de origen y de tablas, salvo vistas activas."""
    n = 0
    protected = {Path(path).resolve() for path in protected_paths}
    candidates = list(CACHE_DIR.glob("*.parquet"))
    candidates.extend((CACHE_DIR / "table_results").glob("table-*.parquet"))
    for f in candidates:
        if f.resolve() in protected:
            continue
        try:
            f.unlink()
            _cache_metadata_path(f).unlink(missing_ok=True)
            f.with_suffix(".json").unlink(missing_ok=True)
            n += 1
        except Exception:
            pass
    return n


def cache_size_mb() -> float:
    total = 0
    candidates = list(CACHE_DIR.glob("*.parquet"))
    candidates.extend((CACHE_DIR / "table_results").glob("table-*.parquet"))
    for f in candidates:
        try:
            total += f.stat().st_size
        except Exception:
            pass
    return total / 1024**2

