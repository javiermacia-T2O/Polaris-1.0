import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

import pandas as pd
import pytest

from app_desktop import MMMApp


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


class _FilterApp(tk.Tk):
    _tb_open_filter = MMMApp._tb_open_filter

    def __init__(self):
        super().__init__()
        self.withdraw()
        self.data = pd.DataFrame({"category": [f"item-{i:03d}" for i in range(500)]})
        self._tb_builder_dataset = None
        self.session = SimpleNamespace(active_dataset=None)
        self.tb_filters = {}
        self._tb_filter_values_cache = {}
        self.tasks = SimpleNamespace(busy=False, closing=False,
                                     cancel=lambda: None)
        self.refresh_count = 0
        self.preview_count = 0
        self.worker_thread_id = None
        self.result_thread_id = None

        self._start_task = self._start_test_task

    def _tb_source_df(self):
        return self.data

    def _tb_refresh_lists(self):
        self.refresh_count += 1

    def _tb_update_preview(self):
        self.preview_count += 1

    def _start_test_task(self, work, on_result, _label):
        result_queue = queue.Queue()

        def worker():
            self.worker_thread_id = threading.get_ident()
            result_queue.put(work(threading.Event(), lambda _message: None))

        def deliver_when_ready():
            try:
                result = result_queue.get_nowait()
            except queue.Empty:
                self.after(5, deliver_when_ready)
                return
            self.result_thread_id = threading.get_ident()
            on_result(result)

        threading.Thread(target=worker, daemon=True).start()
        self.after(5, deliver_when_ready)
        return True


def _wait_for_listbox(root):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        root.update()
        for child in root.winfo_children():
            for widget in _walk(child):
                if isinstance(widget, tk.Listbox):
                    return child, widget
        time.sleep(0.01)
    pytest.fail("El filtro no terminó de cargar sus opciones")


def test_categorical_filter_uses_listbox_and_keeps_hidden_selections():
    try:
        app = _FilterApp()
    except tk.TclError:
        pytest.skip("Tk no está disponible en este entorno")

    dialog = None
    try:
        main_thread_id = threading.get_ident()
        app._tb_open_filter("category")
        dialog, value_list = _wait_for_listbox(app)

        assert app.worker_thread_id != main_thread_id
        assert app.result_thread_id == main_thread_id
        assert value_list.size() == 500
        assert len([w for w in _walk(dialog)
                    if isinstance(w, ttk.Checkbutton)]) == 0
        assert len(value_list.curselection()) == 500

        search = next(w for w in _walk(dialog)
                      if isinstance(w, ttk.Entry))
        search.insert(0, "item-49")
        app.update()
        assert value_list.size() == 10

        value_list.selection_clear(0, "end")
        value_list.selection_set(0)
        value_list.event_generate("<<ListboxSelect>>")
        app.update()
        search.delete(0, "end")
        app.update()
        assert value_list.size() == 500
        assert len(value_list.curselection()) == 491

        buttons = {button.cget("text"): button for button in _walk(dialog)
                   if isinstance(button, ttk.Button)}
        buttons["✓  Aplicar cambios"].invoke()
        assert app.tb_filters["category"] == (
            set(app.data["category"]) -
            {f"item-{i:03d}" for i in range(491, 500)})
        assert app.refresh_count == 1
        assert app.preview_count == 1
    finally:
        try:
            app.destroy()
        except tk.TclError:
            pass


def test_categorical_filter_mark_all_and_none_work_with_search():
    try:
        app = _FilterApp()
    except tk.TclError:
        pytest.skip("Tk no está disponible en este entorno")

    try:
        app._tb_open_filter("category")
        dialog, value_list = _wait_for_listbox(app)
        buttons = {button.cget("text"): button for button in _walk(dialog)
                   if isinstance(button, ttk.Button)}
        search = next(w for w in _walk(dialog) if isinstance(w, ttk.Entry))
        search.insert(0, "item-49")

        buttons["Desmarcar"].invoke()
        assert len(value_list.curselection()) == 0
        buttons["Marcar todo"].invoke()
        assert len(value_list.curselection()) == 10
        search.delete(0, "end")
        app.update()
        assert len(value_list.curselection()) == 500
    finally:
        try:
            app.destroy()
        except tk.TclError:
            pass
