"""Small end-to-end contract from analysis service to a real plugin."""

import threading

import numpy as np
import pandas as pd

from analyses import causal_impact, regression
from services.analysis_service import run_analysis


def test_regression_plugin_runs_through_analysis_service():
    x = np.arange(30.0)
    frame = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=30),
        "x": x,
        "y": 2 * x + 1 + np.sin(x),
    })
    progress = []

    result, log = run_analysis(
        regression, frame,
        {"date_col": "date", "target_col": "y", "input_cols": ["x"]},
        {}, threading.Event(), progress.append,
    )

    assert result["Estado"] == "OK"
    assert "Métricas in-sample (diagnóstico)" in result
    assert "Validación temporal (OOS)" in result
    assert "Validación temporal (rolling OOS)" in result
    assert "[Regresión] OK" in log
    assert progress
    assert len(frame) == 30


def test_causal_impact_plugin_runs_through_analysis_service():
    rng = np.random.default_rng(7)
    dates = pd.date_range("2023-01-02", periods=80, freq="W-MON")
    common = np.linspace(100, 140, 80) + rng.normal(0, 3, 80)
    frame = pd.DataFrame([
        {
            "date": day,
            "region": region,
            "value": float(common[index] + rng.normal(0, 2) +
                           (50 if region == "A" and index >= 50 else 0)),
        }
        for index, day in enumerate(dates)
        for region in ("A", "B")
    ])

    result, log = run_analysis(
        causal_impact, frame,
        {
            "date_col": "date", "kpi_col": "value",
            "dim_cols": ["region"], "target_tasks": "region=A",
            "fecha_campana": str(dates[50].date()),
            "fecha_fin_datos": str(dates[-1].date()),
            "min_controles": 1, "max_controles": 1,
            "max_combinaciones": 2, "top_n_controles": 2,
            "umbral_correlacion": 0,
        },
        {}, threading.Event(), lambda _: None,
    )

    assert result["Estado"] == "OK"
    assert "Resumen ejecutivo" in result
    assert "[CausalImpact] OK" in log
    assert len(frame) == 160
