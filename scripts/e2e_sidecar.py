"""End-to-end smoke of the sidecar protocol used by the React UI.

Drives the same JSON-lines operations the Tauri host forwards, covering the
full workflow: load -> columns -> preview -> analyses -> manifest -> run ->
poll -> result -> table -> chart -> export.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
SIDECAR = WORKSPACE / "src-tauri" / "target" / "debug" / "sidecar" / "medicion-sidecar.exe"
TOKEN = "0123456789abcdef0123456789abcdef"
CSV = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"C:\Users\javier.macia\Desktop\APP MEDICIÓN\MedicionAgil_Light\data\jamaica.csv")


class Client:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [str(SIDECAR), "--token", TOKEN],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.seq = 0

    def call(self, op: str, **params):
        self.seq += 1
        request = {"id": str(self.seq), "token": TOKEN,
                   "operation": op, "params": params}
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(f"sidecar closed during {op}")
        envelope = json.loads(line)
        if not envelope.get("ok"):
            raise RuntimeError(f"{op} failed: {envelope.get('error')}")
        return envelope["result"]

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def main() -> int:
    if not SIDECAR.is_file():
        print(f"FALTA sidecar: {SIDECAR}")
        return 2
    if not CSV.is_file():
        print(f"FALTA dataset: {CSV}")
        return 2
    client = Client()
    try:
        print("health:", client.call("health"))
        meta = client.call("load_dataset", path=str(CSV))
        print("load_dataset:", meta["name"], meta["rows"], "filas",
              meta["backend"])
        dataset_id = meta["dataset_id"]
        columns = client.call("get_columns", dataset_id=dataset_id)
        print("get_columns:", len(columns), "columnas")
        page = client.call("get_table_preview", dataset_id=dataset_id,
                           offset=0, limit=5)
        print("get_table_preview:", len(page["rows"]), "filas de",
              page["total_rows"])
        analyses = client.call("list_analyses")
        print("list_analyses:", [a["id"] for a in analyses])
        target = next((a for a in analyses if a["id"] == "regression"), analyses[0])
        manifest = client.call("get_analysis_manifest",
                               analysis_id=target["id"], dataset_id=dataset_id)
        fields = manifest.get("parameter_schema", {}).get("fields", [])
        print("manifest:", target["id"], len(fields), "campos")
        params = {}
        for field in fields:
            if field.get("key") and field.get("default") is not None:
                params[field["key"]] = field["default"]
        # Fill multi-select inputs so the model has explanatory variables.
        for field in fields:
            if field.get("type") == "multi" and not params.get(field.get("key")):
                options = [str(option) for option in field.get("options", [])]
                target_col = params.get("target_col")
                params[field["key"]] = [o for o in options if o != target_col][:4]
        job_id = client.call("run_analysis", analysis_id=target["id"],
                             dataset_id=dataset_id, parameters=params)
        print("run_analysis -> job", job_id)
        deadline = time.time() + 300
        status = {}
        while time.time() < deadline:
            status = client.call("get_job_status", job_id=job_id)
            print("  job:", status["state"], f"{status['progress']}%")
            if status["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(1.5)
        if status.get("state") != "COMPLETED":
            print("JOB NO COMPLETADO:", status)
            return 1
        result_id = status["result_id"]
        summary = client.call("get_analysis_result", result_id=result_id)
        print("result:", len(summary["tables"]), "tablas,",
              len(summary["charts"]), "gráficos,",
              len(summary["scalars"]), "métricas")
        if summary["tables"]:
            table = client.call("get_result_table", result_id=result_id,
                                table=summary["tables"][0], offset=0, limit=5)
            print("result_table:", table["total_rows"], "filas")
        if summary["charts"]:
            chart = client.call("get_result_chart", result_id=result_id,
                                chart=summary["charts"][0])
            print("result_chart:", chart["mime_type"],
                  len(chart["data_base64"]), "bytes b64")
        export_path = Path(tempfile.gettempdir()) / "medicion-e2e-export.csv"
        exported = client.call("export_dataset", dataset_id=dataset_id,
                               path=str(export_path), fmt="csv")
        print("export_dataset:", exported, export_path.stat().st_size, "bytes")
        second = client.call("load_dataset", path=str(CSV))["dataset_id"]
        merged = client.call("merge_datasets", dataset_ids=[dataset_id, second],
                             name="e2e-unido", operation="concat", mode="all")
        print("merge_datasets:", merged["name"], merged["rows"], "filas")
        client.call("close_dataset", dataset_id=second)
        client.call("close_dataset", dataset_id=merged["dataset_id"])
        cache = client.call("get_cache_info")
        print("get_cache_info:", cache["size_mb"], "MB")
        freed = client.call("free_memory", hard=False)
        print("free_memory:", freed["collected"], "objetos liberados")
        cleared = client.call("clear_cache")
        print("clear_cache:", cleared["removed"], "archivos borrados")
        date_cols = client.call("get_date_columns", dataset_id=dataset_id)
        print("get_date_columns:", [c["column"] for c in date_cols])
        if date_cols:
            date_col = date_cols[0]["column"]
            rng = client.call("get_date_range", dataset_id=dataset_id,
                              column=date_col)
            print("get_date_range:", rng["start"], "->", rng["end"],
                  rng["granularity"])
            filtered = client.call("apply_date_range", dataset_id=dataset_id,
                                   column=date_col, start=rng["start"],
                                   end=rng["end"])
            print("apply_date_range:", filtered["rows"], "filas")
            client.call("reset_date_range", dataset_id=dataset_id)
        # --- Constructor de tablas: multi-fila, multi-pivote, multi-métrica ---
        names = [c["name"] for c in columns]
        numeric = [c["name"] for c in columns
                   if c.get("type") in {"int64", "float64", "Int64", "Float64"}]
        categorical = [c["name"] for c in columns
                       if c["name"] not in numeric]
        if len(categorical) >= 2 and numeric:
            recipe = {
                "rows": categorical[:1],
                "columns": categorical[1:2],
                "values": [{"col": numeric[0], "agg": "sum", "pivot": True},
                           {"col": numeric[0], "agg": "mean", "pivot": False}],
            }
            preview = client.call("preview_table", dataset_id=dataset_id,
                                  recipe=recipe, limit=10)
            print("preview_table:", len(preview["rows"]), "filas,",
                  len(preview["columns"]), "columnas, aprox:",
                  preview.get("approximate"))
            built = client.call("build_table", dataset_id=dataset_id,
                                recipe=recipe)
            built_id = built if isinstance(built, str) else built["dataset_id"]
            meta = client.call("get_dataset_metadata", dataset_id=built_id)
            print("build_table:", meta["name"], meta["rows"], "filas")
            client.call("close_dataset", dataset_id=built_id)
        print("E2E OK")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())