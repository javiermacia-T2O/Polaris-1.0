"""Concurrency, multiplexing and cancellation tests for the sidecar.

These tests exercise the JSON-lines server directly (no Rust, no Tauri) so
they run fast and deterministically. They cover the FASE 1 guarantees:

* a long operation never blocks ``health``/control operations;
* a running operation can be cancelled and answers quickly;
* responses are matched to the request that produced them, even when they
  finish out of order;
* the pending-request registry is always cleaned up (no hung requests).
"""

from __future__ import annotations

import json
import queue
import threading
import time

import pytest

from medicion_core import MedicionApplication
from medicion_core.sidecar import SidecarServer

TOKEN = "a" * 32


class _Pipe:
    """Blocking, thread-safe stand-in for the stdin/stdout JSON channel."""

    def __init__(self) -> None:
        self._in: queue.Queue[str | None] = queue.Queue()
        self._out: queue.Queue[dict] = queue.Queue()

    def feed(self, message: dict) -> None:
        self._in.put(json.dumps(message) + "\n")

    def close(self) -> None:
        self._in.put(None)

    def readline(self) -> str:
        item = self._in.get()
        return "" if item is None else item

    def __iter__(self) -> "_Pipe":
        return self

    def __next__(self) -> str:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def write(self, text: str) -> None:
        for line in text.splitlines():
            if line.strip():
                self._out.put(json.loads(line))

    def flush(self) -> None:
        pass

    def next_message(self, timeout: float = 5.0) -> dict:
        return self._out.get(timeout=timeout)

    def collect_until(self, predicate, timeout: float = 5.0) -> list[dict]:
        deadline = time.perf_counter() + timeout
        collected: list[dict] = []
        while time.perf_counter() < deadline:
            try:
                message = self._out.get(timeout=max(0.01, deadline - time.perf_counter()))
            except queue.Empty:
                break
            collected.append(message)
            if predicate(collected):
                return collected
        return collected


def _start(server: SidecarServer) -> tuple[_Pipe, threading.Thread]:
    pipe = _Pipe()
    thread = threading.Thread(target=server.run, args=(pipe, pipe), daemon=True)
    thread.start()
    return pipe, thread


def _request(request_id: str, operation: str, params: dict | None = None,
             priority: str | None = None) -> dict:
    message = {"id": request_id, "type": "request", "token": TOKEN,
               "operation": operation, "params": params or {}}
    if priority is not None:
        message["priority"] = priority
    return message


def _empty_page(dataset_id, offset=0, limit=100, cancel=None):
    return {"rows": [], "total_rows": 0, "offset": offset, "limit": limit}


def test_health_answers_while_a_long_operation_runs():
    app = MedicionApplication(max_workers=1)
    release = threading.Event()

    def slow(dataset_id, offset=0, limit=100, cancel=None):
        release.wait(10)
        return _empty_page(dataset_id, offset, limit)

    app.get_table_preview = slow
    server = SidecarServer(TOKEN, app)
    pipe, thread = _start(server)
    try:
        pipe.feed(_request("slow", "get_table_preview", {"dataset_id": "d"}))
        time.sleep(0.3)  # let the slow request occupy a worker
        started = time.perf_counter()
        pipe.feed(_request("h1", "health"))
        message = pipe.next_message(timeout=3)
        elapsed = time.perf_counter() - started
        assert message["id"] == "h1"
        assert message["ok"] is True
        assert message["result"] == {"status": "ok", "protocol": 1}
        # Control operations must not wait for the slow worker.
        assert elapsed < 1.0
    finally:
        release.set()
        pipe.close()
        thread.join(timeout=5)


def test_cancel_interrupts_a_running_operation_quickly():
    app = MedicionApplication(max_workers=1)

    def slow(dataset_id, offset=0, limit=100, cancel=None):
        from core.tasks import TaskCancelled

        while cancel is not None and not cancel.wait(0.01):
            pass
        raise TaskCancelled("cancelled by test")

    app.get_table_preview = slow
    server = SidecarServer(TOKEN, app)
    pipe, thread = _start(server)
    try:
        pipe.feed(_request("slow", "get_table_preview", {"dataset_id": "d"}))
        time.sleep(0.3)
        started = time.perf_counter()
        pipe.feed({"id": "c1", "type": "cancel", "token": TOKEN,
                   "target_id": "slow"})
        messages = pipe.collect_until(
            lambda items: any(m.get("id") == "slow" for m in items), timeout=3)
        elapsed = time.perf_counter() - started
        ack = next(m for m in messages if m.get("type") == "cancel_ack")
        response = next(m for m in messages if m.get("id") == "slow")
        assert ack["result"]["cancelled"] is True
        assert response["ok"] is False
        assert response["error"]["code"] == "CANCELLED"
        assert elapsed < 1.5
    finally:
        pipe.close()
        thread.join(timeout=5)


def test_cancel_of_unknown_request_is_reported_as_not_cancelled():
    app = MedicionApplication(max_workers=1)
    server = SidecarServer(TOKEN, app)
    pipe, thread = _start(server)
    try:
        pipe.feed({"id": "c1", "type": "cancel", "token": TOKEN,
                   "target_id": "does-not-exist"})
        message = pipe.next_message(timeout=3)
        assert message["type"] == "cancel_ack"
        assert message["result"]["cancelled"] is False
    finally:
        pipe.close()
        thread.join(timeout=5)


