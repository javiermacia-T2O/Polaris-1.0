"""Cancellation interrupts real DuckDB work without poisoning the next request."""
import threading

import pytest

from core import engine
from medicion_core.jobs import JobManager
from medicion_core.schemas import JobState
from medicion_core.sidecar import SidecarServer
from test_sidecar_multiplex import App, Input, Output

TOKEN = "x" * 32
LONG_QUERY = "SELECT sum(sin(i)) FROM range(1000000000) t(i)"


def test_running_duckdb_request_is_interrupted_and_connection_reused():
    pytest.importorskip("duckdb")
    incoming, outgoing = Input(), Output()
    server = SidecarServer(TOKEN, App())
    started = threading.Event()
    active_marker = []

    def dispatch(operation, params):
        conn = engine.get_conn()
        if operation == "slow":
            active_marker.append(engine.current_cancellation())
            started.set()
            return conn.execute(LONG_QUERY).fetchone()[0]
        return conn.execute("SELECT 42").fetchone()[0]

    server._dispatch = dispatch
    thread = threading.Thread(target=server.run, args=(incoming, outgoing))
    thread.start()
    try:
        incoming.lines.put({"id": "slow", "token": TOKEN, "operation": "slow"})
        assert started.wait(3)
        incoming.lines.put({"id": "other", "token": TOKEN,
                            "operation": "fast", "priority": "interactive"})
        incoming.lines.put({"id": "cancel", "token": TOKEN,
                            "type": "cancel", "target_id": "slow"})
        replies = {item["id"]: item for item in
                   (outgoing.lines.get(timeout=3) for _ in range(3))}
        assert replies["cancel"]["result"]["running_work_interruptible"]
        assert replies["slow"]["error"]["code"] == "cancelled"
        assert replies["other"]["result"] == 42
        incoming.lines.put({"id": "next", "token": TOKEN, "operation": "fast"})
        assert outgoing.lines.get(timeout=3)["result"] == 42
    finally:
        for marker in active_marker:
            marker.cancel()
        incoming.lines.put(None)
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_shutdown_interrupts_running_duckdb_job_and_marks_queued_cancelled():
    pytest.importorskip("duckdb")
    manager = JobManager(max_workers=1)
    started = threading.Event()

    def work(cancel, progress):
        conn = engine.get_conn()
        started.set()
        conn.execute(LONG_QUERY).fetchone()
        return None

    running = manager.submit("slow", work)
    assert started.wait(3)
    queued = manager.submit("queued", lambda cancel, progress: "unexpected")
    shutdown = threading.Thread(target=manager.shutdown)
    shutdown.start()
    shutdown.join(timeout=5)
    assert not shutdown.is_alive()
    assert manager.status(running).state == JobState.CANCELLED
    assert manager.status(queued).state == JobState.CANCELLED


def test_cancelled_scope_does_not_close_or_interrupt_a_later_scope():
    token = engine.QueryCancellation()
    with engine.cancellation_scope(token):
        conn = engine.get_conn()
        assert conn.execute("SELECT 1").fetchone() == (1,)
    assert token.cancel() is False
    with engine.cancellation_scope(engine.QueryCancellation()):
        assert engine.get_conn().execute("SELECT 2").fetchone() == (2,)
    engine.reset_conn()
