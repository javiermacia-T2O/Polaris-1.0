from threading import Event
from types import SimpleNamespace
import tkinter as tk

import pytest

import pandas as pd

import app_desktop
from app_desktop import (MMMApp, _attach_analysis_context,
                         _collect_result_dataframes,
                         _regression_history_metrics)


def test_result_tables_collect_nested_analysis_frames():
    app = SimpleNamespace(result={
        "resumen": pd.DataFrame({"valor": [1]}),
        "targets": [{"serie": pd.DataFrame({"valor": [2]})}],
        "mensaje": "terminado",
    })

    tables = MMMApp._result_tables(app)

    assert len(tables) == 2
    assert {frame.iloc[0, 0] for frame in tables.values()} == {1, 2}


def test_visible_result_table_collector_recurses_into_lists():
    tables = _collect_result_dataframes({
        "targets": [{"detalle": pd.DataFrame({"valor": [7]})}],
    })

    assert len(tables) == 1
    assert next(iter(tables.values())).iloc[0, 0] == 7


def test_analysis_context_is_renderable_as_an_exportable_table():
    result = _attach_analysis_context({
        "Estado": "ERROR · sin diseños válidos",
        "quality_status": "No válida",
        "Diagnóstico de calidad": "Ningún candidato superó min_r2.",
    })

    table = result["Contexto del análisis"].set_index("Campo")["Valor"]
    assert table["Estado"] == "ERROR · sin diseños válidos"
    assert table["quality_status"] == "No válida"
    assert "min_r2" in table["Diagnóstico de calidad"]


def test_regression_history_reads_current_in_sample_metrics_key():
    metrics = pd.DataFrame({"Métrica": ["R²"], "Valor": [0.75]})

    assert _regression_history_metrics({
        "Métricas in-sample (diagnóstico)": metrics}) is metrics


def test_analysis_outputs_save_tables_and_figures_in_one_bundle(monkeypatch, tmp_path):
    frame = pd.DataFrame({"valor": [3]})
    figure = SimpleNamespace(_mmm_name="serie")
    observed = {}
    monkeypatch.setattr(app_desktop, "style_matplotlib", lambda _figure: None)

    def fake_save(root, client, name, **kwargs):
        observed.update(root=root, client=client, name=name, **kwargs)
        kwargs["progress"]((0.5, "Guardando serie"))
        return SimpleNamespace(path=tmp_path / "resultado")

    monkeypatch.setattr(app_desktop, "save_result_bundle", fake_save)
    app = SimpleNamespace(
        _result_tables=lambda: {"resumen": frame},
        _choose_result_destination=lambda: (tmp_path, "Cliente 1"),
        _last_analysis_name="Causal Impact",
        _figure_names={},
        chosen_analysis=None,
        _toast=lambda *args: None,
        _log=lambda *args: None,
    )

    def start_task(work, done, label):
        observed["label"] = label
        done(work(Event(), observed.setdefault("updates", []).append))

    app._start_task = start_task
    MMMApp._save_analysis_outputs(app, include_tables=True, figures=(figure,))

    assert observed["name"] == "Causal Impact"
    assert observed["client"] == "Cliente 1"
    assert observed["tables"] == {"resumen": frame}
    assert len(observed["figures"]) == 1
    assert observed["updates"] == [(50, "Guardando serie")]
    assert app._last_bundle_dir == tmp_path / "resultado"


def test_progress_message_switches_to_determinate_percentage():
    class Widget:
        def __init__(self):
            self.values = {}
            self.stopped = False

        def stop(self):
            self.stopped = True

        def configure(self, **kwargs):
            self.values.update(kwargs)

        config = configure

    app = SimpleNamespace(
        progress=Widget(), progress_percent=Widget(),
        progress_label=Widget(), update_idletasks=lambda: None,
    )

    MMMApp._progress_message(app, (37, "KPI: Revenue · Target: Alemania"))

    assert app.progress.stopped
    assert app.progress.values["mode"] == "determinate"
    assert app.progress.values["value"] == 37
    assert app.progress_percent.values["text"] == "37%"
    assert "Alemania" in app.progress_label.values["text"]


def test_analysis_progress_uses_visible_separate_window():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk no disponible")
    root.tasks = SimpleNamespace(cancel=lambda: None)
    try:
        MMMApp._progress_start(root, "Análisis: Causal Impact")
        dialog = root._analysis_progress_dialog
        root.update()
        assert dialog.winfo_exists()
        assert dialog.winfo_viewable()
        MMMApp._progress_message(root, (42, "KPI: Revenue · Alemania"))
        assert root.progress_percent.cget("text") == "42%"
        assert "Alemania" in root.progress_label.cget("text")
        MMMApp._progress_stop(root)
        assert not dialog.winfo_exists()
    finally:
        root.destroy()
