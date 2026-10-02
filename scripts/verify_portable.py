"""Smoke-check a built portable sidecar with accented JSON and real charts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from queue import Queue
import subprocess
import tempfile
import threading
import time

import numpy as np
import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", type=Path)
    args = parser.parse_args()
    token = "polaris-portable-smoke-token-2026"
    with tempfile.TemporaryDirectory(prefix="polaris-smoke-") as temporary:
        source = Path(temporary) / "regresión.csv"
        x = np.arange(48, dtype=float)
        pd.DataFrame({
            "Fecha": pd.date_range("2025-01-01", periods=len(x)),
            "Canal A": x + 3, "Canal B": np.sin(x / 4) * 7 + 10,
            "Objetivo": 3 * x + 2 * np.sin(x / 4) + 20,
        }).to_csv(source, index=False)
        process = subprocess.Popen(
            [str(args.exe.resolve()), "--token", token],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0)
        responses: Queue[dict | None] = Queue()

        def read_responses() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    responses.put(json.loads(line.decode("utf-8")))
                except (ValueError, UnicodeError):
                    continue
            responses.put(None)

        threading.Thread(target=read_responses, daemon=True).start()
        serial = 0

        def request(operation: str, params: dict | None = None, timeout: int = 60):
            nonlocal serial
            serial += 1
            identifier = str(serial)
            payload = {"id": identifier, "type": "request", "token": token,
                       "operation": operation, "params": params or {}}
            assert process.stdin is not None
            process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            process.stdin.flush()
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                response = responses.get(timeout=max(.1, end - time.monotonic()))
                if response is None:
                    raise RuntimeError("El sidecar terminó antes de responder")
                if response.get("id") != identifier or response.get("type") != "response":
                    continue
                if not response.get("ok"):
                    raise RuntimeError(f"{operation}: {response.get('error')}")
                return response["result"]
            raise TimeoutError(operation)

        try:
            print("health", request("health"), flush=True)
            dataset = request("load_dataset", {"path": str(source)})
            print("dataset", dataset["dataset_id"], flush=True)
            job_id = request("run_analysis", {
                "analysis_id": "regression", "dataset_id": dataset["dataset_id"],
                "parameters": {"regression_type": "OLS (mínimos cuadrados, con p-values)",
                               "date_col": "Fecha", "target_col": "Objetivo",
                               "input_cols": ["Canal A", "Canal B"]}})
            end = time.monotonic() + 90
            while time.monotonic() < end:
                job = request("get_job_status", {"job_id": job_id})
                if job["state"] in ("COMPLETED", "FAILED", "CANCELLED"):
                    break
                time.sleep(.1)
            assert job["state"] == "COMPLETED", job
            result_id = job["result_id"]
            summary = request("get_analysis_result", {"result_id": result_id})
            assert summary["tables"] and summary["charts"], summary
            table = request("get_result_table", {
                "result_id": result_id, "table": "Métricas in-sample (diagnóstico)",
                "offset": 0, "limit": 30})
            assert table["rows"], table
            for chart in summary["charts"]:
                artifact = request("get_result_chart", {"result_id": result_id, "chart": chart})
                assert artifact["mime_type"] == "image/png"
                assert len(artifact["data_base64"]) > 1000
            exported = request("export_analysis_bundle", {
                "result_id": result_id, "destination": temporary,
                "run_name": "Cliente de prueba", "kind": "all_results"})
            assert len(exported["saved"]) == len(summary["tables"]) + 1
            charts = request("export_analysis_bundle", {
                "result_id": result_id, "destination": temporary,
                "run_name": "Cliente de prueba", "kind": "all_charts",
                "existing_root": exported["root_dir"]})
            assert len(charts["saved"]) == len(summary["charts"])
            assert all(Path(path).exists() for path in exported["saved"] + charts["saved"])
            print(f"OK: {len(summary['tables'])} tablas, {len(summary['charts'])} gráficos", flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
