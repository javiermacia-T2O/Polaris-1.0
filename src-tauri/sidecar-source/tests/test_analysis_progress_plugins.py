import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from analyses import regression


ANALYSES = Path(__file__).resolve().parents[1] / "mmm_app" / "analyses"


def _load_file_module(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, ANALYSES / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_monotonic_complete(updates):
    percentages = [percent for percent, _ in updates]
    assert percentages == sorted(percentages)
    assert percentages[-1] == 100


def test_regression_progress_reports_real_stages():
    x = np.arange(30.0)
    frame = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(x)),
        "x": x,
        "y": 3 * x + np.sin(x),
    })
    updates = []

    result = regression.run(
        frame, regression_type=regression.REGRESSION_TYPES[0],
        date_col="date", target_col="y", input_cols=["x"],
        progress_callback=lambda percent, message: updates.append(
            (percent, message)),
    )

    assert result["Estado"] == "OK"
    _assert_monotonic_complete(updates)
    assert any("modelo" in message.lower() for _, message in updates)
    assert any("gráficos" in message.lower() for _, message in updates)


def test_regression_events_and_interactions_enter_the_model(monkeypatch):
    for name in ("_plot_fit", "_plot_forest", "_plot_residuals",
                 "_plot_decomposition"):
        monkeypatch.setattr(regression, name, lambda *args, **kwargs: Figure())
    n = 48
    x = np.linspace(2, 24, n)
    z = np.cos(np.arange(n) / 4)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    event_start, event_end = dates[24], dates[30]
    event = ((dates >= event_start) & (dates <= event_end)).astype(float)
    frame = pd.DataFrame({
        "date": dates, "x": x, "z": z,
        "sales": 8 + 2.1 * x - 0.7 * z + 5 * event
        + np.sin(np.arange(n)) * 0.1,
    })

    result = regression.run(
        frame, regression_type="OLS (mínimos cuadrados, con p-values)",
        date_col="date", target_col="sales", input_cols=["x", "z"],
        events=[{"id": "evt-1", "name": "Black Friday",
                 "start": event_start.date().isoformat(),
                 "end": event_end.date().isoformat()}],
        interaction_terms=[{"left": "x", "right": "z"}],
    )

    assert result["Estado"] == "OK"
    augmented = result["Datos con anomalías y predicciones"]
    assert augmented["Evento · Black Friday"].sum() == 7
    assert "Interacción · x × z" in augmented
    assert isinstance(result["RMSE"], (float, np.floating))
    assert "MAPE (%)" in result
    assert "Correlación real-predicho" in result


def test_regression_bootstrap_reports_completed_batches(monkeypatch):
    monkeypatch.setattr(regression, "BOOTSTRAP_ITER", 10)
    x = np.arange(20.0)
    frame = pd.DataFrame({"x": x, "y": 2 * x + np.cos(x)})
    updates = []

    result = regression.run(
        frame, regression_type="Ridge (regularización L2)",
        target_col="y", input_cols=["x"],
        progress_callback=lambda percent, message: updates.append(
            (percent, message)),
    )

    assert result["Estado"] == "OK"
    _assert_monotonic_complete(updates)
    bootstrap_messages = [message for _, message in updates
                          if message.startswith("Bootstrap:")]
    assert bootstrap_messages
    assert bootstrap_messages[-1] == "Bootstrap: 10/10 iteraciones"


def test_regression_its_mode_returns_level_slope_and_no_fake_contributions(
        monkeypatch):
    for name in ("_plot_fit", "_plot_forest", "_plot_residuals",
                 "_plot_its_effect"):
        monkeypatch.setattr(regression, name, lambda *args, **kwargs: Figure())
    n, start = 50, 34
    t = np.arange(n, dtype=float)
    event = np.zeros(n)
    event[start:start + 2] = 1.0
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n),
        "sales": 10 + 0.2 * t + 5 * (t >= start)
        + 0.3 * np.maximum(0, t - start) + 0.1 * np.sin(t),
        "event": event,
    })

    result = regression.run(
        frame, regression_type="Evento / ITS segmentada (OLS con HAC)",
        date_col="date", target_col="sales", input_cols=[],
        anomaly_cols=["event"],
    )

    assert result["Estado"] == "OK"
    estimates = result["Efectos ITS"].set_index("Métrica")["Estimación"]
    assert abs(estimates["Cambio inmediato de nivel"] - 5) < 1.0
    assert abs(estimates["Cambio de pendiente por periodo"] - 0.3) < 0.1
    assert "no implica causalidad" in result["Interpretación"]
    assert "Contribución apilada por variable" not in result


def test_multisupport_progress_advances_by_channel_kpi(monkeypatch):
    module = _load_file_module("Análisis Multisoporte.py", "multi_progress")
    for name in (
        "_plot_canal_acum", "_plot_canal_sat", "_plot_canal_marg",
        "_plot_sop_acum", "_plot_sop_sat", "_plot_sop_marg",
    ):
        monkeypatch.setattr(module, name, lambda *args, **kwargs: Figure())

    dates = pd.date_range("2024-01-01", periods=6)
    frame = pd.DataFrame([
        {"date": date, "channel": channel, "support": support,
         "investment": 10 + index, "sales": 20 + index}
        for index, date in enumerate(dates)
        for channel in ("Display", "Search")
        for support in ("A", "B")
    ])
    updates = []

    result = module.run(
        frame, date_col="date", canal_col="channel",
        soporte_col="support", inv_col="investment", kpi_cols=["sales"],
        progress_callback=lambda percent, message: updates.append(
            (percent, message)),
    )

    assert "Resumen" in result
    _assert_monotonic_complete(updates)
    assert any("Display · sales" in message for _, message in updates)
    assert any("Search · sales" in message for _, message in updates)
    assert any("soporte" in message.lower() for _, message in updates)


def test_geox_progress_wraps_external_design_call(monkeypatch):
    module = _load_file_module("Geo Test Google (GeoX).py", "geox_progress")

    design = SimpleNamespace(control_geos=["A", "B"],
                             treatment_geos=["C", "D"], score=0.9)
    fake_geox = SimpleNamespace(
        __version__="test",
        ExperimentType=SimpleNamespace(HOLDBACK="holdback"),
        Methodology=SimpleNamespace(TBR="tbr"),
        GeoAssignmentRule=SimpleNamespace(STRATIFIED_SAMPLING="stratified"),
        DesignConfig=lambda **kwargs: kwargs,
        Budget=lambda **kwargs: kwargs,
        Constraints=lambda **kwargs: kwargs,
        run_design=lambda **kwargs: SimpleNamespace(designs=[design]),
    )
    monkeypatch.setitem(__import__("sys").modules, "meridian_geox", fake_geox)
    monkeypatch.setattr(module, "_plot_geo_split",
                        lambda *args, **kwargs: Figure())
    monkeypatch.setattr(module, "_plot_balance",
                        lambda *args, **kwargs: Figure())

    dates = pd.date_range("2024-01-01", periods=8)
    frame = pd.DataFrame([
        {"date": date, "region": region, "sessions": 10 + index}
        for index, date in enumerate(dates)
        for region in ("A", "B", "C", "D")
    ])
    updates = []

    result = module.run(
        frame, date_col="date", region_col="region", kpi_col="sessions",
        progress_callback=lambda percent, message: updates.append(
            (percent, message)),
    )

    assert result["Estado"] == "OK"
    _assert_monotonic_complete(updates)
    assert any("Evaluando diseños" in message for _, message in updates)
    assert any("Diseños calculados" in message for _, message in updates)
