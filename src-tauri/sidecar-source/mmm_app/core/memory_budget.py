"""Presupuesto conservador para portátiles Windows con otras apps abiertas."""

import ctypes
from dataclasses import dataclass
from pathlib import Path


GIB = 1024 ** 3


@dataclass(frozen=True)
class MemoryStatus:
    total: int
    available: int


LAZY_FILE_EXTENSIONS = frozenset({".csv", ".tsv", ".txt", ".parquet"})

# Mantener un límite demasiado agresivo hacía que archivos medianos de CSV/
# Parquet fueran enviados a la ruta lazy incluso cuando la máquina tenía RAM
# libre suficiente. En 2025 los equipos de trabajo suelen tener 8-32 GiB y la
# app debe aprovechar el caché en memoria para mantener la experiencia rápida.
# Si la máquina está realmente apretada, el plan sigue cayendo a disco.
LAZY_FORCE_BYTES = 1024 * 1024 ** 2


@dataclass(frozen=True)
class LoadPlan:
    """Decisión de carga que puede mostrarse sin exponer detalles internos.

    ``pandas`` mantiene el comportamiento rápido habitual. ``disk`` nunca
    construye el DataFrame completo: usa el origen mediante DuckDB y solo
    materializa previews acotados. ``unavailable`` se reserva para formatos
    que no tienen una ruta de lectura incremental fiable.
    """
    mode: str
    estimated_peak: int
    retained_bytes: int
    pandas_budget: int
    message: str

    @property
    def uses_disk(self) -> bool:
        return self.mode == "disk"


def system_memory() -> MemoryStatus:
    """RAM física total y disponible; el fallback limita, nunca amplía."""
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return MemoryStatus(status.ullTotalPhys, status.ullAvailPhys)
    except (AttributeError, OSError):
        pass
    return MemoryStatus(16 * GIB, 4 * GIB)


def app_limit_bytes(status: MemoryStatus | None = None) -> int:
    """Presupuesto dinámico que prioriza datasets residentes sin ahogar Windows.

    La RAM ya ocupada por otros procesos se descuenta en cada decisión. Se
    conserva un margen de 512 MiB o el 10% de la RAM libre, lo que sea mayor;
    el presupuesto máximo es el 75% de la RAM física y 16 GiB.
    """
    status = status or system_memory()
    reserve = max(512 * 1024**2, int(status.available * 0.1))
    return max(0, min(status.available - reserve,
                      int(status.total * 0.75), 16 * GIB))


def duckdb_limit_bytes(status: MemoryStatus | None = None) -> int:
    """Reserva una parte del presupuesto para consultas DuckDB."""
    app_limit = app_limit_bytes(status)
    return min(app_limit, max(256 * 1024**2, int(app_limit * 0.35)))


def pandas_limit_bytes(status: MemoryStatus | None = None) -> int:
    status = status or system_memory()
    return max(0, app_limit_bytes(status) - duckdb_limit_bytes(status))


def table_query_duckdb_limit_bytes(status: MemoryStatus | None = None) -> int:
    """Reserve room for the main DuckDB connection and Python buffers."""
    status = status or system_memory()
    joint = app_limit_bytes(status)
    main = duckdb_limit_bytes(status) // 2
    return max(0, min(3 * GIB, int(joint * 0.45),
                      joint - main - 512 * 1024**2))


def estimate_load_peak_bytes(path: Path) -> int:
    """Estimación conservadora: datos materializados más copias transitorias."""
    path = Path(path)
    size = path.stat().st_size
    ext = path.suffix.lower()
    if ext == ".parquet":
        import pyarrow.parquet as pq
        metadata = pq.read_metadata(path)
        uncompressed = sum(metadata.row_group(i).total_byte_size
                           for i in range(metadata.num_row_groups))
        # El Parquet con diccionarios puede ocupar muy poco en disco, aunque
        # Pandas precise al menos un puntero/valor por celda.
        frame = max(uncompressed * 2,
                    metadata.num_rows * metadata.num_columns * 8)
        return int(frame * 2.5)
    if ext in (".csv", ".tsv", ".txt"):
        # CSV suele expandirse varias veces por objetos, índices y parseo.
        return size * 8
    if ext in (".xlsx", ".xlsm", ".xls"):
        # Excel es un contenedor comprimido y OpenPyXL crea objetos Python.
        return size * 20
    return size * 8


