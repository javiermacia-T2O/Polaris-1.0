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


def test_diagnostics_imports_scientific_dependencies():
    app = MedicionApplication(max_workers=1)
    try:
        diagnostics = app.get_diagnostics()
        assert diagnostics["dependency_imports"]["pandas"] == "ok"
        assert diagnostics["dependency_imports"]["meridian_geox"] == "ok"
        assert diagnostics["packages"]["meridian-geox"] == "1.0.1"
    finally:
        app.shutdown()
