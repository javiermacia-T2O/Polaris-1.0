"""Authenticated JSON-lines sidecar over stdin/stdout.

The transport never opens a network port. Each request is one JSON object and
each response is one JSON object, making Rust responsible only for lifecycle
and forwarding small typed messages.
"""

from __future__ import annotations

import argparse
import inspect
import itertools
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from core.tasks import TaskCancelled

from .errors import AppError, SidecarError
from .observability import event

# Operations that must never wait behind heavy work. They run inline in the
# reader thread so ``health``/``cancel`` answer even while a multi-minute
# analysis or export is running on the worker pools.
_CONTROL_OPERATIONS = frozenset({
    "health", "cancel_request", "shutdown", "get_job_status", "cancel_job",
    "list_jobs",
})

_PRIORITY_NAMES = {"control": 0, "interactive": 1, "background": 2}

# Operation name -> ``MedicionApplication`` method name. Kept as strings so
# building the dispatch table never touches the application instance (and
# therefore never imports pandas) until a real data operation arrives.
_APPLICATION_OPERATIONS = {
    "load_dataset": "load_dataset",
    "close_dataset": "close_dataset",
    "list_datasets": "list_datasets",
    "get_dataset_metadata": "get_dataset_metadata",
    "get_columns": "get_columns",
    "get_column_profile": "get_column_profile",
    "get_column_values": "get_column_values",
    "get_diagnostics": "get_diagnostics",
    "apply_filters": "apply_filters",
    "reset_filters": "reset_filters",
    "get_date_columns": "get_date_columns",
    "get_date_range": "get_date_range",
    "apply_date_range": "apply_date_range",
    "reset_date_range": "reset_date_range",
    "get_table_preview": "get_table_preview",
    "get_table_page": "get_table_page",
    "preview_table": "preview_table",
    "build_table": "build_table",
    "list_analyses": "list_analyses",
    "get_analysis_manifest": "get_analysis_manifest",
    "run_analysis": "run_analysis",
    "cancel_job": "cancel_job",
    "get_job_status": "get_job_status",
    "get_analysis_result": "get_analysis_result",
    "get_result_chart": "get_result_chart",
    "get_result_table": "get_result_table",
    "export_result": "export_result",
    "export_dataset": "export_dataset",
    "merge_datasets": "merge_datasets",
    "get_cache_info": "get_cache_info",
    "clear_cache": "clear_cache",
    "free_memory": "free_memory",
}


def _priority_of(request: dict[str, Any]) -> int:
    raw = request.get("priority")
    if isinstance(raw, bool):
        return 1
    if isinstance(raw, int):
        return max(0, min(2, raw))
    if isinstance(raw, str):
        return _PRIORITY_NAMES.get(raw.strip().lower(), 1)
    return 1


def _progress_parts(value: Any) -> tuple[int, str]:
    if isinstance(value, (tuple, list)) and len(value) == 2:
        percent, message = value
    elif isinstance(value, dict):
        percent = value.get("percent", 0)
        message = value.get("message", "")
    else:
        percent, message = value, ""
    try:
        percent = int(percent)
    except (TypeError, ValueError):
        percent = 0
    return max(0, min(100, percent)), str(message or "")


class _PendingRequest:
    """Cancellation handle for one in-flight request."""

    __slots__ = ("cancel", "conn", "thread_ident", "lock")

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.conn: Any = None
        self.thread_ident: int | None = None
        self.lock = threading.Lock()


class _RequestRegistry:
    """Maps request ids to their cancellation handles."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, _PendingRequest] = {}

    def register(self, request_id: str) -> _PendingRequest:
        item = _PendingRequest()
        with self._lock:
            self._items[request_id] = item
        return item

    def attach_conn(self, request_id: str, conn: Any) -> None:
        with self._lock:
            item = self._items.get(request_id)
        if item is not None:
            with item.lock:
                item.conn = conn
                item.thread_ident = threading.get_ident()

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            item = self._items.get(request_id)
        if item is None:
            return False
        item.cancel.set()
        with item.lock:
            conn = item.conn
            ident = item.thread_ident
        # Resolve the connection by thread identity first: if the connection
        # was recreated, the binding follows the new one without re-registering.
        interrupted = False
        if ident is not None:
            try:
                from core import engine as db_engine

                interrupted = db_engine.interrupt_thread(ident)
            except Exception:
                interrupted = False
        if not interrupted and conn is not None:
            # DuckDB interrupts the query currently running on this
            # connection, so a long scan aborts instead of finishing.
            try:
                conn.interrupt()
            except Exception:
                pass
        return True

    def discard(self, request_id: str) -> None:
        with self._lock:
            self._items.pop(request_id, None)


class _Writer:
    """Serialises every stdout write behind one lock."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, allow_nan=False)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()


