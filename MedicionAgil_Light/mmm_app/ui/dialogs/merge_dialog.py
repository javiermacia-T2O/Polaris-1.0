"""Dialog for merging datasets."""

import tkinter as tk
from tkinter import ttk, messagebox

import pandas as pd

from core.tasks import TaskCancelled
from services.active_dataset import LazyLoaded
from services.merge_service import concat_datasets, merge_datasets
from theme import COLORS, FONTS
from ui.window_position import center_popup


def _dataset_columns(item):
    return item.dataset.columns if isinstance(item, LazyLoaded) else item.columns


def _dataset_shape(item):
    if isinstance(item, LazyLoaded):
        return int(item.stats["rows"]), len(item.dataset.columns)
    return item.shape


def prepare_selected_datasets(datasets, selected, cancel):
    """Materialize only chosen lazy inputs, subject to the shared RAM budget."""
    frames = {}
    reserved = sum(int(datasets[name].memory_usage(deep=True).sum())
                   for name in selected
                   if isinstance(datasets[name], pd.DataFrame))
    for name in selected:
        if cancel.is_set():
            raise TaskCancelled()
        item = datasets[name]
        if isinstance(item, LazyLoaded):
            frame = item.dataset.analysis_frame(cancel, reserved_bytes=reserved)
            reserved += int(frame.memory_usage(deep=True).sum())
        else:
            frame = item
        frames[name] = frame
    return frames


