"""Analysis configuration dialog."""

import tkinter as tk
from tkinter import ttk

import pandas as pd

from theme import COLORS, FONTS, Toast
from ui.window_position import center_popup

class AnalysisConfigDialog(tk.Toplevel):
    """
    Diálogo modal con dos pestañas:
      - Configuración: fecha, canal, soporte, inversión, KPIs
      - Filtros: excluir valores de cualquier columna
    """

    def __init__(self, parent, schema: dict, df: pd.DataFrame = None):
        super().__init__(parent)
        self.title(schema.get("title", "Configuración"))
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()

        self.result: dict | None = None
        self._df = df
        self._vars: dict[str, tk.Variable] = {}
        self._listboxes: dict[str, tk.Listbox] = {}

        # Estado de filtros
        self._filter_state: dict[str, dict[str, bool]] = {}
        self._filter_vars: dict[str, dict[str, tk.BooleanVar]] = {}
        self._active_filter_col: str | None = None
        self.search_var: tk.StringVar | None = None

        fields = schema.get("fields", [])
        self._config_fields = [f for f in fields
                                if f.get("type") not in ("filters", "group")]
        self._all_fields = fields
        self._filter_field = next(
            (f for f in fields if f.get("type") == "filters"), None)

        # --- Geometría ---
        w, h = 980, 720
        center_popup(self, parent, w, h)
        self.minsize(840, 580)

        # Guarda el tipo por campo para convertir bien en _on_accept
        # Guarda el tipo por campo para convertir bien en _on_accept
        self._field_kind = {f["key"]: f.get("type", "select")
                            for f in fields
                            if f.get("type") not in ("filters", "group")}

        self._build_ui()
        self.bind("<Escape>", lambda e: self._on_cancel())

    # ==============================================================
    # UI PRINCIPAL
    # ==============================================================
    def _build_ui(self):
        # --- Header ---
        header = tk.Frame(self, bg=COLORS["bg_card"])
        header.pack(fill="x")
        tk.Frame(header, bg=COLORS["primary"], height=2).pack(fill="x")

        hd = tk.Frame(header, bg=COLORS["bg_card"])
        hd.pack(fill="x", padx=20, pady=12)

        title = "Configuración · Inversión ↔ KPI por Canal"
        tk.Label(hd, text=title, bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")

        # --- Notebook con pestañas ---
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=16, pady=(10, 0))

        self.tab_cfg = tk.Frame(self.nb, bg=COLORS["bg"])
        self.tab_filter = tk.Frame(self.nb, bg=COLORS["bg"])

        self.nb.add(self.tab_cfg, text="  ⚙️  Configuración  ")

        has_filters = (self._filter_field is not None
                       and self._filter_field.get("options"))
        if has_filters:
            self.nb.add(self.tab_filter, text="  🔎  Filtros  ")

        self._build_config_tab()
        if has_filters:
            self._build_filter_tab()
        else:
            self._build_no_filters_tab()

        # --- Footer ---
        footer = tk.Frame(self, bg=COLORS["bg_card"])
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=COLORS["border"], height=1).pack(fill="x")

        fr = tk.Frame(footer, bg=COLORS["bg_card"])
        fr.pack(fill="x", padx=20, pady=12)

        self.lbl_status = tk.Label(fr, text="", bg=COLORS["bg_card"],
                                   fg=COLORS["text_muted"],
                                   font=FONTS["small"])
        self.lbl_status.pack(side="left")

        ttk.Button(fr, text="Cancelar",
                   command=self._on_cancel).pack(side="right", padx=(6, 0))
        ttk.Button(fr, text="✓  Aceptar", style="Primary.TButton",
                   command=self._on_accept).pack(side="right")

        self._refresh_status()

    # ==============================================================
    # PESTAÑA CONFIGURACIÓN
    # ==============================================================
    def _build_config_tab(self):
        canvas = tk.Canvas(self.tab_cfg, bg=COLORS["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(self.tab_cfg, orient="vertical",
                               command=canvas.yview)
        inner = tk.Frame(canvas, bg=COLORS["bg"])
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=20, pady=16)
        scroll.pack(side="right", fill="y")

        # Renderizado con soporte de grupos
        for field in self._config_fields:
            if field.get("type") == "group":
                self._build_group_header(inner, field.get("label", ""))
                continue
            self._build_config_field(inner, field)

    def _build_group_header(self, parent, text: str):
        """Cabecera visual para agrupar campos del diálogo."""
        wrap = tk.Frame(parent, bg=COLORS["bg"])
        wrap.pack(fill="x", pady=(16, 6))

        # Línea decorativa arriba
        tk.Frame(wrap, bg=COLORS["border_hi"], height=1).pack(fill="x")

        row = tk.Frame(wrap, bg=COLORS["bg"])
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text=text, bg=COLORS["bg"],
                 fg=COLORS["primary"], font=FONTS["h2"],
                 anchor="w").pack(side="left")

    def _build_config_field(self, parent, field: dict):
        key = field["key"]
        label = field["label"]
        kind = field.get("type", "select")
        options = field.get("options", [])
        default = field.get("default")
        hint = field.get("hint", None)

        card = tk.Frame(parent, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        card.pack(fill="x", pady=(0, 10))

        # --- Título ---
        title_row = tk.Frame(card, bg=COLORS["bg_card"])
        title_row.pack(fill="x", padx=14, pady=(10, 6))
        tk.Label(title_row, text=label, bg=COLORS["bg_card"],
                 fg=COLORS["text"], font=FONTS["h3"],
                 anchor="w").pack(side="left")

        if hint:
            tk.Label(title_row, text=hint, bg=COLORS["bg_card"],
                     fg=COLORS["text_dim"], font=FONTS["tiny"],
                     anchor="e", wraplength=280, justify="right").pack(
                side="right")

        # ---------------- SELECT ----------------
        if kind == "select":
            var = tk.StringVar(value=str(default) if default is not None
                               else (options[0] if options else ""))
            combo = ttk.Combobox(card, textvariable=var,
                                 values=[str(o) for o in options],
                                 state="readonly")
            combo.pack(fill="x", padx=14, pady=(0, 12))
            self._vars[key] = var

        # ---------------- MULTI ----------------
        elif kind == "multi":
            tk.Label(card,
                     text="Ctrl+clic para seleccionar varios.",
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=FONTS["tiny"], anchor="w").pack(
                anchor="w", padx=14, pady=(0, 4))

            box = tk.Listbox(
                card, selectmode="extended", height=6,
                bg=COLORS["bg_input"], fg=COLORS["text"],
                selectbackground=COLORS["primary_dark"],
                selectforeground="#FFFFFF",
                highlightthickness=1,
                highlightbackground=COLORS["border"],
                highlightcolor=COLORS["primary"],
                borderwidth=0, exportselection=False,
                activestyle="none", font=FONTS["small"],
            )
            box.pack(fill="x", padx=14, pady=(0, 12))

            for i, opt in enumerate(options):
                box.insert("end", str(opt))
            defaults = default if isinstance(default, (list, tuple)) else []
            defaults_str = [str(d) for d in defaults]
            for i, opt in enumerate(options):
                if str(opt) in defaults_str:
                    box.selection_set(i)
            self._listboxes[key] = box

        # ---------------- TEXT ----------------
        elif kind == "text":
            var = tk.StringVar(value=str(default) if default is not None else "")
            entry = ttk.Entry(card, textvariable=var)
            entry.pack(fill="x", padx=14, pady=(0, 12))
            self._vars[key] = var

        # ---------------- NUMBER ----------------
        elif kind == "number":
            # Valor de arranque (vacío permitido)
            if default is None or default == "":
                init = ""
            elif isinstance(default, float):
                init = f"{default:g}"
            else:
                init = str(default)

            var = tk.StringVar(value=init)

            row = tk.Frame(card, bg=COLORS["bg_card"])
            row.pack(fill="x", padx=14, pady=(0, 12))

            def _step(delta, v=var):
                try:
                    txt = v.get().strip().replace(",", ".")
                    cur = float(txt) if txt else 0.0
                except Exception:
                    cur = 0.0
                new = cur + delta
                if new == int(new):
                    v.set(str(int(new)))
                else:
                    v.set(f"{new:g}")

            btn_plus = ttk.Button(row, text="+", width=3,
                                  command=lambda: _step(+1))
            btn_plus.pack(side="right", padx=(4, 0))
            btn_minus = ttk.Button(row, text="−", width=3,
                                   command=lambda: _step(-1))
            btn_minus.pack(side="right", padx=(4, 0))

            entry = ttk.Entry(row, textvariable=var, justify="right")
            entry.pack(side="left", fill="x", expand=True)

            # Valida: solo dígitos, punto, coma y signo (o vacío)
            vcmd = (self.register(
                lambda P: P == "" or all(ch.isdigit() or ch in ".,-"
                                          for ch in P)), "%P")
            entry.configure(validate="key", validatecommand=vcmd)

            self._vars[key] = var

        # ---------------- FILTERS ----------------
        elif kind == "filters":
            tk.Label(card,
                     text="Deja todos marcados para no filtrar. "
                          "Desmarca los valores que quieras excluir.",
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=FONTS["tiny"], anchor="w",
                     wraplength=460, justify="left").pack(
                anchor="w", padx=14, pady=(0, 6))

            outer = tk.Frame(card, bg=COLORS["bg_input"],
                             highlightbackground=COLORS["border"],
                             highlightthickness=1)
            outer.pack(fill="x", padx=14, pady=(0, 12))

            canvas = tk.Canvas(outer, bg=COLORS["bg_input"],
                               height=260, highlightthickness=0)
            scroll = ttk.Scrollbar(outer, orient="vertical",
                                   command=canvas.yview)
            inner = tk.Frame(canvas, bg=COLORS["bg_input"])
            inner.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.configure(yscrollcommand=scroll.set)
            canvas.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")

            if not options:
                tk.Label(inner,
                         text="No hay columnas filtrables.",
                         bg=COLORS["bg_input"], fg=COLORS["text_dim"],
                         font=FONTS["tiny"]).pack(padx=8, pady=8)
            else:
                for col in options:
                    self._build_filter_group(inner, col)
    # ==============================================================
    # PESTAÑA FILTROS
    # ==============================================================
    def _build_no_filters_tab(self):
        tk.Label(self.tab_filter,
                 text="No hay columnas filtrables en este dataset.",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["body"]).pack(expand=True, pady=40)

    def _build_filter_tab(self):
        cols = self._filter_field["options"]

        # Debug
        print(f"[DIALOG] Columnas filtrables: {len(cols)}")
        for c in cols:
            try:
                n = self._df[c].nunique(dropna=True) if self._df is not None else 0
                print(f"  · {c}: {n} valores únicos")
            except Exception as e:
                print(f"  · {c}: ERROR {e}")

        # Inicializar estado
        for c in cols:
            vals = self._get_unique_values(c)
            self._filter_state[c] = {v: True for v in vals}
            self._filter_vars[c] = {}

        # --- Barra superior: selector de columna + reset ---
        head = tk.Frame(self.tab_filter, bg=COLORS["bg"])
        head.pack(fill="x", padx=20, pady=(16, 6))

        tk.Label(head, text="Columna a filtrar:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")

        self.combo_filter_col = ttk.Combobox(
            head, values=cols, state="readonly", width=30)
        self.combo_filter_col.pack(side="left", padx=(8, 0))
        self.combo_filter_col.current(0)
        self.combo_filter_col.bind("<<ComboboxSelected>>",
                                   self._on_change_filter_col)

        ttk.Button(head, text="↺ Resetear todos",
                   command=self._reset_all_filters).pack(side="right")

        # --- Cuerpo: izquierda lista, derecha resumen ---
        body = tk.Frame(self.tab_filter, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        # ---- Panel izquierdo ----
        left = tk.Frame(body, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        left.pack(side="left", fill="both", expand=True)

        # Buscador
        search_wrap = tk.Frame(left, bg=COLORS["bg_card"])
        search_wrap.pack(fill="x", padx=10, pady=(10, 6))

        tk.Label(search_wrap, text="Buscar:",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")

        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_wrap, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.search_var.trace_add("write", lambda *a: self._rebuild_values())

        # Botones rápidos
        quick = tk.Frame(left, bg=COLORS["bg_card"])
        quick.pack(fill="x", padx=10, pady=(0, 6))

        ttk.Button(quick, text="Marcar todo", width=12,
                   command=lambda: self._set_all_visible(True)).pack(side="left")
        ttk.Button(quick, text="Desmarcar", width=10,
                   command=lambda: self._set_all_visible(False)).pack(
            side="left", padx=(6, 0))
        ttk.Button(quick, text="Invertir", width=10,
                   command=self._invert_visible).pack(side="left", padx=(6, 0))


        self.lbl_count = tk.Label(quick, text="", bg=COLORS["bg_card"],
                                  fg=COLORS["text_dim"], font=FONTS["tiny"])
        self.lbl_count.pack(side="right")

        # Lista scrollable
        list_wrap = tk.Frame(left, bg=COLORS["bg_card"])
        list_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.val_canvas = tk.Canvas(list_wrap, bg=COLORS["bg_input"],
                                    highlightthickness=1,
                                    highlightbackground=COLORS["border"],
                                    height=300)
        self.val_scroll = ttk.Scrollbar(list_wrap, orient="vertical",
                                        command=self.val_canvas.yview)
        self.val_inner = tk.Frame(self.val_canvas, bg=COLORS["bg_input"])
        self.val_inner.bind(
            "<Configure>",
            lambda e: self.val_canvas.configure(
                scrollregion=self.val_canvas.bbox("all")))
        self.val_win = self.val_canvas.create_window(
            (0, 0), window=self.val_inner, anchor="nw")
        self.val_canvas.configure(yscrollcommand=self.val_scroll.set)

        # Ajusta el ancho del frame interno al ancho del canvas
        self.val_canvas.bind(
            "<Configure>",
            lambda e: self.val_canvas.itemconfig(self.val_win, width=e.width))

        # Scroll con la rueda
        def _on_wheel(ev):
            self.val_canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")

        self.val_canvas.bind("<Enter>",
                             lambda e: self.val_canvas.bind_all("<MouseWheel>", _on_wheel))
        self.val_canvas.bind("<Leave>",
                             lambda e: self.val_canvas.unbind_all("<MouseWheel>"))

        self.val_canvas.pack(side="left", fill="both", expand=True)
        self.val_scroll.pack(side="right", fill="y")

        # ---- Panel derecho: resumen ----
        right = tk.Frame(body, bg=COLORS["bg_card"],
                         highlightbackground=COLORS["border"],
                         highlightthickness=1, width=270)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        tk.Label(right, text="Filtros activos",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h3"]).pack(anchor="w", padx=12, pady=(10, 4))

        self.summary_frame = tk.Frame(right, bg=COLORS["bg_card"])
        self.summary_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        # Primera columna activa
        self._active_filter_col = cols[0]
        self._rebuild_values()
        self._refresh_summary()

    # ==============================================================
    # FILTROS · LÓGICA
    # ==============================================================
    def _get_unique_values(self, col: str) -> list[str]:
        if self._df is None or col not in self._df.columns:
            return []
        try:
            s = self._df[col].dropna().astype(str).str.strip()
            s = s[s != ""]
            return sorted(s.unique().tolist())
        except Exception:
            return []

    def _on_change_filter_col(self, _event=None):
        col = self.combo_filter_col.get()
        if not col:
            return
        self._active_filter_col = col
        if self.search_var is not None:
            self.search_var.set("")
        self._rebuild_values()

    def _rebuild_values(self):
        col = self._active_filter_col
        if not col or not hasattr(self, "val_inner"):
            return

        for w in self.val_inner.winfo_children():
            w.destroy()

        query = self.search_var.get().strip().lower() if self.search_var else ""
        all_vals = list(self._filter_state.get(col, {}).keys())
        visible = [v for v in all_vals if not query or query in v.lower()]

        if col not in self._filter_vars:
            self._filter_vars[col] = {}

        for v in visible:
            if v not in self._filter_vars[col]:
                var = tk.BooleanVar(value=self._filter_state[col].get(v, True))
                self._filter_vars[col][v] = var
            var = self._filter_vars[col][v]

            cb = ttk.Checkbutton(
                self.val_inner, text=v, variable=var,
                command=lambda c=col, val=v: self._on_value_toggle(c, val))
            cb.pack(anchor="w", padx=8, pady=1, fill="x")

        total_sel = sum(1 for v in all_vals if self._filter_state[col].get(v, True))
        self.lbl_count.config(
            text=f"{total_sel}/{len(all_vals)} activos · {len(visible)} visibles")

    def _on_value_toggle(self, col: str, value: str):
        var = self._filter_vars[col].get(value)
        if var is not None:
            self._filter_state[col][value] = var.get()
        self._refresh_summary()
        self._refresh_status()

    def _set_all_visible(self, value: bool):
        col = self._active_filter_col
        if not col:
            return
        for v, var in self._filter_vars[col].items():
            var.set(value)
            self._filter_state[col][v] = value
        self._refresh_summary()
        self._refresh_status()

    def _invert_visible(self):
        col = self._active_filter_col
        if not col:
            return
        for v, var in self._filter_vars[col].items():
            new = not var.get()
            var.set(new)
            self._filter_state[col][v] = new
        self._refresh_summary()
        self._refresh_status()

    def _reset_all_filters(self):
        for col in self._filter_state:
            for v in self._filter_state[col]:
                self._filter_state[col][v] = True
                if col in self._filter_vars and v in self._filter_vars[col]:
                    self._filter_vars[col][v].set(True)
        self._refresh_summary()
        self._refresh_status()

    def _refresh_summary(self):
        if not hasattr(self, "summary_frame"):
            return
        for w in self.summary_frame.winfo_children():
            w.destroy()

        any_filter = False
        for col, vals in self._filter_state.items():
            total = len(vals)
            if total == 0:
                continue
            sel = sum(1 for v in vals.values() if v)
            if sel == total:
                continue
            any_filter = True
            color = COLORS["warning"] if sel == 0 else COLORS["primary"]

            card = tk.Frame(self.summary_frame, bg=COLORS["bg_input"],
                            highlightbackground=color,
                            highlightthickness=1)
            card.pack(fill="x", pady=2)

            tk.Label(card, text=col, bg=COLORS["bg_input"],
                     fg=COLORS["text"], font=FONTS["small"],
                     anchor="w").pack(fill="x", padx=8, pady=(4, 0))
            tk.Label(card, text=f"{sel} de {total} valores activos",
                     bg=COLORS["bg_input"], fg=color,
                     font=FONTS["tiny"], anchor="w").pack(
                fill="x", padx=8, pady=(0, 4))

        if not any_filter:
            tk.Label(self.summary_frame,
                     text="Sin filtros activos",
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=FONTS["small"]).pack(anchor="w", pady=6)

    def _refresh_status(self):
        if not hasattr(self, "lbl_status"):
            return
        n = 0
        for vals in self._filter_state.values():
            total = len(vals)
            if total == 0:
                continue
            if sum(1 for v in vals.values() if v) < total:
                n += 1
        if n == 0:
            self.lbl_status.config(text="Sin filtros aplicados",
                                   fg=COLORS["text_muted"])
        else:
            self.lbl_status.config(
                text=f"{n} columna(s) con filtros activos",
                fg=COLORS["primary"])

    # ==============================================================
    # ACEPTAR / CANCELAR
    # ==============================================================
    def _on_accept(self):
        out = {}

        # --- Selects / textos / números ---
        for key, var in self._vars.items():
            raw = var.get()
            kind = self._field_kind.get(key, "select")

            if kind == "number":
                txt = str(raw).strip().replace(",", ".")
                if txt == "":
                    out[key] = None
                else:
                    try:
                        n = float(txt)
                        out[key] = int(n) if n == int(n) else n
                    except Exception:
                        out[key] = None
            else:
                out[key] = raw

        # --- Multi ---
        for key, box in self._listboxes.items():
            sel = box.curselection()
            out[key] = [box.get(i) for i in sel]

        # --- Filtros de valores ---
        filters = {}
        for col, vals in self._filter_state.items():
            total = len(vals)
            if total == 0:
                continue
            activos = [v for v, keep in vals.items() if keep]
            if len(activos) < total:
                filters[col] = activos
        if filters:
            out["filters"] = filters

        self.result = out
        self.destroy()
    def _on_cancel(self):
        self.result = None
        self.destroy()

# ==================================================================
# DIÁLOGO · UNIR DATAFRAMES
# ==================================================================
# ==================================================================
# DIÁLOGO · UNIR DATAFRAMES (MULTI-SELECCIÓN)
# ==================================================================
