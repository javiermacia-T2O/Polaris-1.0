"""Transport concurrency checks using the actual JSON-lines dispatcher."""
import json
import queue
import threading
import time

from medicion_core.sidecar import SidecarServer


class Input:
    def __init__(self):
        self.lines = queue.Queue()

    def __iter__(self):
        while (line := self.lines.get()) is not None:
            yield json.dumps(line) + "\n"


class Output:
    def __init__(self):
        self.lines = queue.Queue()

    def write(self, line):
        self.lines.put(json.loads(line))

    def flush(self):
        pass


class App:
    def shutdown(self):
        pass


def test_health_and_out_of_order_responses_during_slow_request():
    incoming, outgoing = Input(), Output()
    server = SidecarServer("x" * 32, App())
    started = threading.Event()
    release = threading.Event()

    def dispatch(operation, params):
        if operation == "slow":
            started.set()
            release.wait(3)
            return "slow"
        if operation == "fast":
            return "fast"
        if operation == "health":
            return {"status": "ok", "protocol": 1}
        raise AssertionError(operation)

    server._dispatch = dispatch
    thread = threading.Thread(target=server.run, args=(incoming, outgoing))
    thread.start()
    try:
        def request(id, operation):
            incoming.lines.put({"id": id, "token": "x" * 32,
                                "operation": operation, "params": {},
                                "priority": "interactive" if operation == "fast" else "heavy"})

        request("1", "slow")
        assert started.wait(2)
        before = time.monotonic()
        request("2", "health")
        request("3", "fast")
        responses = [outgoing.lines.get(timeout=2) for _ in range(2)]
        assert {item["id"] for item in responses} == {"2", "3"}
        assert time.monotonic() - before < 1.5
        incoming.lines.put({"id": "4", "token": "x" * 32,
                            "type": "cancel", "target_id": "1"})
        assert outgoing.lines.get(timeout=2)["result"]["cancel_requested"]
        release.set()
        assert outgoing.lines.get(timeout=2)["id"] == "1"
    finally:
        release.set()
        incoming.lines.put(None)
        thread.join(timeout=4)
        assert not thread.is_alive()


def test_saturated_heavy_lane_keeps_control_and_interactive_capacity():
    incoming, outgoing = Input(), Output()
    server = SidecarServer("x" * 32, App())
    started, release = threading.Event(), threading.Event()
    called = []

    def dispatch(operation, params):
        called.append(params.get("value"))
        if operation == "heavy":
            started.set()
            assert release.wait(5)
        return operation

    def request(request_id, operation="heavy", **extra):
        incoming.lines.put({"id": request_id, "token": "x" * 32,
                            "operation": operation,
                            "params": {"value": request_id}, **extra})

    server._dispatch = dispatch
    thread = threading.Thread(target=server.run, args=(incoming, outgoing))
    thread.start()
    try:
        request("running")
        assert started.wait(2)
        for index in range(7):
            request(f"queued-{index}")
        request("overflow")
        request("health", "health")
        request("interactive", "fast", priority="interactive")
        replies = {item["id"]: item for item in
                   (outgoing.lines.get(timeout=2) for _ in range(3))}
        assert replies["overflow"]["error"]["code"] == "busy"
        assert replies["health"]["ok"] and replies["interactive"]["ok"]
        incoming.lines.put({"id": "cancel", "token": "x" * 32,
                            "type": "cancel", "target_id": "queued-0"})
        replies = {item["id"]: item for item in
                   (outgoing.lines.get(timeout=2) for _ in range(2))}
        assert replies["cancel"]["result"]["cancel_requested"]
        assert replies["queued-0"]["error"]["code"] == "cancelled"
        request("replacement")
        release.set()
        replies = [outgoing.lines.get(timeout=3) for _ in range(8)]
        assert all(reply["ok"] for reply in replies)
        assert "queued-0" not in called
        assert "replacement" in called
    finally:
        release.set()
        incoming.lines.put(None)
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_worker_failure_and_malformed_messages_do_not_break_dispatcher():
    import io

    server = SidecarServer("x" * 32, App())

    def dispatch(operation, params):
        if operation == "fail":
            raise RuntimeError("injected worker failure")
        return "ok"

    server._dispatch = dispatch
    messages = [
        [],
        {"id": "bad-operation", "token": "x" * 32, "operation": {}},
        {"id": "bad-params", "token": "x" * 32, "operation": "fast", "params": []},
        {"id": "failure", "token": "x" * 32, "operation": "fail"},
        {"id": "after", "token": "x" * 32, "operation": "fast"},
    ]
    output = io.StringIO()
    server.run(io.StringIO("\n".join(json.dumps(item) for item in messages)), output)
    replies = {item["id"]: item for item in
               map(json.loads, output.getvalue().splitlines())}
    assert replies[None]["ok"] is False
    assert replies["bad-operation"]["ok"] is False
    assert replies["bad-params"]["ok"] is False
    assert replies["failure"]["error"]["code"] == "internal_error"
    assert replies["after"]["result"] == "ok"


def test_telemetry_and_serialization_failure_always_settle_request(monkeypatch):
    import io
    import medicion_core.sidecar as sidecar

    server = SidecarServer("x" * 32, App())
    server._dispatch = lambda operation, params: float("nan") if operation == "nan" else "ok"

    def event(level, component, operation, **fields):
        if operation == "request_finished" and fields.get("request_operation") == "telemetry":
            raise OSError("log volume unavailable")

    monkeypatch.setattr(sidecar, "event", event)
    messages = [{"id": operation, "token": "x" * 32, "operation": operation}
                for operation in ("telemetry", "nan", "after")]
    output = io.StringIO()
    server.run(io.StringIO("\n".join(map(json.dumps, messages))), output)
    replies = {item["id"]: item for item in
               map(json.loads, output.getvalue().splitlines())}
    assert replies["telemetry"]["error"]["code"] == "internal_error"
    assert replies["nan"]["error"]["code"] == "internal_error"
    assert replies["after"]["result"] == "ok"


def test_unavailable_worker_does_not_skip_application_shutdown(monkeypatch):
    import io
    import medicion_core.sidecar as sidecar

    pools = []

    class UnavailablePool:
        def __init__(self, **kwargs):
            self.stopped = False
            pools.append(self)

        def submit(self, *args, **kwargs):
            raise RuntimeError("pool unavailable")

        def shutdown(self, wait):
            self.stopped = True

    class Application(App):
        stopped = False

        def shutdown(self):
            self.stopped = True

    app = Application()
    server = SidecarServer("x" * 32, app)
    monkeypatch.setattr(sidecar, "ThreadPoolExecutor", UnavailablePool)
    request = {"id": "work", "token": "x" * 32, "operation": "fast"}
    output = io.StringIO()
    assert server.run(io.StringIO(json.dumps(request)), output) == 0
    assert json.loads(output.getvalue())["error"]["code"] == "internal_error"
    assert app.stopped
    assert all(pool.stopped for pool in pools)
