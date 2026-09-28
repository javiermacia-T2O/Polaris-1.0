import tkinter as tk
from tkinter import ttk

import pandas as pd
import pytest

from ui.dialogs.geox_dialog import GeoXDialog


def _walk_widgets(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk_widgets(child)


def test_geox_primary_action_calls_accept_and_keeps_result():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk no está disponible en este entorno")

    dialog = None
    try:
        root.withdraw()
        frame = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=4),
            "region": ["A", "B", "C", "D"],
            "sales": [10, 12, 9, 15],
        })
        dialog = GeoXDialog(root, frame, "Ventas 2026")
        root.update_idletasks()

        buttons = [widget for widget in _walk_widgets(dialog)
                   if isinstance(widget, ttk.Button)]
        run_button = next(button for button in buttons
                          if button.cget("text") == "▶ Ejecutar análisis")
        run_button.invoke()

        assert dialog.result is not None
        assert dialog.result["date_col"] == "date"
        assert dialog.result["region_col"] == "region"
        assert dialog.result["kpi_col"] == "sales"
        assert dialog.result["duration_days"] == 14
        assert "top_n" not in dialog.result
        assert len(dialog.notebook.tabs()) == 2
    finally:
        if dialog is not None:
            try:
                dialog.destroy()
            except tk.TclError:
                pass
        root.destroy()


def test_geox_centers_dialog_over_parent_and_fits_display():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk no está disponible en este entorno")

    dialog = None
    try:
        root.geometry("900x650+40+40")
        root.update_idletasks()
        frame = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=4),
            "region": ["A", "B", "C", "D"],
            "sales": [10, 12, 9, 15],
        })
        dialog = GeoXDialog(root, frame, "Ventas 2026")
        root.update_idletasks()

        parent_center = (root.winfo_rootx() + root.winfo_width() // 2,
                         root.winfo_rooty() + root.winfo_height() // 2)
        dialog_center = (dialog.winfo_rootx() + dialog.winfo_width() // 2,
                         dialog.winfo_rooty() + dialog.winfo_height() // 2)
        assert abs(parent_center[0] - dialog_center[0]) <= 24
        assert abs(parent_center[1] - dialog_center[1]) <= 24
        assert dialog.winfo_width() <= dialog.winfo_screenwidth()
        assert dialog.winfo_height() <= dialog.winfo_screenheight()
    finally:
        if dialog is not None:
            try:
                dialog.destroy()
            except tk.TclError:
                pass
        root.destroy()
