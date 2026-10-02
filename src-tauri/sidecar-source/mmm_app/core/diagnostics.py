"""Bounded diagnostic log for recoverable desktop task failures."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.runtime_paths import writable_root


def debug_enabled() -> bool:
    return os.environ.get("MMM_DEBUG", "").strip().lower() in {
        "1", "true", "yes", "on"}


def memory_snapshot() -> dict[str, float]:
    """GiB units; psutil remains optional at runtime."""
    try:
        import psutil
        return {
            "rss_gib": round(psutil.Process().memory_info().rss / 1024**3, 3),
            "available_gib": round(psutil.virtual_memory().available / 1024**3, 3),
        }
    except ImportError:
        from core.memory_budget import system_memory
        return {"available_gib": round(system_memory().available / 1024**3, 3)}


def log_debug(category: str, message: str, **fields) -> None:
    if not debug_enabled():
        return
    details = " ".join(f"{key}={str(value)[:500]}" for key, value in fields.items())
    logger = configure_error_log(writable_root() / "logs" / "app.log")
    logger.debug("[%s] %s%s", category, message,
                 f" {details}" if details else "")


def configure_error_log(path: Path) -> logging.Logger:
    logger = logging.getLogger("mmm_app.tasks")
    logger.setLevel(logging.DEBUG if debug_enabled() else logging.ERROR)
    if logger.handlers:
        return logger
    logger.propagate = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=2 * 1024 * 1024, backupCount=2,
            encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())
    return logger


def log_task_error(label: str, exc: Exception) -> None:
    path = writable_root() / "logs" / "app.log"
    configure_error_log(path).error(
        "Task %s failed: %s", label, exc,
        exc_info=(type(exc), exc, exc.__traceback__))
