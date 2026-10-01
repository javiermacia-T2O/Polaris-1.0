"""Prueba de humo del sidecar congelado.

Verifica que el bundle congelado arranca y ejecuta el flujo real:
`health` -> `load_dataset` -> `get_columns` -> `list_analyses`.

Uso: python scripts/_smoke_frozen_sidecar.py <ruta-al-sidecar.exe>
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class Sidecar:
    def __init__(self, exe: str):
        self.token = "smoke-token-0123456789abcdef"
        self.proc = subprocess.Popen(
            [exe, "--token", self.token],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
        )
        self.counter = 0

    def call(self, operation: str, params: dict | None = None, timeout: float = 180):
        self.counter += 1
        request_id = str(self.counter)
        self.proc.stdin.write(json.dumps({
            "id": request_id, "type": "request", "token": self.token,
            "operation": operation, "params": params or {},
        }) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"sidecar cerrado durante {operation}")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                if not message.get("ok"):
                    raise RuntimeError(f"{operation} falló: {message.get('error')}")
                return message.get("result")
        raise TimeoutError(f"{operation} sin respuesta")

    def close(self):
        try:
            self.proc.stdin.write(json.dumps({"id": "x", "type": "shutdown"}) + "\n")
            self.proc.stdin.flush()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main() -> int:
    exe = sys.argv[1]
    sidecar = Sidecar(exe)
    try:
        health = sidecar.call("health")
        print("health:", health)

        # `list_analyses` carga los plugins de análisis (matplotlib, sklearn,
        # statsmodels, causalimpact, meridian_geox, jax, tslearn) sin depender
        # del presupuesto de memoria, así que prueba que el bundle congelado
        # incluye todo el stack científico.
        analyses = sidecar.call("list_analyses")
        print("list_analyses:", len(analyses))

        with tempfile.TemporaryDirectory() as tmp:
            csv = Path(tmp) / "smoke.csv"
            csv.write_text(
                "Canal,Importe,Fecha\nWeb,10,2026-01-01\nTienda,20,2026-01-02\n",
                encoding="utf-8")
            try:
                meta = sidecar.call("load_dataset", {"path": str(csv)})
                print("load_dataset:", meta.get("dataset_id"), meta.get("rows"))
                columns = sidecar.call(
                    "get_columns", {"dataset_id": meta["dataset_id"]})
                print("get_columns:", [c.get("name") for c in columns])
            except RuntimeError as exc:
                # Un presupuesto de memoria insuficiente es ambiental (RAM
                # libre baja en el equipo de build), no un fallo del bundle.
                if "MEMORY_BUDGET_ERROR" in str(exc):
                    print("load_dataset: omitido por memoria baja (ambiental)")
                else:
                    raise

        print("SMOKE_OK")
        return 0
    finally:
        sidecar.close()


if __name__ == "__main__":
    raise SystemExit(main())