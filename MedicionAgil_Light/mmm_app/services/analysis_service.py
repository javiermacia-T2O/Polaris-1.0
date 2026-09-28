"""Ejecución de análisis sin dependencias de la interfaz.

El servicio conserva las protecciones de memoria y cancelación del worker de
la aplicación. El progreso y la salida estándar se comunican mediante los
callbacks que recibe, sin acceder a widgets ni al estado de la aplicación.
"""

import contextlib
import inspect
import io
import sys
import threading

import pandas as pd

from core.events import generate_anomaly_columns
from core import engine as db_engine
from core import memory_budget
from core import destination_mapping
from core.loader import _lossless_numeric
from core.tasks import TaskCancelled
from services.data_service import prepare_df_for_analysis


def prepare_regression_data(source, events, date_col, cancel):
    """Prepara las anomalías para una regresión fuera del hilo de la UI."""
    memory_budget.ensure_dataframe_operation_fits(
        source, 2, "Preparación de regresión")
    if cancel.is_set():
        raise TaskCancelled()
    columns = generate_anomaly_columns(events, source, date_col)
    if cancel.is_set():
        raise TaskCancelled()
    augmented = source.copy()
    for name, series in columns.items():
        augmented[name] = series.values[:len(augmented)]
    return augmented, list(columns)


def prepare_causal_data(source, events, date_col, kpi_cols, cancel):
    """Prepara los datos de Causal Impact fuera del hilo de la UI."""
    used = int(source.memory_usage(deep=True).sum())
    n_events = len(events.get("sub_events", []))
    estimated_extra = used * 2 + len(source) * 16 * n_events
    if estimated_extra > memory_budget.pandas_limit_bytes():
        raise MemoryError("La preparación Causal Impact excedería "
                          "el presupuesto de RAM")
    if cancel.is_set():
        raise TaskCancelled()
    augmented = source.copy()
    if "destination_area_mapped" not in augmented.columns:
        for hotel_col in ("Hotel_short_name", "hotel_short_name",
                          "Hotel", "hotel", "Hotel_code"):
            if hotel_col in augmented.columns:
                destination_mapping.add_destination_column(
                    augmented, hotel_col=hotel_col,
                    dest_col="destination_area_mapped",
                    fallback="otros", inplace=True)
                break
    for column in kpi_cols:
        if cancel.is_set():
            raise TaskCancelled()
        if not pd.api.types.is_numeric_dtype(augmented[column]):
            converted = _lossless_numeric(augmented[column])
            if converted is None:
                raise ValueError(f"'{column}' contiene valores no numéricos")
            augmented[column] = converted
    anomaly_columns = generate_anomaly_columns(events, augmented, date_col)
    for name, series in anomaly_columns.items():
        augmented[name] = series.values[:len(augmented)]
    return augmented


def run_analysis(module, df, kwargs, type_snapshot, cancel, progress):
    """Ejecuta un módulo de análisis y devuelve ``(resultado, registro)``.

    ``kwargs`` se usa solo mediante expansión al invocar el módulo; el
    diccionario original queda disponible para el historial que gestiona la
    interfaz al completar la tarea.
    """
    supports_progress = "progress_callback" in inspect.signature(
        module.run).parameters
    if not supports_progress:
        progress((5, "Comprobando datos y memoria..."))
    memory_budget.ensure_dataframe_operation_fits(df, 4, "Análisis")
    prepared = df
    if len(df) >= db_engine.UMBRAL_PANDAS:
        progress("Preparando tipos para el análisis...")
        prepared = prepare_df_for_analysis(df, type_snapshot)
    if cancel.is_set():
        raise TaskCancelled()

    class _TeeBuffer(io.StringIO):
        def __init__(self):
            super().__init__()
            self._last = ""

        def write(self, value):
            super().write(value)
            lines = (self._last + value).split("\n")
            self._last = lines.pop()
            for line in lines:
                if line.strip():
                    progress(line.strip()[:100])

    buffer = _TeeBuffer()
    original_stdout = sys.stdout
    worker_thread = threading.get_ident()

    class _ThreadBoundWriter:
        def write(self, value):
            target = (buffer if threading.get_ident() == worker_thread
                      else original_stdout)
            return target.write(value)

        def flush(self):
            buffer.flush()
            # En la compilación Windows ``--windowed`` Python no siempre
            # proporciona stdout (puede ser None). Los plugins pueden usar
            # print(..., flush=True), por lo que nunca debemos convertir el
            # registro informativo de un worker en un fallo del análisis.
            flush = getattr(original_stdout, "flush", None)
            if callable(flush):
                flush()

        def __getattr__(self, name):
            return getattr(original_stdout, name)

    run_kwargs = dict(kwargs)
    if supports_progress:
        run_kwargs["progress_callback"] = (
            lambda percent, message: progress((percent, message)))
    else:
        progress((25, "Calculando análisis..."))
    with contextlib.redirect_stdout(_ThreadBoundWriter()):
        result = module.run(prepared, **run_kwargs)
    if cancel.is_set():
        raise TaskCancelled()
    progress((100, "Análisis completado"))
    return result, buffer.getvalue()
