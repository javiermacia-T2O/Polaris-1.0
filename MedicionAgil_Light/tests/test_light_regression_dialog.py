"""Regression controls must use the same dates and inputs the user sees."""

from types import SimpleNamespace

import pandas as pd
import pytest
import tkinter as tk

from ui.dialogs import regression_dialog as module


def test_regression_dialog_shows_model_selector_and_explanatory_toggle():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk no está disponible")
    dialog = None
    try:
        root.withdraw()
        frame = pd.DataFrame({
            "Fecha": pd.date_range("2025-01-01", periods=60),
            "Ventas": range(60), "Precio": range(60),
        })
        dialog = module.RegressionDialog(root, frame, "regression_ui_smoke")
        root.update_idletasks()
        models = dialog.cb_reg_type.cget("values")
        assert any("Automático" in item for item in models)
        assert any("OLS" in item for item in models)
        assert any("Evento / ITS" in item for item in models)
        assert any("Elastic Net" in item for item in models)
        assert any("Bayesian Ridge" in item for item in models)
        assert dialog.cb_reg_type.winfo_manager() == "grid"
        assert "Precio" in dialog.var_cfg
    finally:
        if dialog is not None:
            dialog.destroy()
        root.destroy()


def test_dragged_event_uses_dates_of_plotted_points(monkeypatch):
    saved = []
    monkeypatch.setattr(module, "add_sub_event", lambda events, **kw: saved.append(kw))
    monkeypatch.setattr(module, "save_events", lambda *_: None)
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=100),
        "y": range(100),
    })
    dialog = SimpleNamespace(
        date_var=SimpleNamespace(get=lambda: "date"), df=frame,
        _chart_dates=pd.Series(pd.to_datetime([
            "2025-01-01", "2025-02-01", "2025-03-01"])),
        events={"sub_events": []}, dataset_name="test",
        _refresh_events_list=lambda: None, _refresh_chart=lambda: None,
        _refresh_global_status=lambda: None,
        _toast_local=lambda *_: None,
    )

    module.RegressionDialog._create_event_quick(dialog, 1, 2)

    assert saved[0]["start"] == pd.Timestamp("2025-02-01")
    assert saved[0]["end"] == pd.Timestamp("2025-03-01")


def test_selected_explanatory_variable_and_model_reach_result(monkeypatch):
    class Preflight:
        proceed = True

        def __init__(self, *_):
            pass

    monkeypatch.setattr(module, "PreflightDialog", Preflight)
    monkeypatch.setattr(module, "prepare_regression_data",
                        lambda source, events, date, cancel: (source, []))
    frame = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=40),
                          "y": range(40), "x": range(40)})

    class Tasks:
        def start(self, work, on_result, **_):
            on_result(work(None, None))
            return True

    dialog = SimpleNamespace(
        var_cfg={"x": {"visible": True, "type": "Línea", "color": "#ffffff"}},
        df_full=frame, events={"sub_events": []},
        date_var=SimpleNamespace(get=lambda: "date"),
        target_var=SimpleNamespace(get=lambda: "y"),
        reg_type_var=SimpleNamespace(get=lambda: "Elastic Net"),
        master=SimpleNamespace(tasks=Tasks()),
        _preflight_check_silent=lambda: ([], [], {}),
        wait_window=lambda *_: None, _save_last_config=lambda: None,
        destroy=lambda: None, btn_run=SimpleNamespace(state=lambda *_: None),
    )

    module.RegressionDialog._on_execute(dialog)

    assert dialog.result["input_cols"] == ["x"]
    assert dialog.result["regression_type"] == "Elastic Net"
    assert dialog.result["_history_entry"]["input_cols"] == ["x"]
