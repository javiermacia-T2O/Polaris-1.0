"""Structured rotating logs for the application boundary."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
from typing import Any

from core.diagnostics import memory_snapshot
from core.runtime_paths import writable_root

_logger: logging.Logger | None = None
_lock = threading.Lock()


def log_path() -> Path:
    return writable_root() / "logs" / "sidecar.jsonl"


def event(level: str, component: str, operation: str, *,
          duration_ms: float | None = None, job_id: str | None = None,
          dataset_id: str | None = None, error_code: str | None = None,
          **fields: Any) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level.upper(), "component": component,
        "operation": operation, "job_id": job_id,
        "dataset_id": dataset_id, "duration_ms": duration_ms,
        "error_code": error_code, "memory": memory_snapshot(),
        **fields,
    }
    logger = _get_logger()
    logger.log(getattr(logging, level.upper(), logging.INFO),
               json.dumps(record, ensure_ascii=False, default=str))


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    with _lock:
        if _logger is not None:
            return _logger
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("medicion.sidecar.structured")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024,
                                      backupCount=4, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        _logger = logger
        return logger
