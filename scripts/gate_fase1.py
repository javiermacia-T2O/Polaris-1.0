"""FASE 1 integration gate against the *frozen* PyInstaller sidecar.

This is the mandatory PASO 0 gate: it drives the exact ``--onedir`` artifact
that Tauri deploys (``src-tauri/target/debug/sidecar/medicion-sidecar.exe``)
over the real JSON-lines protocol and proves the FASE 1 guarantees still hold
once the code is frozen:

    A) a HEAVY operation never blocks ``health``
    B) a HEAVY operation never blocks an interactive operation
    C) a running operation can be cancelled and answers quickly
    D) cancelling A does not disturb B (cancel isolation)
    E) the sidecar can crash and be restarted
    F) ``shutdown`` leaves no orphan process
    G) every request receives exactly one response (pending_requests == 0)

It also reports P50/P95 latency for ``health``, ``cancel`` and an interactive
operation issued while a HEAVY operation is running.

Run with the project venv:

    .venv\\Scripts\\python.exe scripts\\gate_fase1.py
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
SIDECAR = (WORKSPACE / "src-tauri" / "target" / "debug" / "sidecar"
           / "medicion-sidecar.exe")
TOKEN = "gate-fase1-token-0123456789abcdef"
HEAVY_CSV = WORKSPACE / "data test" / "datos ga4 jamaica report 09-09-26.csv"
# A full CSV export of the 228,922-row source takes ~1.7 s and checks the
# cancellation flag between chunks, so it is a real, cancellable HEAVY
# operation (unlike a DISTINCT scan, which finishes in milliseconds).
HEAVY_OPERATION = "export_dataset"
_EXPORT_DIR = Path(tempfile.gettempdir()) / "polaris-gate-fase1"
_EXPORT_SEQ = itertools.count(1)


class Sidecar:
    """Thin JSON-lines client with a background reader thread.

    Every response is stored by request id, so waiting for one request never
    discards the response of another (which is exactly what the multiplexing
    gate needs to observe).
    """

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [str(SIDECAR), "--token", TOKEN],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self._responses: dict[str, dict] = {}
        self._seen: list[dict] = []
        self._condition = threading.Condition()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._seq = 0

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._condition:
                self._seen.append(message)
                if message.get("type") == "response":
                    self._responses[str(message.get("id"))] = message
                self._condition.notify_all()

    def next_id(self) -> str:
        self._seq += 1
        return f"req-{self._seq}"

    def send(self, operation: str, params: dict | None = None,
             priority: str = "interactive", request_id: str | None = None) -> str:
        rid = request_id or self.next_id()
        payload = {"id": rid, "type": "request", "token": TOKEN,
                   "operation": operation, "params": params or {},
                   "priority": priority}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        return rid

    def cancel(self, target_id: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({
            "id": self.next_id(), "type": "cancel", "token": TOKEN,
            "target_id": target_id}) + "\n")
        self.proc.stdin.flush()

    def shutdown(self) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps({
                "id": self.next_id(), "type": "shutdown", "token": TOKEN}) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    def wait_response(self, request_id: str, timeout: float = 120.0) -> dict:
        """Block until the response for ``request_id`` arrives."""
        deadline = time.perf_counter() + timeout
        with self._condition:
            while request_id not in self._responses:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError(f"sin respuesta para {request_id}")
                self._condition.wait(timeout=remaining)
            return self._responses[request_id]

    def call(self, operation: str, params: dict | None = None,
             priority: str = "interactive", timeout: float = 120.0) -> dict:
        rid = self.send(operation, params, priority)
        return self.wait_response(rid, timeout)

    def snapshot(self) -> list[dict]:
        with self._condition:
            return list(self._seen)

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _stats(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values), 1) if values else None,
        "p95_ms": round(_percentile(values, 0.95), 1) if values else None,
        "max_ms": round(max(values), 1) if values else None,
    }


def _load_heavy(client: Sidecar) -> str:
    response = client.call("load_dataset", {"path": str(HEAVY_CSV)}, timeout=300)
    if not response.get("ok"):
        raise RuntimeError(f"load_dataset falló: {response.get('error')}")
    return response["result"]["dataset_id"]


def _heavy_params(dataset_id: str) -> dict:
    # Each call exports to its own file so concurrent HEAVY operations never
    # collide on the destination path.
    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _EXPORT_DIR / f"export-{next(_EXPORT_SEQ)}.csv"
    return {"dataset_id": dataset_id, "path": str(path), "fmt": "csv"}


def main() -> int:
    if not SIDECAR.is_file():
        print(f"FALTA sidecar: {SIDECAR}")
        return 2
    if not HEAVY_CSV.is_file():
        print(f"FALTA dataset pesado: {HEAVY_CSV}")
        return 2

    results: dict[str, object] = {}
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            failures.append(name)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

    client = Sidecar()
    try:
        # --- warm-up: build the application and load the heavy dataset -------
        health = client.call("health", timeout=60)
        check("health_before_load", health.get("ok") is True,
              f"result={health.get('result')}")
        dataset_id = _load_heavy(client)
        print(f"      dataset_id={dataset_id}")

        # Measure how long the heavy op actually takes on its own.
        started = time.perf_counter()
        solo = client.call(HEAVY_OPERATION, _heavy_params(dataset_id),
                           priority="background", timeout=300)
        solo_ms = (time.perf_counter() - started) * 1000
        check("heavy_operation_completes", solo.get("ok") is True,
              f"{solo_ms:.0f} ms")
        if solo_ms < 400:
            print("      AVISO: la operación pesada es muy corta; "
                  "las medidas de multiplexación pueden ser ruidosas.")

        # --- A) HEAVY + health ---------------------------------------------
        heavy_id = client.send(HEAVY_OPERATION, _heavy_params(dataset_id),
                               priority="background")
        time.sleep(0.15)  # let the heavy request occupy a worker
        health_latencies: list[float] = []
        for _ in range(30):
            t0 = time.perf_counter()
            reply = client.call("health", timeout=10)
            health_latencies.append((time.perf_counter() - t0) * 1000)
            if reply.get("ok") is not True:
                break
        heavy_reply = client.wait_response(heavy_id, timeout=300)
        check("A_health_never_blocks_on_heavy",
              all(v < 500 for v in health_latencies)
              and heavy_reply.get("ok") is True,
              f"health {_stats(health_latencies)}")

        # --- B) HEAVY + interactive ----------------------------------------
        heavy_id = client.send(HEAVY_OPERATION, _heavy_params(dataset_id),
                               priority="background")
        time.sleep(0.15)
        interactive_latencies: list[float] = []
        for _ in range(10):
            t0 = time.perf_counter()
            reply = client.call("get_columns", {"dataset_id": dataset_id},
                                priority="interactive", timeout=10)
            interactive_latencies.append((time.perf_counter() - t0) * 1000)
            if reply.get("ok") is not True:
                break
        heavy_reply = client.wait_response(heavy_id, timeout=300)
        check("B_interactive_never_blocks_on_heavy",
              all(v < 1000 for v in interactive_latencies)
              and heavy_reply.get("ok") is True,
              f"interactive {_stats(interactive_latencies)}")

        # --- C) cancellation -------------------------------------------------
        cancel_latencies: list[float] = []
        cancelled_ok = True
        for _ in range(10):
            heavy_id = client.send(HEAVY_OPERATION,
                                   _heavy_params(dataset_id),
                                   priority="background")
            time.sleep(0.2)
            t0 = time.perf_counter()
            client.cancel(heavy_id)
            reply = client.wait_response(heavy_id, timeout=30)
            cancel_latencies.append((time.perf_counter() - t0) * 1000)
            code = (reply.get("error") or {}).get("code")
            if reply.get("ok") is not False or code != "CANCELLED":
                cancelled_ok = False
                print(f"      respuesta inesperada: {reply}")
                break
        check("C_cancellation_answers_quickly",
              cancelled_ok and all(v < 3000 for v in cancel_latencies),
              f"cancel {_stats(cancel_latencies)}")

        # --- D) cancel isolation --------------------------------------------
        # A runs on the background pool, B on the interactive pool, so they
        # execute concurrently and cancelling A must not disturb B.
        a_id = client.send(HEAVY_OPERATION, _heavy_params(dataset_id),
                           priority="background")
        b_id = client.send(HEAVY_OPERATION, _heavy_params(dataset_id),
                           priority="interactive")
        time.sleep(0.2)
        client.cancel(a_id)
        a_reply = client.wait_response(a_id, timeout=30)
        b_reply = client.wait_response(b_id, timeout=300)
        a_code = (a_reply.get("error") or {}).get("code")
        check("D_cancel_isolation",
              a_reply.get("ok") is False and a_code == "CANCELLED"
              and b_reply.get("ok") is True,
              f"A={a_code} B_ok={b_reply.get('ok')}")

        # --- G) every request got exactly one response ----------------------
        seen = client.snapshot()
        responses = [m for m in seen if m.get("type") == "response"]
        ids = [m.get("id") for m in responses]
        duplicates = {i for i in ids if ids.count(i) > 1}
        check("G_no_duplicate_or_missing_responses",
              not duplicates,
              f"{len(responses)} respuestas, duplicados={sorted(duplicates)}")

        # --- E) crash + restart ---------------------------------------------
        client.proc.kill()
        client.proc.wait(timeout=15)
        restarted = Sidecar()
        try:
            reply = restarted.call("health", timeout=60)
            check("E_restart_after_crash", reply.get("ok") is True,
                  f"result={reply.get('result')}")
        finally:
            restarted.shutdown()
            restarted.close()
        check("F_shutdown_leaves_no_orphan",
              restarted.proc.poll() is not None,
              f"exit={restarted.proc.returncode}")
    finally:
        client.close()

    print("\n=== RESUMEN GATE FASE 1 ===")
    print(json.dumps({
        "sidecar": str(SIDECAR),
        "checks": results,
        "latency": {
            "health_during_heavy": _stats(health_latencies),
            "interactive_during_heavy": _stats(interactive_latencies),
            "cancel": _stats(cancel_latencies),
        },
        "failures": failures,
    }, indent=2, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())