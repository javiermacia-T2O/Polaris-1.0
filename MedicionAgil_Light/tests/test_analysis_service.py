from pathlib import Path
import threading
from types import SimpleNamespace

import pandas as pd
import pytest

from core.tasks import TaskCancelled
from services import analysis_service


class _DummyModule:
    def __init__(self, *, cancel=None):
        self.calls = []
        self.cancel = cancel

    def run(self, df, **kwargs):
        self.calls.append((df, kwargs))
        print("primer mensaje")
        print("segundo mensaje")
        if self.cancel is not None:
            self.cancel.cancelled = True
        return {"filas": len(df), "opciones": kwargs}


def test_run_analysis_executes_prepares_reports_and_preserves_history_kwargs(
        monkeypatch):
    source = pd.DataFrame({"amount": ["1", "2"]})
    module = _DummyModule()
    cancel = SimpleNamespace(cancelled=False, is_set=lambda: cancel.cancelled)
    progress = []
    prepared = source.assign(amount=[1, 2])
    budget_calls = []

    monkeypatch.setattr(analysis_service.db_engine, "UMBRAL_PANDAS", 1)
    monkeypatch.setattr(
        analysis_service.memory_budget,
        "ensure_dataframe_operation_fits",
        lambda *args: budget_calls.append(args),
    )
    monkeypatch.setattr(
        analysis_service, "prepare_df_for_analysis",
        lambda df, types: prepared,
    )
    history = {"target_col": "amount"}
    kwargs = {"answer": 42, "_history_entry": history}

    result, log = analysis_service.run_analysis(
        module, source, kwargs, {"amount": "numero"}, cancel, progress.append)

    assert result == {"filas": 2, "opciones": kwargs}
    assert log == "primer mensaje\nsegundo mensaje\n"
    assert [item for item in progress if isinstance(item, str)] == [
        "Preparando tipos para el análisis...", "primer mensaje", "segundo mensaje",
    ]
    assert [item[0] for item in progress if isinstance(item, tuple)] == [5, 25, 100]
    assert budget_calls == [(source, 4, "Análisis")]
    assert module.calls == [(prepared, kwargs)]
    assert kwargs == {"answer": 42, "_history_entry": history}


def test_run_analysis_honours_cancellation_before_module_execution(monkeypatch):
    module = _DummyModule()
    cancel = SimpleNamespace(is_set=lambda: True)
    monkeypatch.setattr(
        analysis_service.memory_budget, "ensure_dataframe_operation_fits", lambda *_: None)

    with pytest.raises(TaskCancelled):
        analysis_service.run_analysis(
            module, pd.DataFrame({"x": [1]}), {}, {}, cancel, lambda _: None)

    assert module.calls == []


def test_run_analysis_honours_cancellation_after_module_execution(monkeypatch):
    cancel = SimpleNamespace(cancelled=False, is_set=lambda: cancel.cancelled)
    module = _DummyModule(cancel=cancel)
    monkeypatch.setattr(
        analysis_service.memory_budget, "ensure_dataframe_operation_fits", lambda *_: None)

    with pytest.raises(TaskCancelled):
        analysis_service.run_analysis(
            module, pd.DataFrame({"x": [1]}), {}, {}, cancel, lambda _: None)

    assert len(module.calls) == 1


def test_run_analysis_stops_on_memory_budget_before_module_execution(monkeypatch):
    module = _DummyModule()
    cancel = SimpleNamespace(is_set=lambda: False)
    monkeypatch.setattr(
        analysis_service.memory_budget,
        "ensure_dataframe_operation_fits",
        lambda *_: (_ for _ in ()).throw(MemoryError("sin presupuesto")),
    )

    with pytest.raises(MemoryError, match="sin presupuesto"):
        analysis_service.run_analysis(
            module, pd.DataFrame({"x": [1]}), {}, {}, cancel, lambda _: None)

    assert module.calls == []


def test_run_analysis_keeps_other_thread_output_out_of_analysis_log(
        monkeypatch, capsys):
    class _ThreadedOutputModule:
        @staticmethod
        def run(_df, **_kwargs):
            other = threading.Thread(target=lambda: print("salida externa"))
            other.start()
            other.join()
            print("salida worker")
            return {}

    monkeypatch.setattr(
        analysis_service.memory_budget, "ensure_dataframe_operation_fits", lambda *_: None)
    progress = []
    cancel = SimpleNamespace(is_set=lambda: False)

    _, log = analysis_service.run_analysis(
        _ThreadedOutputModule(), pd.DataFrame({"x": [1]}), {}, {},
        cancel, progress.append)

    assert log == "salida worker\n"
    assert [item for item in progress if isinstance(item, str)] == ["salida worker"]
    assert [item[0] for item in progress if isinstance(item, tuple)] == [5, 25, 100]
    assert "salida externa" in capsys.readouterr().out