def test_responses_are_matched_to_their_request_even_out_of_order():
    app = MedicionApplication(max_workers=1)

    def op(dataset_id, offset=0, limit=100, cancel=None):
        time.sleep(0.6 if dataset_id == "slow" else 0.05)
        return _empty_page(dataset_id, offset, limit)

    app.get_table_preview = op
    server = SidecarServer(TOKEN, app)
    pipe, thread = _start(server)
    try:
        pipe.feed(_request("slow", "get_table_preview", {"dataset_id": "slow"}))
        pipe.feed(_request("fast", "get_table_preview", {"dataset_id": "fast"}))
        messages = pipe.collect_until(
            lambda items: {"slow", "fast"} <= {m.get("id") for m in items},
            timeout=5)
        order = [m["id"] for m in messages if m.get("type") == "response"]
        assert order == ["fast", "slow"]
        assert all(m["ok"] is True for m in messages)
    finally:
        pipe.close()
        thread.join(timeout=5)


def test_pending_registry_is_cleaned_up_after_every_request():
    app = MedicionApplication(max_workers=1)
    app.get_table_preview = _empty_page
    server = SidecarServer(TOKEN, app)
    pipe, thread = _start(server)
    try:
        for index in range(5):
            pipe.feed(_request(f"r{index}", "get_table_preview",
                               {"dataset_id": "d"}))
        messages = pipe.collect_until(
            lambda items: len([m for m in items
                               if m.get("type") == "response"]) >= 5, timeout=5)
        assert len([m for m in messages if m.get("type") == "response"]) == 5
        # No request may stay registered once its response was sent.
        assert server._registry._items == {}
    finally:
        pipe.close()
        thread.join(timeout=5)


def test_background_priority_does_not_starve_interactive_requests():
    app = MedicionApplication(max_workers=1)
    release = threading.Event()

    def op(dataset_id, offset=0, limit=100, cancel=None):
        if dataset_id == "bg":
            release.wait(10)
        return _empty_page(dataset_id, offset, limit)

    app.get_table_preview = op
    server = SidecarServer(TOKEN, app)
    pipe, thread = _start(server)
    try:
        pipe.feed(_request("bg", "get_table_preview", {"dataset_id": "bg"},
                           priority="background"))
        time.sleep(0.3)
        started = time.perf_counter()
        pipe.feed(_request("fg", "get_table_preview", {"dataset_id": "fg"},
                           priority="interactive"))
        message = pipe.next_message(timeout=3)
        elapsed = time.perf_counter() - started
        assert message["id"] == "fg"
        assert message["ok"] is True
        assert elapsed < 1.0
    finally:
        release.set()
        pipe.close()
        thread.join(timeout=5)


def test_request_timing_includes_time_waiting_for_a_worker(monkeypatch):
    from medicion_core import sidecar as sidecar_module

    app = MedicionApplication(max_workers=1)
    entered = threading.Event()
    release = threading.Event()
    events = []

    def slow(dataset_id, offset=0, limit=100, cancel=None):
        if dataset_id == "hold":
            entered.set()
            release.wait(5)
        return _empty_page(dataset_id, offset, limit)

    app.get_table_preview = slow
    monkeypatch.setattr(sidecar_module, "event", lambda *args, **kwargs:
                        events.append((args, kwargs)))
    server = SidecarServer(TOKEN, app, interactive_workers=1)
    pipe, thread = _start(server)
    try:
        pipe.feed(_request("hold", "get_table_preview", {"dataset_id": "hold"}))
        assert entered.wait(3)
        pipe.feed(_request("queued", "get_table_preview", {"dataset_id": "queued"}))
        time.sleep(0.25)
        release.set()
        responses = pipe.collect_until(
            lambda items: len([item for item in items
                               if item.get("type") == "response"]) == 2,
            timeout=5)
        assert len([item for item in responses
                    if item.get("type") == "response"]) == 2

        request_event = next(kwargs for args, kwargs in events
                             if args[:2] == ("info", "sidecar.request")
                             and kwargs.get("request_id") == "queued")
        assert request_event["queue_ms"] >= 150
        assert request_event["total_ms"] >= request_event["queue_ms"]
    finally:
        release.set()
        pipe.close()
        thread.join(timeout=5)


def test_unauthorized_request_is_rejected_without_touching_the_registry():
    app = MedicionApplication(max_workers=1)
    server = SidecarServer(TOKEN, app)
    pipe, thread = _start(server)
    try:
        pipe.feed({"id": "x", "type": "request", "token": "wrong",
                   "operation": "health", "params": {}})
        message = pipe.next_message(timeout=3)
        assert message["ok"] is False
        assert server._registry._items == {}
    finally:
        pipe.close()
        thread.join(timeout=5)


@pytest.mark.parametrize("priority", ["control", "interactive", "background"])
def test_priority_names_are_accepted(priority):
    app = MedicionApplication(max_workers=1)
    server = SidecarServer(TOKEN, app)
    pipe, thread = _start(server)
    try:
        pipe.feed(_request("h", "health", priority=priority))
        message = pipe.next_message(timeout=3)
        assert message["ok"] is True
    finally:
        pipe.close()
        thread.join(timeout=5)