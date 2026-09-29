"""Smoke-probe the frozen sidecar over JSON lines.

Spawns the packaged ``medicion-sidecar.exe``, authenticates with a token and
exercises the operations that were recently fixed (``get_column_values`` and
the table-builder projection in ``preview_table``). The probe writes a tiny
synthetic CSV so no real dataset is ever read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TOKEN = "polaris-probe-token-0123456789abcdef"


def _send(proc: subprocess.Popen, payload: dict) -> dict:
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("sidecar closed stdout")
    return json.loads(line)


def main() -> int:
    exe = Path(sys.argv[1]).resolve()
    if not exe.exists():
        print(f"missing exe: {exe}")
        return 2

    import pandas as pd

    frame = pd.DataFrame(
        {
            "Grupo": ["A", "A", "B", "B", "C", "C"],
            "Canal": ["X", "Y", "X", "Y", "X", "Y"],
            "KPI": [1, 2, 3, 4, 5, 6],
        }
    )
    csv_path = Path(tempfile.gettempdir()) / "polaris_probe_frozen.csv"
    frame.to_csv(csv_path, index=False)

    env = dict(os.environ)
    env["MEDICION_SESSION_TOKEN"] = TOKEN
    proc = subprocess.Popen(
        [str(exe)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        health = _send(proc, {"id": 1, "token": TOKEN, "operation": "health"})
        print("health:", health.get("ok"))

        loaded = _send(
            proc,
            {
                "id": 2,
                "token": TOKEN,
                "operation": "load_dataset",
                "params": {"path": str(csv_path)},
            },
        )
        dataset_id = loaded["result"]["dataset_id"]
        print("load_dataset:", loaded.get("ok"), dataset_id)

        values = _send(
            proc,
            {
                "id": 3,
                "token": TOKEN,
                "operation": "get_column_values",
                "params": {"dataset_id": dataset_id, "column": "Grupo"},
            },
        )
        print("get_column_values:", values.get("ok"), values.get("result"))

        row_only = _send(
            proc,
            {
                "id": 4,
                "token": TOKEN,
                "operation": "preview_table",
                "params": {"dataset_id": dataset_id, "recipe": {"rows": ["Grupo"]}},
            },
        )
        print("preview rows only:", row_only.get("result", {}).get("columns"))

        row_col = _send(
            proc,
            {
                "id": 5,
                "token": TOKEN,
                "operation": "preview_table",
                "params": {
                    "dataset_id": dataset_id,
                    "recipe": {"rows": ["Grupo"], "columns": ["Canal"]},
                },
            },
        )
        print("preview rows+cols:", row_col.get("result", {}).get("columns"))

        metadata = _send(
            proc,
            {
                "id": 6,
                "token": TOKEN,
                "operation": "get_dataset_metadata",
                "params": {"dataset_id": dataset_id},
            },
        )
        result = metadata.get("result", {})
        print("metadata rows:", result.get("rows"),
              "approximate:", result.get("rows_approximate"))
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())