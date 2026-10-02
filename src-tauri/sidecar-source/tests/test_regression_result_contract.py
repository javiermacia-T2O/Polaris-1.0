"""Regression outputs must stay usable through the public application API."""

import time
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from medicion_core import MedicionApplication
from medicion_core.schemas import JobState


def test_regression_tables_and_charts_round_trip(tmp_path):
    x = np.arange(48, dtype=float)
    source = tmp_path / "regression.csv"
    pd.DataFrame({
        "Fecha": pd.date_range("2025-01-01", periods=len(x)),
        "Canal A": x + 2,
        "Canal B": np.sin(x / 4) * 7 + 10,
        "Objetivo": 3 * x + 2 * np.sin(x / 4) + 20,
    }).to_csv(source, index=False)
    app = MedicionApplication(max_workers=1)
    try:
        dataset = app.load_dataset(source)
        job_id = app.run_analysis("regression", dataset.dataset_id, {
            "regression_type": "OLS (mínimos cuadrados, con p-values)",
            "date_col": "Fecha", "target_col": "Objetivo",
            "input_cols": ["Canal A", "Canal B"],
        })
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = app.get_job_status(job_id)
            if job.state in {JobState.COMPLETED, JobState.FAILED}:
                break
            time.sleep(.05)
        assert job.state == JobState.COMPLETED, job.error
        summary = app.get_analysis_result(job.result_id)
        assert "Métricas in-sample (diagnóstico)" in summary.tables
        assert "Ajuste · Real vs Predicho" in summary.charts
        assert "Diagnóstico de ejecución" not in summary.scalars
        metrics = app.get_result_table(job.result_id, "Métricas in-sample (diagnóstico)")
        assert metrics["rows"]
        for chart in summary.charts:
            artifact = app.get_result_chart(job.result_id, chart)
            assert artifact["mime_type"] == "image/png"
            assert len(artifact["data_base64"]) > 1000
        figures = app._results[job.result_id].value
        fit = figures["Ajuste · Real vs Predicho"]["plot"]
        assert fit.axes[0].get_legend().get_frame().get_facecolor()[:3] == (1, 1, 1)
        forest = figures["Coeficientes · Forest plot"]["plot"]
        left, right = forest.axes[0].get_xlim()
        assert abs(left + right) < 1e-8
        decomposition = figures["Contribución apilada por variable"]["plot"]
        assert decomposition.axes[0].get_legend().get_frame().get_facecolor()[:3] == (1, 1, 1)
    finally:
        app.shutdown()


def test_mojibake_automatic_option_is_accepted():
    from analyses.regression import REGRESSION_TYPES, run
    x = np.arange(36, dtype=float)
    frame = pd.DataFrame({"Fecha": pd.date_range("2025-01-01", periods=36),
                          "X": x, "Y": x * 2 + np.sin(x)})
    # Exercise the same legacy text visible in the user's error screenshot.
    result = run(frame, regression_type="AutomÃ¡tico (selecciÃ³n temporal OOS)",
                 date_col="Fecha", target_col="Y", input_cols=["X"])
    assert result["Estado"] == "OK"
    assert result["Modelo solicitado"] == REGRESSION_TYPES[0]


def test_sidecar_reads_unicode_requests_with_legacy_windows_codepage():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env["PYTHONPATH"] = os.pathsep.join([str(root / "python"), str(root / "mmm_app")])
    script = (
        "import json,sys; from medicion_core.sidecar import _redirect_engine_output; "
        "output=sys.stdout; _redirect_engine_output(); "
        "value=json.loads(sys.stdin.readline()); "
        "output.write(json.dumps(value,ensure_ascii=False)+'\\n'); output.flush()"
    )
    request = {"chart": "Ajuste · Real vs Predicho",
               "method": "Automático (selección temporal OOS)"}
    completed = subprocess.run(
        [sys.executable, "-c", script], input=(json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"),
        capture_output=True, env=env, timeout=20)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert json.loads(completed.stdout.decode("utf-8")) == request


def test_regression_validation_error_is_a_failed_job(tmp_path):
    source = tmp_path / "invalid-regression.csv"
    pd.DataFrame({"Fecha": pd.date_range("2025-01-01", periods=4),
                  "X": [1, 2, 3, 4]}).to_csv(source, index=False)
    app = MedicionApplication(max_workers=1)
    try:
        dataset = app.load_dataset(source)
        job_id = app.run_analysis("regression", dataset.dataset_id, {
            "date_col": "Fecha", "target_col": "No existe", "input_cols": ["X"]})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = app.get_job_status(job_id)
            if job.state in {JobState.COMPLETED, JobState.FAILED}:
                break
            time.sleep(.05)
        assert job.state == JobState.FAILED
        assert job.result_id is None
        assert "variable objetivo" in job.error["message"]
    finally:
        app.shutdown()