class SidecarServer:
    def __init__(self, token: str, application: Any | None = None,
                 *, interactive_workers: int = 2,
                 background_workers: int = 1):
        if len(token) < 24:
            raise SidecarError("El token de sesión del sidecar no es válido.")
        self.token = token
        # ``application`` is built on first use, not here: the scientific
        # stack (pandas, DuckDB, the analysis services) must not load before
        # the engine can answer ``health``.
        self._application = application
        self._application_lock = threading.Lock()
        self.interactive_workers = max(1, int(interactive_workers))
        self.background_workers = max(1, int(background_workers))
        self._signatures: dict[Callable[..., Any], frozenset[str]] = {}
        self._registry = _RequestRegistry()

    @property
    def application(self) -> Any:
        """Return the application, constructing it on first access."""
        instance = self._application
        if instance is None:
            with self._application_lock:
                instance = self._application
                if instance is None:
                    from .application import MedicionApplication

                    instance = MedicionApplication()
                    self._application = instance
        return instance

    def _cancel_request(self, target_id: str) -> dict[str, Any]:
        """Cancel an in-flight request by id (control operation)."""
        return {"cancelled": self._registry.cancel(str(target_id))}

    def _accepts(self, target: Callable[..., Any], name: str) -> bool:
        accepted = self._signatures.get(target)
        if accepted is None:
            try:
                parameters = inspect.signature(target).parameters
            except (TypeError, ValueError):
                parameters = {}
            accepted = frozenset(parameters)
            self._signatures[target] = accepted
        return name in accepted

    def handle(self, request: dict[str, Any], *,
               cancel: threading.Event | None = None,
               progress: Callable[[Any], None] | None = None,
               request_id: str | None = None) -> dict[str, Any]:
        request_id = request_id if request_id is not None else request.get("id")
        operation = str(request.get("operation") or "")
        started = time.perf_counter()
        try:
            if request.get("token") != self.token:
                raise SidecarError("Sesión no autorizada.")
            params = request.get("params") or {}
            result = self._dispatch(operation, params, cancel=cancel,
                                    progress=progress, request_id=request_id)
            event("info", "sidecar", operation,
                  duration_ms=round((time.perf_counter() - started) * 1000, 2),
                  dataset_id=params.get("dataset_id"))
            return {"id": request_id, "type": "response", "ok": True,
                    "result": _jsonable(result)}
        except TaskCancelled:
            event("info", "sidecar", operation, error_code="cancelled",
                  duration_ms=round((time.perf_counter() - started) * 1000, 2))
            error = SidecarError("La operación se canceló.",
                                 details={"reason": "cancelled"})
            error.code = "CANCELLED"
            return {"id": request_id, "type": "response", "ok": False,
                    "error": error.as_dict()}
        except AppError as exc:
            event("error", "sidecar", operation,
                  duration_ms=round((time.perf_counter() - started) * 1000, 2),
                  error_code=exc.code)
            return {"id": request_id, "type": "response", "ok": False,
                    "error": exc.as_dict()}
        except (KeyError, TypeError, ValueError) as exc:
            error = SidecarError("Solicitud inválida.",
                                 details={"reason": str(exc)})
            return {"id": request_id, "type": "response", "ok": False,
                    "error": error.as_dict()}
        except MemoryError as exc:
            event("error", "sidecar", operation,
                  duration_ms=round((time.perf_counter() - started) * 1000, 2),
                  error_code="memory_error")
            error = SidecarError(
                "No hay memoria suficiente para esta operación.",
                details={"reason": str(exc)})
            return {"id": request_id, "type": "response", "ok": False,
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
                return {"id": request_id, "type": "response", "ok": False,
                        "error": error.as_dict()}
            raise

    def _dispatch(self, operation: str, params: dict[str, Any], *,
                  cancel: threading.Event | None = None,
                  progress: Callable[[Any], None] | None = None,
                  request_id: str | None = None) -> Any:
        if operation == "health":
            return {"status": "ok", "protocol": 1}
        if operation == "cancel_request":
            return self._cancel_request(params.get("target_id", ""))
        method = _APPLICATION_OPERATIONS.get(operation)
        if method is None:
            raise SidecarError("Operación no soportada.",
                               details={"operation": operation})
        # Resolving the bound method here is what triggers the lazy import of
        # the scientific stack, so ``health``/``cancel`` never pay for it.
        target = getattr(self.application, method)
        if cancel is not None and self._accepts(target, "cancel"):
            params = {**params, "cancel": cancel}
        if progress is not None and self._accepts(target, "progress"):
            params = {**params, "progress": progress}
        if request_id is not None and self._accepts(target, "request_id"):
            params = {**params, "request_id": request_id}
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
        writer = _Writer(outgoing)
        registry = self._registry
        counter = itertools.count(1)
        interactive = ThreadPoolExecutor(
            max_workers=self.interactive_workers,
            thread_name_prefix="medicion-ipc")
        background = ThreadPoolExecutor(
            max_workers=self.background_workers,
            thread_name_prefix="medicion-bg")
        stop = threading.Event()
        try:
            for line in incoming:
                if not line.strip():
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError as exc:
                    writer.send({
                        "id": None, "type": "response", "ok": False,
                        "error": SidecarError(
                            "JSON inválido.",
                            details={"reason": str(exc)}).as_dict(),
                    })
                    continue
                if not isinstance(request, dict):
                    continue
                kind = str(request.get("type") or "request")
                if kind == "cancel":
                    target_id = str(request.get("target_id") or "")
                    found = registry.cancel(target_id)
                    writer.send({"id": request.get("id"), "type": "cancel_ack",
                                 "ok": True, "result": {"cancelled": found}})
                    continue
                if kind == "shutdown":
                    stop.set()
                    writer.send({"id": request.get("id"), "type": "response",
                                 "ok": True, "result": {"status": "stopping"}})
                    break
                if kind != "request":
                    continue
                request_id = str(request.get("id") or next(counter))
                operation = str(request.get("operation") or "")
                if operation in _CONTROL_OPERATIONS:
                    # Control operations run inline so they never queue behind
                    # a heavy analysis or export.
                    self._run_one(request, request_id, writer, registry)
                    continue
                priority = _priority_of(request)
                pool = background if priority >= 2 else interactive
                pool.submit(self._run_one, request, request_id, writer,
                            registry, priority)
        finally:
            stop.set()
            interactive.shutdown(wait=False, cancel_futures=True)
            background.shutdown(wait=False, cancel_futures=True)
            # Only shut down an application that was actually built; a
            # health-only session must not import the scientific stack just to
            # tear it down.
            if self._application is not None:
                self._application.shutdown()
        return 0

    def _run_one(self, request: dict[str, Any], request_id: str,
                 writer: _Writer, registry: _RequestRegistry,
                 priority: int = 1) -> None:
        queued_at = time.perf_counter()
        pending = registry.register(request_id)
        # Bind the worker thread's DuckDB connection so a cancel can call
        # ``interrupt()`` and abort the query actually running here. Control
        # operations (health/cancel/job status) never touch the data pool:
        # they must answer even when every connection is busy with a heavy
        # query, so they never wait on a reservation.
        operation = str(request.get("operation") or "")
        def progress(value: Any) -> None:
            percent, message = _progress_parts(value)
            writer.send({"id": request_id, "type": "progress",
                         "percent": percent, "message": message})

        started = time.perf_counter()
        try:
            if operation in _CONTROL_OPERATIONS:
                response = self.handle(request, cancel=pending.cancel,
                                       progress=progress, request_id=request_id)
            else:
                from core import engine as db_engine
                from core.resource_manager import HEAVY_OPERATIONS, get_manager
                params = request.get("params") or {}
                heavy = operation in HEAVY_OPERATIONS or (
                    operation == "get_table_page" and bool(params.get("sort")))
                with get_manager().reserve(
                        "heavy" if heavy else "light", cancel=pending.cancel):
                    conn = db_engine.get_conn(cancel=pending.cancel)
                    registry.attach_conn(request_id, conn)
                    response = self.handle(request, cancel=pending.cancel,
                                           progress=progress, request_id=request_id)
        except Exception as exc:  # pragma: no cover - defensive
            response = {"id": request_id, "type": "response", "ok": False,
                        "error": SidecarError(
                            "La operación falló de forma inesperada.",
                            details={"type": type(exc).__name__,
                                     "reason": str(exc)}).as_dict()}
        finally:
            registry.discard(request_id)
            # Return the pooled DuckDB connection so the next request on this
            # worker thread starts from a clean, released lease.
            try:
                from core import engine as db_engine

                db_engine.release_conn()
            except Exception:
                pass
        finished = time.perf_counter()
        # Per-request observability: never logs dataset contents, only timing
        # and outcome so a slow or cancelled request can be diagnosed.
        error = response.get("error") if not response.get("ok") else None
        event("info", "sidecar.request", str(request.get("operation") or ""),
              duration_ms=round((finished - started) * 1000, 2),
              request_id=request_id,
              priority=priority,
              queue_ms=round((started - queued_at) * 1000, 2),
              execution_ms=round((finished - started) * 1000, 2),
              total_ms=round((finished - queued_at) * 1000, 2),
              status="ok" if response.get("ok") else "error",
              cancelled=bool(error and error.get("code") == "CANCELLED"),
              error_code=(error or {}).get("code"))
        writer.send(response)


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
    # Duck-typed instead of importing pydantic at module scope: a health-only
    # startup must not pay for the validation library.
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
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
