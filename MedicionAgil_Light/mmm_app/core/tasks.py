"""Una tarea de fondo por aplicación; Tkinter solo se toca desde after()."""

import queue
import threading
from concurrent.futures import ThreadPoolExecutor


class TaskCancelled(Exception):
    pass


class TaskManager:
    def __init__(self, root, poll_ms=50):
        self.root = root
        self.poll_ms = poll_ms
        self.executor = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix="mmm-worker")
        self.messages = queue.SimpleQueue()
        self.cancel_event = None
        self.busy = False
        self.closing = False
        self._on_result = None
        self._on_error = None
        self._on_progress = None
        self._on_cancel = None
        self._on_closed = None

    def start(self, work, *, on_result, on_error, on_progress=None,
              on_cancel=None):
        if self.busy or self.closing:
            return False
        self.busy = True
        self.cancel_event = threading.Event()
        self._on_result = on_result
        self._on_error = on_error
        self._on_progress = on_progress
        self._on_cancel = on_cancel
        event = self.cancel_event

        def progress(message):
            if not event.is_set():
                self.messages.put(("progress", message))

        def run():
            try:
                if event.is_set():
                    raise TaskCancelled()
                result = work(event, progress)
                self.messages.put(("result", result))
            except Exception as exc:
                self.messages.put(("error", exc))

        self.executor.submit(run)
        self.root.after(self.poll_ms, self._poll)
        return True

    def cancel(self):
        if self.cancel_event is not None:
            self.cancel_event.set()

    def close(self, on_closed):
        self.closing = True
        self._on_closed = on_closed
        self.cancel()
        if not self.busy:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self._on_closed()

    def _poll(self):
        finished = False
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                if self._on_progress is not None and not self.cancel_event.is_set():
                    self._on_progress(value)
                continue
            finished = True
            self.busy = False
            cancelled = self.cancel_event.is_set() or isinstance(value, TaskCancelled)
            on_cancel, on_error, on_result = (
                self._on_cancel, self._on_error, self._on_result)
            self.cancel_event = None
            self._on_result = self._on_error = None
            self._on_progress = self._on_cancel = None
            if cancelled:
                if on_cancel is not None:
                    on_cancel()
            elif kind == "error":
                on_error(value)
            else:
                on_result(value)
        if self.closing and not self.busy:
            self.executor.shutdown(wait=False, cancel_futures=True)
            if self._on_closed is not None:
                callback = self._on_closed
                self._on_closed = None
                callback()
        elif self.busy and not finished:
            self.root.after(self.poll_ms, self._poll)
