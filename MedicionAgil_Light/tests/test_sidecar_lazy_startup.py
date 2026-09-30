"""FASE 2: the sidecar must become ready without the scientific stack.

The engine answers ``health`` and control operations before pandas, DuckDB or
the analysis services are imported. These tests lock that contract in so a
future eager import cannot silently reintroduce a multi-second startup.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from medicion_core.sidecar import SidecarServer

TOKEN = "t" * 32

PYTHON_DIR = Path(__file__).resolve().parent.parent / "python"
APP_DIR = Path(__file__).resolve().parent.parent / "mmm_app"


def _run_child(body: str) -> str:
    """Run ``body`` in a fresh interpreter and return its stdout."""
    script = (
        "import sys;"
        f"sys.path.insert(0, {str(PYTHON_DIR)!r});"
        f"sys.path.insert(0, {str(APP_DIR)!r});"
        + body
    )
    completed = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_health_does_not_import_pandas():
    """A health-only session must never load the scientific stack.

    Run in a child interpreter: other tests in this session may already have
    imported pandas, so the shared ``sys.modules`` cannot be trusted.
    """
    output = _run_child(
        "from medicion_core.sidecar import SidecarServer;"
        "server = SidecarServer('t' * 32);"
        "response = server.handle({'id': '1', 'token': 't' * 32,"
        " 'operation': 'health'});"
        "assert response['ok'] is True;"
        "assert response['result'] == {'status': 'ok', 'protocol': 1};"
        "assert server._application is None;"
        "print('pandas' in sys.modules)"
    )
    assert output == "False"


def test_cancel_request_is_a_control_operation_without_the_application():
    server = SidecarServer(TOKEN)
    response = server.handle({
        "id": "1", "token": TOKEN, "operation": "cancel_request",
        "params": {"target_id": "missing"},
    })
    assert response["ok"] is True
    assert response["result"] == {"cancelled": False}
    assert server._application is None


def test_unknown_operation_is_rejected_without_building_the_application():
    server = SidecarServer(TOKEN)
    response = server.handle({
        "id": "1", "token": TOKEN, "operation": "not_a_real_operation",
    })
    assert response["ok"] is False
    assert server._application is None


def test_unauthorized_request_is_rejected():
    server = SidecarServer(TOKEN)
    response = server.handle({"id": "1", "token": "wrong", "operation": "health"})
    assert response["ok"] is False


def test_importing_the_sidecar_module_is_cheap():
    """Importing ``medicion_core.sidecar`` must not pull pandas."""
    output = _run_child(
        "import medicion_core.sidecar;"
        "print('pandas' in sys.modules)"
    )
    assert output == "False"


def test_application_is_built_on_first_data_operation():
    """The first real operation builds the application exactly once."""
    server = SidecarServer(TOKEN)
    assert server._application is None
    first = server.application
    assert first is not None
    # A second access reuses the same instance (no duplicate construction).
    assert server.application is first


@pytest.mark.parametrize("operation", ["health", "cancel_request"])
def test_control_operations_never_touch_the_application(operation):
    server = SidecarServer(TOKEN)
    server.handle({"id": "1", "token": TOKEN, "operation": operation,
                   "params": {}})
    assert server._application is None