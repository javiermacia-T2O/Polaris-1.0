import threading

import numpy as np
import pandas as pd
import pytest

from analyses import regression
from ui.dialogs.regression_dialog import event_dates_from_indices


def test_bootstrap_uses_at_most_two_threads_and_reports_progress(monkeypatch):
    seen = []

    def fake_worker(seed, X, y, model_name):
        seen.append(threading.current_thread().name)
        return np.array([float(seed % 7), float(seed % 5)])

    monkeypatch.setattr(regression, "BOOTSTRAP_ITER", 8)
    monkeypatch.setattr(regression, "_boot_iter_worker", fake_worker)
    progress = []
    X = pd.DataFrame({"x": np.arange(12.), "z": np.arange(12.) * 2})
    result = regression._fit_with_ci(
        "Ridge (regularización L2)", X, np.arange(12.),
        progress_callback=lambda value, detail: progress.append((value, detail)))

    assert result["bootstrap_used"] is True
    assert len(seen) == 8
    assert all(name.startswith("reg-bootstrap") for name in seen)
    assert len({name for name in seen}) <= 2
    assert any("Bootstrap:" in detail for _, detail in progress)


def test_event_dates_from_indices_is_sorted_clamped_and_inclusive():
    frame = pd.DataFrame({
        "date": ["2024-01-03", "bad", "2024-01-01", "2024-01-02"]
    })
    start, end = event_dates_from_indices(frame, "date", 99, -4)
    assert start == pd.Timestamp("2024-01-01")
    assert end == pd.Timestamp("2024-01-03")


def test_temporal_holdout_uses_only_train_rows_for_ols():
    x = np.arange(30.0)
    y = 4.0 + 2.0 * x
    x[24:] = 1_000_000  # cambio extremo futuro no entra en el escalado
    metrics = regression._temporal_holdout_metrics(
        "OLS (mínimos cuadrados, con p-values)",
        pd.DataFrame({"x": x}), y)

    assert metrics.loc[0, "Estado"] == "OK"
    assert metrics.loc[0, "Observaciones entrenamiento"] == 24
    assert metrics.loc[0, "Observaciones prueba"] == 6
    assert metrics.loc[0, "RMSE OOS"] > 0


def test_rolling_origin_validation_is_chronological_and_beats_mean_baseline():
    x = np.arange(60.0)
    y = 4.0 + 2.0 * x
    metrics = regression._rolling_origin_metrics(
        "OLS (mínimos cuadrados, con p-values)",
        pd.DataFrame({"x": x}), y)

    assert metrics.loc[0, "Estado"] == "OK"
    assert metrics.loc[0, "Folds"] >= 2
    assert metrics.loc[0, "Observaciones OOS"] > 0
    assert metrics.loc[0, "RMSE OOS"] < metrics.loc[0, "RMSE baseline PRE"]


def test_automatic_selection_prefers_simpler_model_within_two_percent(
        monkeypatch):
    def metrics(name, *_args):
        rmse = 10.1 if name.startswith("OLS") else 10.0
        return pd.DataFrame([{
            "Estado": "OK", "RMSE OOS": rmse,
            "RMSE baseline PRE": 20.0, "Folds": 3,
        }])

    monkeypatch.setattr(regression, "_rolling_origin_metrics", metrics)
    selected, _, comparison = regression._select_automatic_model(
        pd.DataFrame({"x": np.arange(30.)}), np.arange(30.))

    assert selected.startswith("OLS")
    assert comparison.loc[comparison["Seleccionado"], "Modelo"].iloc[0] == selected


def test_automatic_selection_rejects_models_that_do_not_beat_baseline(
        monkeypatch):
    monkeypatch.setattr(regression, "_rolling_origin_metrics", lambda *_: pd.DataFrame([{
        "Estado": "OK", "RMSE OOS": 21.0,
        "RMSE baseline PRE": 20.0, "Folds": 3,
    }]))

    with pytest.raises(ValueError, match="Ningún modelo probado"):
        regression._select_automatic_model(
            pd.DataFrame({"x": np.arange(30.)}), np.arange(30.))


def test_tree_importance_is_not_split_into_fake_prediction_contributions():
    X = pd.DataFrame({"x": [0.0, 1.0], "z": [1.0, 0.0]})
    prediction = np.array([3.0, 9.0])
    contrib, baseline = regression._compute_contributions(
        {"coefs_original": {"es_importancia": True,
                            "intercept": 0.0, "coefs": {"x": 0.9, "z": 0.1}}},
        X, prediction)

    assert contrib.empty
    np.testing.assert_allclose(baseline.values, prediction)


def test_segmented_its_estimates_level_and_slope_with_hac_intervals():
    n, start = 72, 48
    t = np.arange(n, dtype=float)
    event = np.zeros(n)
    event[start:start + 3] = 1.0
    y = 20 + 0.2 * t + 8 * (t >= start) + 0.45 * np.maximum(0, t - start)
    y += 0.15 * np.sin(t)
    fit = regression._fit_segmented_its(
        pd.DataFrame({"control": np.cos(t / 7)}), y,
        pd.DataFrame({"Evento": event}),
        pd.Series(pd.date_range("2024-01-01", periods=n)))

    assert fit["model"].cov_type == "HAC"
    assert fit["event_start_index"] == start
    estimates = fit["its_summary"].set_index("Métrica")["Estimación"]
    assert abs(estimates["Cambio inmediato de nivel"] - 8) < 1.0
    assert abs(estimates["Cambio de pendiente por periodo"] - 0.45) < 0.1
    assert len(fit["its_series"]) == n - start


def test_its_requires_a_marked_event_and_date_column():
    frame = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=20),
                          "sales": np.arange(20.0)})
    missing_event = regression.run(
        frame, regression_type="Evento / ITS segmentada (OLS con HAC)",
        date_col="date", target_col="sales", input_cols=[])
    missing_date = regression.run(
        frame.drop(columns="date"),
        regression_type="Evento / ITS segmentada (OLS con HAC)",
        target_col="sales", input_cols=[], anomaly_cols=["event"])

    assert "evento marcado" in missing_event["Estado"]
    assert "columna de fecha" in missing_date["Estado"]