def test_run_analysis_allows_flush_when_windowed_stdout_is_unavailable(monkeypatch):
    """PyInstaller --windowed can expose sys.stdout as None."""
    class _FlushModule:
        @staticmethod
        def run(_df, **_kwargs):
            print("registro con flush", flush=True)
            return {"ok": True}

    monkeypatch.setattr(
        analysis_service.memory_budget, "ensure_dataframe_operation_fits", lambda *_: None)
    cancel = SimpleNamespace(is_set=lambda: False)
    progress = []
    with monkeypatch.context() as context:
        context.setattr(analysis_service.sys, "stdout", None)
        result, log = analysis_service.run_analysis(
            _FlushModule(), pd.DataFrame({"x": [1]}), {}, {}, cancel,
            progress.append)

    assert result == {"ok": True}
    assert log == "registro con flush\n"
    assert "registro con flush" in progress


def test_prepare_regression_data_checks_budget_cancellation_and_integrity(
        monkeypatch):
    source = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=2),
                           "amount": [3, 4]})
    events = {"sub_events": []}
    cancel = SimpleNamespace(is_set=lambda: False)
    budget_calls = []
    anomaly = pd.Series([0, 1], index=source.index)
    monkeypatch.setattr(
        analysis_service.memory_budget, "ensure_dataframe_operation_fits",
        lambda *args: budget_calls.append(args))
    monkeypatch.setattr(
        analysis_service, "generate_anomaly_columns",
        lambda *_: {"anomaly": anomaly})

    augmented, anomaly_columns = analysis_service.prepare_regression_data(
        source, events, "date", cancel)

    assert budget_calls == [(source, 2, "Preparación de regresión")]
    assert anomaly_columns == ["anomaly"]
    assert augmented["anomaly"].tolist() == [0, 1]
    assert source.columns.tolist() == ["date", "amount"]
    with pytest.raises(TaskCancelled):
        analysis_service.prepare_regression_data(
            source, events, "date", SimpleNamespace(is_set=lambda: True))


def test_prepare_causal_data_keeps_lossless_kpis_destination_and_anomalies(
        monkeypatch):
    source = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=2),
        "Hotel": ["H1", "H2"], "kpi": ["1.5", "2.0"],
    })
    events = {"sub_events": [{"id": "event"}]}
    cancel = SimpleNamespace(is_set=lambda: False)
    monkeypatch.setattr(analysis_service.memory_budget, "pandas_limit_bytes",
                        lambda: 10**9)
    monkeypatch.setattr(
        analysis_service.destination_mapping, "add_destination_column",
        lambda df, **_: df.__setitem__("destination_area_mapped", ["A", "B"]))
    monkeypatch.setattr(
        analysis_service, "generate_anomaly_columns",
        lambda *_: {"anomaly": pd.Series([1, 0])})

    augmented = analysis_service.prepare_causal_data(
        source, events, "date", ["kpi"], cancel)

    assert pd.api.types.is_numeric_dtype(augmented["kpi"])
    assert augmented["kpi"].tolist() == [1.5, 2.0]
    assert augmented["destination_area_mapped"].tolist() == ["A", "B"]
    assert augmented["anomaly"].tolist() == [1, 0]
    assert source.columns.tolist() == ["date", "Hotel", "kpi"]


def test_prepare_causal_data_rejects_budget_and_cancellation(monkeypatch):
    source = pd.DataFrame({"date": ["2024-01-01"], "kpi": [1]})
    events = {"sub_events": []}
    monkeypatch.setattr(analysis_service.memory_budget, "pandas_limit_bytes",
                        lambda: 0)
    with pytest.raises(MemoryError, match="presupuesto de RAM"):
        analysis_service.prepare_causal_data(
            source, events, "date", ["kpi"], SimpleNamespace(is_set=lambda: False))

    monkeypatch.setattr(analysis_service.memory_budget, "pandas_limit_bytes",
                        lambda: 10**9)
    with pytest.raises(TaskCancelled):
        analysis_service.prepare_causal_data(
            source, events, "date", ["kpi"], SimpleNamespace(is_set=lambda: True))


def test_analysis_service_has_no_ui_or_app_dependencies():
    source = Path(analysis_service.__file__).read_text(encoding="utf-8")

    assert "tkinter" not in source
    assert "app_desktop" not in source
    assert "MMMApp" not in source
