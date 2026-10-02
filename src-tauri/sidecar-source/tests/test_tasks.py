import threading
import time

from core.tasks import TaskManager


class FakeRoot:
    def __init__(self):
        self.pending = []

    def after(self, delay, callback):
        self.pending.append(callback)

    def pump_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            pending, self.pending = self.pending, []
            for callback in pending:
                callback()
            time.sleep(0.005)
        assert predicate()


def test_worker_result_and_progress_return_on_ui_thread():
    root = FakeRoot()
    manager = TaskManager(root, poll_ms=1)
    main_thread = threading.get_ident()
    calls = []

    def work(cancel, progress):
        assert threading.get_ident() != main_thread
        progress("reading")
        return 42

    assert manager.start(work, on_result=lambda value: calls.append(
        ("result", value, threading.get_ident())),
        on_error=lambda exc: calls.append(("error", exc)),
        on_progress=lambda message: calls.append(
            ("progress", message, threading.get_ident())))
    assert not manager.start(work, on_result=lambda *_: None,
                             on_error=lambda *_: None)
    root.pump_until(lambda: not manager.busy)
    assert calls == [("progress", "reading", main_thread),
                     ("result", 42, main_thread)]
    manager.close(lambda: None)


def test_cancel_discards_result_and_closes_cleanly():
    root = FakeRoot()
    manager = TaskManager(root, poll_ms=1)
    started = threading.Event()
    calls = []

    def work(cancel, progress):
        started.set()
        cancel.wait(1)
        return "late result"

    manager.start(work, on_result=lambda result: calls.append(result),
                  on_error=lambda exc: calls.append(exc),
                  on_cancel=lambda: calls.append("cancelled"))
    assert started.wait(1)
    manager.close(lambda: calls.append("closed"))
    root.pump_until(lambda: "closed" in calls)
    assert calls == ["cancelled", "closed"]


def test_result_callback_can_start_a_followup_task():
    root = FakeRoot()
    manager = TaskManager(root, poll_ms=1)
    calls = []

    def first_done(value):
        calls.append(value)
        assert manager.start(lambda cancel, progress: "second",
                             on_result=calls.append,
                             on_error=lambda exc: calls.append(exc))

    manager.start(lambda cancel, progress: "first",
                  on_result=first_done,
                  on_error=lambda exc: calls.append(exc))
    root.pump_until(lambda: len(calls) == 2)
    assert calls == ["first", "second"]
    manager.close(lambda: None)
