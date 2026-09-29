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
        try:
            for line in incoming:
                if not line.strip():
                    continue
                try:
                    request = json.loads(line)
                    response = self.handle(request)
                except json.JSONDecodeError as exc:
                    response = {
                        "id": None, "ok": False,
                        "error": SidecarError(
                            "JSON inválido.",
                            details={"reason": str(exc)}).as_dict(),
                    }
                outgoing.write(json.dumps(response, ensure_ascii=False,
                                          allow_nan=False) + "\n")
                outgoing.flush()
        finally:
            self.application.shutdown()
        return 0


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
