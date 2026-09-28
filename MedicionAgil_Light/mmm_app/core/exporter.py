"""Exportación de datos independiente de Tkinter."""

import gzip
import pickle
import time
from pathlib import Path

from core.atomic import atomic_output
from core.tasks import TaskCancelled
from core import engine
from core import memory_budget


def export_dataframe(df, path, fmt, *, csv_compression="none",
                     parquet_compression="zstd", cancel=None, progress=None,
                     chunksize=200_000):
    path = Path(path)
    started = time.perf_counter()

    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()

    def report(fraction, detail):
        if progress is not None:
            progress((fraction, detail))

    check_cancel()
    with atomic_output(path) as temporary:
        if fmt in ("csv", "tsv"):
            separator = "," if fmt == "csv" else "\t"
            if len(df) == 0:
                temporary.write_text("")
            else:
                opener = (lambda p: gzip.open(p, "wt", encoding="utf-8-sig",
                                              newline="")) if (fmt == "csv" and
                                                                csv_compression == "gzip") else (
                    lambda p: open(p, "w", encoding="utf-8-sig", newline=""))
                with opener(temporary) as stream:
                    df.head(0).to_csv(stream, index=False, sep=separator)
                    for start in range(0, len(df), chunksize):
                        check_cancel()
                        block = df.iloc[start:start + chunksize]
                        block.to_csv(stream, index=False, header=False,
                                     sep=separator)
                        report(min((start + chunksize) / len(df), 1),
                               f"Escrito {min(start + chunksize, len(df)):,} "
                               f"de {len(df):,} filas")
        elif fmt == "parquet":
            report(0.1, "Escribiendo Parquet...")
            engine.write_parquet_fast(df, temporary,
                                      compression=parquet_compression)
        elif fmt == "xlsx":
            memory_budget.ensure_excel_export_fits([df])
            report(0.1, "Escribiendo Excel...")
            df.to_excel(temporary, index=False, engine="openpyxl")
        else:
            raise ValueError(f"Formato no soportado: {fmt}")
        check_cancel()
    report(1.0, "Exportación completada")
    return {"path": str(path), "format": fmt,
            "meta": {"size_mb": path.stat().st_size / (1024**2),
                     "time": time.perf_counter() - started}}


def save_figure_isolated(figure, path, *, cancel=None):
    """Renderiza una copia Agg sin utilizar el canvas Tk de la figura visible."""
    if cancel is not None and cancel.is_set():
        raise TaskCancelled()
    snapshot = pickle.dumps(figure, protocol=pickle.HIGHEST_PROTOCOL)
    if len(snapshot) * 2 > memory_budget.pandas_limit_bytes():
        raise MemoryError("El gráfico necesita demasiada memoria para exportarlo")
    clone = pickle.loads(snapshot)
    del snapshot
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    FigureCanvasAgg(clone)
    if cancel is not None and cancel.is_set():
        raise TaskCancelled()
    with atomic_output(path) as temporary:
        clone.savefig(temporary, dpi=150, bbox_inches="tight",
                      facecolor=clone.get_facecolor())
        if cancel is not None and cancel.is_set():
            raise TaskCancelled()
