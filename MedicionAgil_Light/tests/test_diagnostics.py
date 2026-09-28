import logging

from core import diagnostics
from core.diagnostics import configure_error_log


def test_error_log_is_bounded_and_writes_exception(tmp_path):
    logger = logging.getLogger("mmm_app.tasks")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    path = tmp_path / "logs" / "app.log"
    try:
        configured = configure_error_log(path)
        raise ValueError("test failure")
    except ValueError:
        configured.exception("Recoverable operation failed")
    assert "test failure" in path.read_text(encoding="utf-8")
    assert configured.handlers[0].maxBytes == 2 * 1024 * 1024
    for handler in configured.handlers[:]:
        configured.removeHandler(handler)
        handler.close()


def test_optional_debug_records_categories_without_logging_rows(
        tmp_path, monkeypatch):
    logger = logging.getLogger("mmm_app.tasks")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    monkeypatch.setenv("MMM_DEBUG", "1")
    monkeypatch.setattr(diagnostics, "writable_root", lambda: tmp_path)
    diagnostics.log_debug("DATA", "active dataset", rows=25, backend="parquet")
    diagnostics.log_debug("MATERIALIZE", "fetchdf bounded", rows=10)
    content = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "[DATA] active dataset rows=25 backend=parquet" in content
    assert "[MATERIALIZE] fetchdf bounded rows=10" in content
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