class MergeDialog(tk.Toplevel):
    """
    Diálogo para unir 2 o más DataFrames del pool.

    Permite marcar varios DFs y unirlos en cadena:
      - concat: apila filas de todos (en orden del pool)
      - merge: une por columnas clave comunes a todos
    """

    def __init__(self, parent, datasets: dict[str, pd.DataFrame],
                 default_a: str | None = None):
        super().__init__(parent)
        self.title("Unir DataFrames")
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()

        self.datasets = datasets
        self.result: dict | None = None
        self._check_vars: dict[str, tk.BooleanVar] = {}
        self._merge_key_vars: dict[str, tk.BooleanVar] = {}

        w, h = 820, 720
        center_popup(self, parent, w, h)
        self.minsize(720, 600)

        self._build_ui(default_a)
        self._refresh_selection_info()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # --------------------------------------------------------------
    def _build_ui(self, default_a):
        names = list(self.datasets.keys())

        # --- Header ---
        header = tk.Frame(self, bg=COLORS["bg_card"])
        header.pack(fill="x")
        tk.Frame(header, bg=COLORS["primary"], height=2).pack(fill="x")
        tk.Label(header, text="🔗  Unir DataFrames",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(anchor="w", padx=20, pady=12)

        # --- Cuerpo scrollable ---
        body_wrap = tk.Frame(self, bg=COLORS["bg"])
        body_wrap.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        canvas = tk.Canvas(body_wrap, bg=COLORS["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(body_wrap, orient="vertical",
                                command=canvas.yview)
        body = tk.Frame(canvas, bg=COLORS["bg"])
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # --- Selección de DataFrames ---
        tk.Label(body, text="DataFrames a unir",
                 bg=COLORS["bg"], fg=COLORS["primary"],
                 font=FONTS["kpi_lbl"]).pack(anchor="w", pady=(6, 4))

        sel_card = tk.Frame(body, bg=COLORS["bg_card"],
                             highlightbackground=COLORS["border"],
                             highlightthickness=1)
        sel_card.pack(fill="x", pady=(0, 10))
        sel_inner = tk.Frame(sel_card, bg=COLORS["bg_card"])
        sel_inner.pack(fill="x", padx=12, pady=10)

        tk.Label(sel_inner,
                 text="Marca al menos 2. Se unirán en el orden que "
                      "aparecen en el pool (arriba → abajo).",
                 bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], justify="left",
                 wraplength=720, anchor="w").pack(anchor="w", pady=(0, 6))

        # Botones rápidos
        quick = tk.Frame(sel_inner, bg=COLORS["bg_card"])
        quick.pack(fill="x", pady=(0, 6))

        def _sel_all():
            for v in self._check_vars.values():
                v.set(True)
            self._refresh_selection_info()
            self._on_selection_change()

        def _sel_none():
            for v in self._check_vars.values():
                v.set(False)
            self._refresh_selection_info()
            self._on_selection_change()

        ttk.Button(quick, text="Marcar todos",
                   command=_sel_all).pack(side="left")
        ttk.Button(quick, text="Desmarcar",
                   command=_sel_none).pack(side="left", padx=(6, 0))

        self.lbl_sel_count = tk.Label(
            quick, text="", bg=COLORS["bg_card"],
            fg=COLORS["text_muted"], font=FONTS["tiny"])
        self.lbl_sel_count.pack(side="right")

        # Checkboxes
        initially_selected = ([default_a] if default_a in names else [names[0]])
        initially_selected += [n for n in names if n != initially_selected[0]][:1]
        for name in names:
            df = self.datasets[name]
            var = tk.BooleanVar(value=name in initially_selected)
            self._check_vars[name] = var
            row = tk.Frame(sel_inner, bg=COLORS["bg_card"])
            row.pack(fill="x", pady=1)
            ttk.Checkbutton(
                row, text=name, variable=var,
                command=self._on_selection_change,
            ).pack(side="left")
            tk.Label(row,
                     text=f"({_dataset_shape(df)[0]:,} × {_dataset_shape(df)[1]})"
                          .replace(",", "."),
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=FONTS["tiny"]).pack(side="left", padx=(6, 0))

        # Info común
        self.lbl_common = tk.Label(
            body, text="", bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=FONTS["tiny"], justify="left", wraplength=760)
        self.lbl_common.pack(anchor="w", pady=(0, 10))

        # --- Tipo de unión ---
        tk.Label(body, text="Tipo de unión",
                 bg=COLORS["bg"], fg=COLORS["primary"],
                 font=FONTS["kpi_lbl"]).pack(anchor="w", pady=(6, 4))

        type_card = tk.Frame(body, bg=COLORS["bg_card"],
                              highlightbackground=COLORS["border"],
                              highlightthickness=1)
        type_card.pack(fill="x", pady=(0, 10))
        tf_inner = tk.Frame(type_card, bg=COLORS["bg_card"])
        tf_inner.pack(fill="x", padx=12, pady=10)

        self.var_type = tk.StringVar(value="concat")
        ttk.Radiobutton(tf_inner, text="Apilar filas (concat)",
                        variable=self.var_type, value="concat",
                        command=self._on_type_change).pack(anchor="w")
        ttk.Radiobutton(tf_inner, text="Unir por columnas clave (merge)",
                        variable=self.var_type, value="merge",
                        command=self._on_type_change).pack(anchor="w")

        # --- Panel dinámico ---
        self.dyn_frame = tk.Frame(body, bg=COLORS["bg"])
        self.dyn_frame.pack(fill="both", expand=True)

        # --- Nombre ---
        name_frame = tk.Frame(body, bg=COLORS["bg"])
        name_frame.pack(fill="x", pady=(10, 0))
        tk.Label(name_frame, text="Nombre del resultado:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        self.var_name = tk.StringVar(value="unido")
        ttk.Entry(name_frame, textvariable=self.var_name,
                  width=30).pack(side="left", padx=(6, 0))

        # --- Footer ---
        footer = tk.Frame(self, bg=COLORS["bg_card"])
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=COLORS["border"], height=1).pack(fill="x")
        fr = tk.Frame(footer, bg=COLORS["bg_card"])
        fr.pack(fill="x", padx=20, pady=12)
        ttk.Button(fr, text="Cancelar",
                   command=self._on_cancel).pack(side="right", padx=(6, 0))
        self.btn_merge = ttk.Button(fr, text="✓  Unir",
                                    style="Primary.TButton",
                                    command=self._on_apply)
        self.btn_merge.pack(side="right")
        self.lbl_merge_status = tk.Label(fr, text="", bg=COLORS["bg_card"],
                                         fg=COLORS["text_muted"])
        self.lbl_merge_status.pack(side="left")

        self._on_type_change()

    # --------------------------------------------------------------
    def _selected_names(self) -> list[str]:
        """Nombres de los DFs marcados, en el orden del pool."""
        return [n for n in self.datasets.keys()
                if self._check_vars.get(n) and self._check_vars[n].get()]

    def _refresh_selection_info(self):
        sel = self._selected_names()
        total = len(self.datasets)
        self.lbl_sel_count.config(text=f"{len(sel)}/{total} seleccionados")

        if len(sel) < 2:
            self.lbl_common.config(
                text="Selecciona al menos 2 DataFrames.",
                fg=COLORS["warning"])
            return

        # Columnas comunes a TODOS los seleccionados
        comunes = set(_dataset_columns(self.datasets[sel[0]]))
        for n in sel[1:]:
            comunes &= set(_dataset_columns(self.datasets[n]))

        detalles = [f"{n}: {_dataset_shape(self.datasets[n])[0]:,}×"
                    f"{_dataset_shape(self.datasets[n])[1]}".replace(",", ".")
                    for n in sel]
        self.lbl_common.config(
            text=(" · ".join(detalles) +
                  f"\nColumnas comunes a todos: {len(comunes)}"),
            fg=COLORS["text_muted"])

    def _on_selection_change(self):
        self._refresh_selection_info()
        self._on_type_change()

    def _on_type_change(self):
        for w in self.dyn_frame.winfo_children():
            w.destroy()
        self._merge_key_vars = {}

        sel = self._selected_names()
        if len(sel) < 2:
            return

        comunes = list(_dataset_columns(self.datasets[sel[0]]))
        for n in sel[1:]:
            comunes = [c for c in comunes
                       if c in _dataset_columns(self.datasets[n])]

        if self.var_type.get() == "concat":
            self._build_concat_panel(comunes, sel)
        else:
            self._build_merge_panel(comunes, sel)

    def _build_concat_panel(self, comunes, sel):
        card = tk.Frame(self.dyn_frame, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=COLORS["bg_card"])
        inner.pack(fill="x", padx=12, pady=10)

        tk.Label(inner,
                 text=f"Apila las filas de los {len(sel)} DataFrames "
                      f"en el orden mostrado arriba.",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(anchor="w", pady=(0, 6))

        self.var_concat_mode = tk.StringVar(value="common")
        ttk.Radiobutton(
            inner,
            text=f"Solo columnas comunes ({len(comunes)})",
            variable=self.var_concat_mode,
            value="common").pack(anchor="w")
        ttk.Radiobutton(
            inner,
            text="Todas las columnas (NaN donde falte)",
            variable=self.var_concat_mode,
            value="all").pack(anchor="w")

    def _build_merge_panel(self, comunes, sel):
        card = tk.Frame(self.dyn_frame, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=COLORS["bg_card"])
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        tk.Label(inner,
                 text=f"Une por columnas clave (deben existir en "
                      f"los {len(sel)} DataFrames).",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(anchor="w", pady=(0, 6))

        if not comunes:
            tk.Label(inner,
                     text="⚠ No hay columnas comunes a todos los DFs "
                          "seleccionados: no se puede hacer merge.",
                     bg=COLORS["bg_card"], fg=COLORS["warning"],
                     font=FONTS["small"]).pack(anchor="w")
            return

        tk.Label(inner, text="Columnas clave:",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h3"]).pack(anchor="w", pady=(4, 2))

        keys_wrap = tk.Frame(inner, bg=COLORS["bg_card"])
        keys_wrap.pack(fill="x")
        for c in comunes:
            v = tk.BooleanVar(
                value=any(k in str(c).lower()
                          for k in ("fecha", "date", "id", "key")))
            self._merge_key_vars[c] = v
            ttk.Checkbutton(keys_wrap, text=c, variable=v).pack(anchor="w")

        tk.Label(inner, text="Tipo de join:",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h3"]).pack(anchor="w", pady=(10, 2))

        self.var_how = tk.StringVar(value="inner")
        how_row = tk.Frame(inner, bg=COLORS["bg_card"])
        how_row.pack(fill="x")
        for label, val in [("inner (solo coincidencias en todos)", "inner"),
                           ("outer (todo, NaN donde falte)", "outer")]:
            ttk.Radiobutton(how_row, text=label, variable=self.var_how,
                            value=val).pack(anchor="w")

        tk.Label(inner,
                 text="Nota: en cadenas de >2 DFs solo se permiten "
                      "inner / outer para que el resultado sea "
                      "consistente.",
                 bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], justify="left",
                 wraplength=720).pack(anchor="w", pady=(4, 0))

    # --------------------------------------------------------------
    def _on_apply(self):
        sel = self._selected_names()
        if len(sel) < 2:
            messagebox.showwarning("Aviso",
                                    "Selecciona al menos 2 DataFrames.", parent=self)
            return

        name = self.var_name.get().strip() or "unido"
        operation = self.var_type.get()
        mode = self.var_concat_mode.get() if operation == "concat" else None
        keys = ([c for c, var in self._merge_key_vars.items() if var.get()]
                if operation != "concat" else None)
        how = self.var_how.get() if operation != "concat" else None
        selected_items = {name: self.datasets[name] for name in sel}

        def work(cancel, progress):
            progress("Preparando datasets seleccionados...")
            frames = prepare_selected_datasets(selected_items, sel, cancel)
            progress("Uniendo datasets...")
            result = (concat_datasets(frames, sel, mode)
                      if operation == "concat"
                      else merge_datasets(frames, sel, keys, how))
            if cancel.is_set():
                raise TaskCancelled()
            return self.master._ensure_date_sorted(result, name)

        def done(result):
            self._merge_active = False
            if name in self.datasets and not messagebox.askyesno(
                    "Sobrescribir", f"'{name}' ya existe en el pool. ¿Sobrescribir?", parent=self):
                return
            self.result = {"name": name, "df": result}
            self.destroy()

        def failed(exc):
            self._merge_active = False
            self.btn_merge.state(["!disabled"])
            self.lbl_merge_status.config(text="No se pudo unir")
            messagebox.showerror("Error al unir", str(exc), parent=self)

        def cancelled():
            self._merge_active = False
            self.btn_merge.state(["!disabled"])
            self.lbl_merge_status.config(text="Unión cancelada")

        if not self.master.tasks.start(
                work, on_result=done, on_error=failed,
                on_progress=lambda text: self.lbl_merge_status.config(text=text),
                on_cancel=cancelled):
            messagebox.showwarning("Aviso", "Ya hay una tarea en curso.", parent=self)
            return
        self._merge_active = True
        self.btn_merge.state(["disabled"])
        self.lbl_merge_status.config(text="Uniendo datasets...")

    def _do_concat(self, sel: list[str], mode=None) -> pd.DataFrame:
        """Concatena todos los DFs seleccionados."""
        mode = self.var_concat_mode.get() if mode is None else mode
        return concat_datasets(self.datasets, sel, mode)

    def _do_merge(self, sel: list[str], keys=None, how=None) -> pd.DataFrame:
        """Hace merge en cadena de todos los DFs seleccionados."""
        keys = ([c for c, v in self._merge_key_vars.items() if v.get()]
                if keys is None else keys)
        if not keys:
            raise ValueError("Selecciona al menos una columna clave.")

        how = self.var_how.get() if how is None else how

        return merge_datasets(self.datasets, sel, keys, how)

    def _on_cancel(self):
        if getattr(self, "_merge_active", False):
            self.master.tasks.cancel()
            return
        self.result = None
        self.destroy()

# ==================================================================
# HELPERS DE PIVOT CON DUCKDB
# ==================================================================

# ==================================================================
# DIÁLOGO · CAUSAL IMPACT (v2 · revisado)
# ==================================================================
