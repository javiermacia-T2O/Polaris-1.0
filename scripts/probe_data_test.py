"""Exercise the frozen sidecar against every dataset in ``data test/``.

The probe never inspects dataset contents itself: it only asks the sidecar to
load each file and then reports timings, metadata and a small table-builder
preview. This validates that the packaged engine handles both small and
multi-gigabyte sources without hanging.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

TOKEN = "polaris-probe-token-0123456789abcdef"


class Sidecar:
    def __init__(self, exe: Path) -> None:
        env = dict(os.environ)
        env["MEDICION_SESSION_TOKEN"] = TOKEN
        self.proc = subprocess.Popen(
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self._id = 0

    def call(self, operation: str, params: dict | None = None) -> dict:
        self._id += 1
        payload = {"id": self._id, "token": TOKEN, "operation": operation}
        if params:
            payload["params"] = params
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("sidecar closed stdout")
        return json.loads(line)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.proc.wait(timeout=60)


def probe(sidecar: Sidecar, path: Path) -> None:
    print(f"\n=== {path.name} ({path.stat().st_size / 1024**2:.1f} MB) ===")
    started = time.perf_counter()
    loaded = sidecar.call("load_dataset", {"path": str(path)})
    elapsed = (time.perf_counter() - started) * 1000
    if not loaded.get("ok"):
        print(f"  load_dataset FAILED in {elapsed:.0f} ms: {loaded.get('error')}")
        return
    dataset_id = loaded["result"]["dataset_id"]
    print(f"  load_dataset ok in {elapsed:.0f} ms  rows={loaded['result'].get('rows')}")

    columns = sidecar.call("get_columns", {"dataset_id": dataset_id})
    names = [c["name"] for c in columns.get("result", [])]
    print(f"  get_columns ok  n={len(names)}")

    if not names:
        return

    # Pick a low-cardinality-looking column for the filter probe.
    first = names[0]
    values = sidecar.call(
        "get_column_values",
        {"dataset_id": dataset_id, "column": first, "limit": 500},
    )
    result = values.get("result") or {}
    print(f"  get_column_values ok  truncated={result.get('truncated')} "
          f"n={len(result.get('values') or [])}")

    started = time.perf_counter()
    preview = sidecar.call(
        "preview_table",
        {"dataset_id": dataset_id, "recipe": {"rows": [first]}},
    )
    elapsed = (time.perf_counter() - started) * 1000
    cols = (preview.get("result") or {}).get("columns")
    print(f"  preview rows-only ok in {elapsed:.0f} ms  cols={cols}")

    started = time.perf_counter()
    built = sidecar.call(
        "build_table",
        {"dataset_id": dataset_id, "recipe": {"rows": [first]}},
    )
    elapsed = (time.perf_counter() - started) * 1000
    print(f"  build_table ok in {elapsed:.0f} ms  ok={built.get('ok')}")


def main() -> int:
    exe = Path(sys.argv[1]).resolve()
    folder = Path(sys.argv[2]).resolve()
    files = sorted(p for p in folder.iterdir() if p.is_file())
    sidecar = Sidecar(exe)
    try:
        health = sidecar.call("health")
        print("health:", health.get("ok"))
        for path in files:
            try:
                probe(sidecar, path)
            except Exception as exc:  # keep going across datasets
                print(f"  ERROR: {exc}")
    finally:
        sidecar.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())