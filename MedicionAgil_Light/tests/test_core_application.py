from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pandas as pd
import pytest

from medicion_core import MedicionApplication
from medicion_core.application import _DatasetEntry, _ResultEntry
from medicion_core.errors import DataValidationError
from medicion_core.jobs import JobManager
from medicion_core.schemas import JobState
from medicion_core.sidecar import SidecarServer


def test_importing_new_core_does_not_import_tkinter():
    project = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([
        str(project / "python"), str(project / "mmm_app")])
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, medicion_core; assert 'tkinter' not in sys.modules"],
        env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_dataset_handles_filter_page_and_reset(tmp_path):
    source = tmp_path / "sample.csv"
    pd.DataFrame({"region": ["A", "B", "A"],
                  "value": [1, 2, 3]}).to_csv(source, index=False)
    app = MedicionApplication(max_workers=1)
    try:
        metadata = app.load_dataset(source)
        assert metadata.rows == 3
        assert metadata.columns == ["region", "value"]

        filtered = app.apply_filters(metadata.dataset_id, [
            {"column": "region", "values": ["A"]},
        ])
        assert filtered.rows == 2
        page = app.get_table_preview(metadata.dataset_id, limit=1)
        assert page.total_rows == 2
        assert len(page.rows) == 1
        assert page.rows[0]["region"] == "A"
        sorted_page = app.get_table_page(
            metadata.dataset_id, limit=2,
            sort=[{"column": "value", "direction": "desc"}])
        assert [row["value"] for row in sorted_page.rows] == [3, 1]

        assert app.reset_filters(metadata.dataset_id).rows == 3
        app.close_dataset(metadata.dataset_id)
        with pytest.raises(DataValidationError):
            app.get_dataset_metadata(metadata.dataset_id)
    finally:
        app.shutdown()


def test_large_source_row_count_is_deferred_and_resolved(tmp_path, monkeypatch):
    """A huge source must not block metadata; the count resolves in background."""
    from services.active_dataset import ActiveDataset

    source = tmp_path / "big.csv"
    pd.DataFrame({"region": ["A", "B", "A"],
                  "value": [1, 2, 3]}).to_csv(source, index=False)
    dataset = ActiveDataset.open_file(source)
    app = MedicionApplication(max_workers=1)
    # Treat every non-pandas source as "huge" so the count is deferred.
    app._ASYNC_COUNT_BYTES = 0
    app._datasets["dataset-1"] = _DatasetEntry(
        "dataset-1", "big", dataset, dataset, source)
    try:
        metadata = app.get_dataset_metadata("dataset-1")
        assert metadata.rows_approximate is True
        assert metadata.rows == 0
        deadline = time.time() + 5
        while app.get_dataset_metadata("dataset-1").rows_approximate \
                and time.time() < deadline:
            time.sleep(0.02)
        resolved = app.get_dataset_metadata("dataset-1")
        assert resolved.rows_approximate is False
        assert resolved.rows == 3
    finally:
        app.shutdown()


def test_job_manager_reports_completion_and_cancellation():
    manager = JobManager(max_workers=1)
    try:
        completed_id = manager.submit(
            "demo", lambda _cancel, progress:
            (progress((50, "mitad")), "result-1")[1])
        deadline = time.time() + 3
        while manager.status(completed_id).state not in {
                JobState.COMPLETED, JobState.FAILED} and time.time() < deadline:
            time.sleep(0.01)
        completed = manager.status(completed_id)
        assert completed.state == JobState.COMPLETED
        assert completed.result_id == "result-1"

        gate = manager.submit("wait", lambda cancel, _progress:
                              _wait_for_cancel(cancel))
        manager.cancel(gate)
        deadline = time.time() + 3
        while manager.status(gate).state not in {
                JobState.CANCELLED, JobState.FAILED} and time.time() < deadline:
            time.sleep(0.01)
        assert manager.status(gate).state == JobState.CANCELLED
    finally:
        manager.shutdown()


def _wait_for_cancel(cancel):
    while not cancel.wait(0.01):
        pass
    return None


def test_sidecar_requires_token_and_responds_to_health():
    token = "a" * 32
    server = SidecarServer(token, MedicionApplication(max_workers=1))
    output = io.StringIO()
    request = json.dumps({"id": "1", "token": token,
                          "operation": "health", "params": {}})
    server.run(io.StringIO(request + "\n"), output)
    response = json.loads(output.getvalue())
    assert response == {"id": "1", "ok": True,
                        "result": {"status": "ok", "protocol": 1}}


def test_sidecar_rejects_wrong_token():
    server = SidecarServer("a" * 32, MedicionApplication(max_workers=1))
    response = server.handle({"id": "1", "token": "wrong",
                              "operation": "health", "params": {}})
    server.application.shutdown()
    assert response["ok"] is False
    assert response["error"]["code"] == "SIDECAR_ERROR"



def test_result_chart_is_returned_as_png_artifact():
    import base64
    from matplotlib.figure import Figure

    app = MedicionApplication(max_workers=1)
    figure = Figure(figsize=(2, 1))
    figure.add_subplot(111).plot([0, 1], [1, 2])
    app._results["result-1"] = _ResultEntry(
        "result-1", "analysis-1", {"Ajuste": {"plot": figure}})
    try:
        artifact = app.get_result_chart("result-1", "Ajuste")
        image = base64.b64decode(artifact["data_base64"])
        assert artifact["mime_type"] == "image/png"
        assert artifact["chart"] == "Ajuste"
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        app.shutdown()


def test_build_table_aggregates_resident_pandas_dataset():
    from services.active_dataset import ActiveDataset

    app = MedicionApplication(max_workers=1)
    frame = pd.DataFrame({"region": ["A", "A", "B"],
                          "sales": ["2", "3", "7"]})
    dataset = ActiveDataset.from_frame(frame)
    app._datasets["dataset-1"] = _DatasetEntry(
        "dataset-1", "fixture", dataset, dataset, None)
    try:
        table_id = app.build_table("dataset-1", {
            "rows": ["region"],
            "values": [{"col": "sales", "agg": "sum"}],
            "column_types": {"sales": "numero"},
            "pivot": False,
        })
        page = app.get_table_page(table_id, limit=10)
        assert page.columns == ["region", "sales"]
        assert {row["region"]: row["sales"] for row in page.rows} == {
            "A": 5, "B": 7,
        }
        assert (app._datasets["dataset-1"].active.source["sales"].dtype
            == frame["sales"].dtype)
    finally:
        app.shutdown()


def test_diagnostics_imports_scientific_dependencies():
    app = MedicionApplication(max_workers=1)
    try:
        diagnostics = app.get_diagnostics()
        assert diagnostics["dependency_imports"]["pandas"] == "ok"
        assert diagnostics["dependency_imports"]["meridian_geox"] == "ok"
        assert diagnostics["packages"]["meridian-geox"] == "1.0.1"
    finally:
        app.shutdown()
