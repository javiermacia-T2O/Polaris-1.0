"""Authenticated JSON-lines sidecar over stdin/stdout.

The transport never opens a network port. Each request is one JSON object and
each response is one JSON object, making Rust responsible only for lifecycle
and forwarding small typed messages.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor

from core import engine
from core.tasks import TaskCancelled
from typing import Any, Callable

from pydantic import BaseModel

from .application import MedicionApplication
from .errors import AppError, SidecarError
from .observability import event


class SidecarServer:
    def __init__(self, token: str, application: MedicionApplication | None = None):
        if len(token) < 24:
            raise SidecarError("El token de sesión del sidecar no es válido.")
        self.token = token
        self.application = application or MedicionApplication()

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        operation = str(request.get("operation") or "")
        started = time.perf_counter()
        try:
            if request.get("token") != self.token:
                raise SidecarError("Sesión no autorizada.")
            params = request.get("params") or {}
            result = self._dispatch(operation, params)
            event("info", "sidecar", operation,
                  duration_ms=round((time.perf_counter() - started) * 1000, 2),
                  dataset_id=params.get("dataset_id"))
            return {"id": request_id, "ok": True,
                    "result": _jsonable(result)}
        except AppError as exc:
            event("error", "sidecar", operation,
                  duration_ms=round((time.perf_counter() - started) * 1000, 2),
                  error_code=exc.code)
            return {"id": request_id, "ok": False, "error": exc.as_dict()}
        except (KeyError, TypeError, ValueError) as exc:
            error = SidecarError("Solicitud inválida.",
                                 details={"reason": str(exc)})
            return {"id": request_id, "ok": False,
                    "error": error.as_dict()}
        except MemoryError as exc:
            event("error", "sidecar", operation,
                  duration_ms=round((time.perf_counter() - started) * 1000, 2),
                  error_code="memory_error")
            error = SidecarError(
                "No hay memoria suficiente para esta operación.",
                details={"reason": str(exc)})
            return {"id": request_id, "ok": False,
                    "error": error.as_dict()}
        except Exception as exc:
            # DuckDB raises duckdb.OutOfMemoryException (not MemoryError)
            # when its internal memory_limit is exceeded. Catch it here so a
            # single oversized query never kills the whole sidecar process.
            name = type(exc).__name__.lower()
            if "outofmemory" in name or "memory" in str(exc).lower():
                event("error", "sidecar", operation,
                      duration_ms=round((time.perf_counter() - started) * 1000, 2),
                      error_code="memory_error")
                error = SidecarError(
                    "No hay memoria suficiente para esta operación.",
                    details={"reason": str(exc)})
                return {"id": request_id, "ok": False,
                        "error": error.as_dict()}
            raise

    def _dispatch(self, operation: str, params: dict[str, Any]) -> Any:
        operations: dict[str, Callable[..., Any]] = {
            "health": lambda: {"status": "ok", "protocol": 1},
            "load_dataset": self.application.load_dataset,
            "close_dataset": self.application.close_dataset,
            "list_datasets": self.application.list_datasets,
            "get_dataset_metadata": self.application.get_dataset_metadata,
            "get_columns": self.application.get_columns,
            "get_column_profile": self.application.get_column_profile,
            "get_column_values": self.application.get_column_values,
            "get_diagnostics": self.application.get_diagnostics,
            "apply_filters": self.application.apply_filters,
            "reset_filters": self.application.reset_filters,
            "get_date_columns": self.application.get_date_columns,
            "get_date_range": self.application.get_date_range,
            "apply_date_range": self.application.apply_date_range,
            "reset_date_range": self.application.reset_date_range,
            "get_table_preview": self.application.get_table_preview,
            "get_table_page": self.application.get_table_page,
            "preview_table": self.application.preview_table,
            "build_table": self.application.build_table,
            "list_analyses": self.application.list_analyses,
            "get_analysis_manifest": self.application.get_analysis_manifest,
            "run_analysis": self.application.run_analysis,
            "cancel_job": self.application.cancel_job,
            "get_job_status": self.application.get_job_status,
            "get_analysis_result": self.application.get_analysis_result,
            "get_result_chart": self.application.get_result_chart,
            "get_result_table": self.application.get_result_table,
            "export_result": self.application.export_result,
            "export_dataset": self.application.export_dataset,
            "merge_datasets": self.application.merge_datasets,
            "get_cache_info": self.application.get_cache_info,
            "clear_cache": self.application.clear_cache,
            "free_memory": self.application.free_memory,
        }
        target = operations.get(operation)
        if target is None:
            raise SidecarError("Operación no soportada.",
                               details={"operation": operation})
        return target(**params)

    def run(self, input_stream=None, output_stream=None) -> int:
        if input_stream is None and output_stream is None:
            # stdout is the JSON IPC channel. The scientific engine
            # (mmm_app) writes progress with print(), so route every engine
            # write to stderr and keep the real stdout exclusively for
            # protocol responses. This also avoids cp1252 UnicodeEncodeError
            # when stdout is a pipe.
            real_stdout = sys.stdout
            _redirect_engine_output()
            incoming, outgoing = sys.stdin, real_stdout
        else:
            incoming = input_stream or sys.stdin
            outgoing = output_stream or sys.stdout
        write_lock = threading.Lock()
        active = {}
        active_lock = threading.Lock()
        # Independent bounded lanes reserve room for interactive requests even
        # when exports saturate the heavy queue. Mutations stay serialized.
        capacities = {"interactive": threading.BoundedSemaphore(24),
                      "heavy": threading.BoundedSemaphore(8)}
        executors = {lane: ThreadPoolExecutor(max_workers=1,
                     thread_name_prefix=f"ipc-{lane}") for lane in capacities}

        def error(request_id, code, message, details=None):
            return {"id": request_id, "ok": False, "error": {
                "code": code, "message": message, "details": details or {}}}

        def cancelled(request_id):
            return error(request_id, "cancelled", "Solicitud cancelada.")

        def send(response):
            try:
                encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError, OverflowError) as exc:
                encoded = json.dumps(error(response.get("id"), "internal_error",
                                           "No se pudo serializar la respuesta.",
                                           {"type": type(exc).__name__}))
            with write_lock:
                outgoing.write(encoded + "\n")
                outgoing.flush()

        def execute(request, marker, queued_at, lane):
            request_id = request["id"]
            execution_at = time.perf_counter()
            status = "error"
            try:
                with engine.cancellation_scope(marker):
                    response = self.handle(request)
                    marker.check()
                status = "ok" if response["ok"] else "error"
                return response
            except Exception as exc:
                if marker.is_set() or isinstance(exc, TaskCancelled):
                    status = "cancelled"
                    return cancelled(request_id)
                event("error", "sidecar", "request_failed", request_id=request_id,
                      error_type=type(exc).__name__)
                return error(request_id, "internal_error", "La operación falló.",
                             {"type": type(exc).__name__})
            finally:
                event("info", "sidecar", "request_finished", request_id=request_id,
                      request_operation=request.get("operation"), priority=lane,
                      queue_ms=round((execution_at - queued_at) * 1000, 2),
                      execution_ms=round((time.perf_counter() - execution_at) * 1000, 2),
                      total_ms=round((time.perf_counter() - queued_at) * 1000, 2),
                      status=status, cancel_requested=marker.is_set())

        def completed(future, request_id, key, marker, lane):
            try:
                marker.finish()
                response = cancelled(request_id) if future.cancelled() else future.result()
            except Exception as exc:
                # Telemetry or another failure outside execute's try block
                # must still settle the caller. Do not log from this fallback.
                response = error(request_id, "internal_error", "La operación falló.",
                                 {"type": type(exc).__name__})
            finally:
                with active_lock:
                    active.pop(key, None)
                capacities[lane].release()
            send(response)

        try:
            for line in incoming:
                if not line.strip():
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError as exc:
                    send(error(None, "sidecar_error", "JSON inválido.",
                               {"reason": str(exc)}))
                    continue
                if not isinstance(request, dict):
                    send(error(None, "sidecar_error", "La solicitud debe ser un objeto."))
                    continue
                request_id = request.get("id")
                if (not isinstance(request_id, (str, int)) or
                        isinstance(request_id, bool) or request_id == ""):
                    send(error(None, "sidecar_error", "ID de solicitud inválido."))
                    continue
                if request.get("token") != self.token:
                    send(error(request_id, "sidecar_error", "Sesión no autorizada."))
                    continue
                operation = request.get("operation")
                if operation is not None and not isinstance(operation, str):
                    send(error(request_id, "sidecar_error", "Operación inválida."))
                    continue
                if request.get("type") == "cancel":
                    with active_lock:
                        target = active.get(str(request.get("target_id")))
                    interruptible = False
                    if target is not None:
                        marker, future = target
                        interruptible = marker.cancel()
                        future.cancel()
                    send({"id": request_id, "ok": True, "result": {
                        "cancel_requested": target is not None,
                        "running_work_interruptible": interruptible,
                        "fallback": "cooperative_or_wait_for_completion",
                        "rollback_guaranteed": False}})
                    continue
                if request.get("type", "request") != "request":
                    send(error(request_id, "sidecar_error", "Tipo de mensaje inválido."))
                    continue
                if not isinstance(request.get("params", {}), dict):
                    send(error(request_id, "sidecar_error", "params debe ser un objeto."))
                    continue
                if request.get("priority") not in (None, "control", "interactive", "heavy"):
                    send(error(request_id, "sidecar_error", "Prioridad inválida."))
                    continue
                if operation in {"health", "cancel_job", "get_job_status"}:
                    try:
                        send(self.handle(request))
                    except Exception as exc:
                        send(error(request_id, "internal_error", "La operación falló.",
                                   {"type": type(exc).__name__}))
                    continue
                if operation == "shutdown":
                    with active_lock:
                        pending = list(active.values())
                    for marker, future in pending:
                        marker.cancel()
                        future.cancel()
                    send({"id": request_id, "ok": True, "result": {"shutting_down": True}})
                    break
                key = str(request_id)
                with active_lock:
                    duplicate = key in active
                if duplicate:
                    send(error(request_id, "duplicate_id", "ID de solicitud ya activo."))
                    continue
                lane = _request_lane(operation, request.get("priority"))
                if not capacities[lane].acquire(blocking=False):
                    send(error(request_id, "busy", "Cola de trabajo llena.", {"priority": lane}))
                    continue
                marker = engine.QueryCancellation()
                try:
                    future = executors[lane].submit(execute, request, marker,
                                                    time.perf_counter(), lane)
                except RuntimeError as exc:
                    marker.finish()
                    capacities[lane].release()
                    send(error(request_id, "internal_error", "Worker no disponible.",
                               {"type": type(exc).__name__}))
                    continue
                with active_lock:
                    active[key] = (marker, future)
                future.add_done_callback(lambda done, rid=request_id, key=key,
                                         marker=marker, lane=lane:
                                         completed(done, rid, key, marker, lane))
        finally:
            # EOF drains accepted work. Explicit shutdown above first cancels
            # work. Closing each worker's own connection avoids native leaks.
            try:
                for executor in executors.values():
                    try:
                        executor.submit(engine.reset_conn)
                    except RuntimeError:
                        # A broken/stopped pool cannot accept finalizers;
                        # still join every pool and shut down the app below.
                        pass
            finally:
                try:
                    for executor in executors.values():
                        executor.shutdown(wait=True)
                finally:
                    self.application.shutdown()
        return 0


_INTERACTIVE = frozenset({
    "list_datasets", "get_dataset_metadata", "get_columns", "get_column_profile",
    "get_column_values", "get_date_columns", "get_date_range", "get_table_preview",
    "get_table_page", "preview_table", "list_analyses", "get_analysis_manifest",
    "get_analysis_result", "get_result_chart", "get_result_table", "get_cache_info",
})
_MUTATIONS = frozenset({
    "load_dataset", "close_dataset", "apply_filters", "reset_filters",
    "apply_date_range", "reset_date_range", "build_table", "run_analysis",
    "export_result", "export_dataset", "merge_datasets", "clear_cache", "free_memory",
})


def _request_lane(operation, priority):
    # A caller cannot move a mutation out of the serialized heavy lane or
    # promote expensive work onto the stdin/control thread.
    if operation in _MUTATIONS:
        return "heavy"
    if priority in {"interactive", "heavy"}:
        return priority
    return "interactive" if operation in _INTERACTIVE else "heavy"


def _redirect_engine_output() -> None:
    """Send every engine write to stderr, keeping stdout for JSON only.

    The scientific engine (``mmm_app``) reports progress with ``print``.
    Because the sidecar protocol owns stdout, those writes must never reach
    it. Reconfiguring stdout to UTF-8 also prevents ``UnicodeEncodeError``
    when the process is launched with a legacy code page (cp1252).
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.stdout = sys.stderr


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default=os.environ.get("MEDICION_SESSION_TOKEN"))
    args = parser.parse_args(argv)
    if not args.token:
        print("Falta MEDICION_SESSION_TOKEN", file=sys.stderr)
        return 2
    return SidecarServer(args.token).run()


if __name__ == "__main__":
    raise SystemExit(main())