def plan_load(path: Path, retained_bytes: int = 0,
              status: MemoryStatus | None = None) -> LoadPlan:
    """Elige una carga completa o en disco sin rebajar el presupuesto.

    La decisión se toma antes de leer el archivo, por lo que evita empezar
    una materialización que dejaría sin memoria al proceso. Para CSV, TSV,
    TXT y Parquet el modo en disco permite seguir trabajando incluso con la
    RAM saturada. Los análisis que requieran Pandas siguen pasando por su
    preflight específico y no heredan este permiso.
    """
    path = Path(path)
    estimated = estimate_load_peak_bytes(path)
    budget = pandas_limit_bytes(status)
    ext = path.suffix.lower()
    if ext in LAZY_FILE_EXTENSIONS and path.stat().st_size >= LAZY_FORCE_BYTES:
        return LoadPlan(
            "disk", estimated, retained_bytes, budget,
            f"'{path.name}' supera {LAZY_FORCE_BYTES // (1024**2)} MiB; se "
            "trabajará desde disco con DuckDB para no materializarlo en "
            "memoria. El preview y las consultas siguen disponibles.")
    # ``status.available`` is current physical headroom and already reflects
    # resident datasets. Adding ``retained_bytes`` here double-counts those
    # allocations and rejects replacements that still fit in available RAM.
    if estimated <= budget:
        return LoadPlan(
            "pandas", estimated, retained_bytes, budget,
            "Carga directa en memoria disponible.")

    detail = (f"La RAM disponible no permite abrir '{path.name}' completo "
              f"({estimated / GIB:.1f} GiB estimados; presupuesto "
              f"{budget / GIB:.1f} GiB).")
    if ext in LAZY_FILE_EXTENSIONS:
        return LoadPlan(
            "disk", estimated, retained_bytes, budget,
            f"{detail} Se trabajará desde disco con DuckDB; el preview y "
            "las consultas pueden tardar más, pero la aplicación seguirá "
            "disponible.")
    return LoadPlan(
        "unavailable", estimated, retained_bytes, budget,
        f"{detail} Este formato no tiene una lectura incremental fiable. "
        "Libera memoria o conviértelo a CSV o Parquet para abrirlo desde disco.")


def ensure_load_fits(path: Path, retained_bytes: int = 0) -> int:
    plan = plan_load(path, retained_bytes)
    if plan.mode != "pandas":
        raise MemoryError(
            f"Carga completa rechazada por memoria: {plan.message}")
    return plan.estimated_peak


def safe_merge_row_limit(frames, status: MemoryStatus | None = None) -> int:
    """Límite de salida considerando entradas residentes y dos copias de join."""
    input_bytes = 0
    row_bytes = 0.0
    for frame in frames:
        used = int(frame.memory_usage(deep=True).sum())
        input_bytes += used
        row_bytes += used / max(len(frame), 1)
    free_budget = max(0, pandas_limit_bytes(status) - input_bytes)
    memory_rows = int(free_budget / max(row_bytes * 2, 1))
    return min(5_000_000, memory_rows)


def ensure_dataframe_operation_fits(frame, extra_copies: int, operation: str):
    """Evita operaciones que previsiblemente duplicarían demasiado un DF."""
    used = int(frame.memory_usage(deep=True).sum())
    estimated_extra = used * extra_copies
    free_budget = max(0, pandas_limit_bytes() - used)
    if estimated_extra > free_budget:
        raise MemoryError(
            f"{operation} rechazada por memoria: necesita aproximadamente "
            f"{estimated_extra/GIB:.1f} GiB adicionales y el presupuesto "
            f"libre tras el DataFrame residente es {free_budget/GIB:.1f} GiB.")


def ensure_excel_export_fits(frames):
    """Preflight de openpyxl: cada celda Python puede costar ~320 bytes.

    Incluye objetos de celda, valor, referencias y estructuras del libro.
    La cifra es deliberadamente conservadora para los portátiles de 16 GiB.
    """
    cells = 0
    for frame in frames:
        if len(frame) > 1_048_576 or len(frame.columns) > 16_384:
            raise ValueError("Excel supera el límite de filas o columnas")
        cells += len(frame) * len(frame.columns)
    estimated_extra = cells * 320
    if estimated_extra > pandas_limit_bytes():
        raise MemoryError(
            f"Excel rechazado por memoria: {cells:,} celdas necesitan "
            f"~{estimated_extra/GIB:.1f} GiB adicionales. "
            "Usa CSV o Parquet.")
