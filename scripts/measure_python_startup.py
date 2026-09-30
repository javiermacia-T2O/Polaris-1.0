"""Measure how long the Python sidecar takes to become ready.

Run with the project venv:

    .venv\\Scripts\\python.exe scripts\\measure_python_startup.py

It reports, in milliseconds:

* ``import medicion_core.sidecar``  -> module import cost
* ``MedicionApplication()``         -> application construction cost
* ``health`` round-trip              -> first control response cost

The numbers are printed as JSON so they can be pasted into the FASE 2 report.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
PYTHON_DIR = WORKSPACE / "MedicionAgil_Light" / "python"
APP_DIR = WORKSPACE / "MedicionAgil_Light" / "mmm_app"


def _child() -> int:
    sys.path.insert(0, str(PYTHON_DIR))
    sys.path.insert(0, str(APP_DIR))

    started = time.perf_counter()
    from medicion_core.sidecar import SidecarServer  # noqa: E402

    imported = time.perf_counter()

    server = SidecarServer("x" * 32)
    constructed = time.perf_counter()

    response = server.handle({"id": "1", "token": "x" * 32, "operation": "health"})
    answered = time.perf_counter()

    print(json.dumps({
        "import_ms": round((imported - started) * 1000, 1),
        "construct_ms": round((constructed - imported) * 1000, 1),
        "health_ms": round((answered - constructed) * 1000, 1),
        "total_ms": round((answered - started) * 1000, 1),
        "health_ok": bool(response.get("ok")),
    }))
    return 0


def main() -> int:
    if os.environ.get("POLARIS_MEASURE_CHILD") == "1":
        return _child()
    env = {**os.environ, "POLARIS_MEASURE_CHILD": "1"}
    completed = subprocess.run([sys.executable, __file__], env=env,
                               capture_output=True, text=True)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())