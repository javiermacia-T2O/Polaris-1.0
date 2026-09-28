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

    def _dispatch(self, operation: str, params: dict[str, Any]) -> Any:
        operations: dict[str, Callable[..., Any]] = {
            "health": lambda: {"status": "ok", "protocol": 1},
            "load_dataset": self.application.load_dataset,
            "close_dataset": self.application.close_dataset,
            "list_datasets": self.application.list_datasets,
            "get_dataset_metadata": self.application.get_dataset_metadata,
            "get_columns": self.application.get_columns,
            "get_column_profile": self.application.get_column_profile,
            "get_diagnostics": self.application.get_diagnostics,
            "apply_filters": self.application.apply_filters,
            "reset_filters": self.application.reset_filters,
            "get_table_preview": self.application.get_table_preview,
            "get_table_page": self.application.get_table_page,
            "build_table": self.application.build_table,
            "list_analyses": self.application.list_analyses,
            "get_analysis_manifest": self.application.get_analysis_manifest,
            "run_analysis": self.application.run_analysis,
            "cancel_job": self.application.cancel_job,
            "get_job_status": self.application.get_job_status,
            "get_analysis_result": self.application.get_analysis_result,
            "get_result_table": self.application.get_result_table,
            "export_result": self.application.export_result,
        }
        target = operations.get(operation)
        if target is None:
            raise SidecarError("Operación no soportada.",
                               details={"operation": operation})
        return target(**params)

    def run(self, input_stream=None, output_stream=None) -> int:
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
