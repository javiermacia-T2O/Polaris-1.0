"""Regression configuration dialog."""

import copy
import warnings

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from core.events import (
    add_sub_event, clear_regression_history, create_group,
    generate_anomaly_columns, load_events, load_last_regression_config,
    load_regression_history, remove_sub_event, save_events,
    save_last_regression_config, toggle_own, update_sub_event,
)
from core.loader import get_date_columns
from services.analysis_service import prepare_regression_data
from theme import COLORS, FONTS, Toast, Tooltip
from ui.dialogs.preflight_dialog import PreflightDialog
from ui.window_position import center_popup

REGRESSION_TYPES = [
    "Automático (selección temporal OOS)",
    "OLS (mínimos cuadrados, con p-values)",
    "Evento / ITS segmentada (OLS con HAC)",
    "Ridge (regularización L2)",
    "Lasso (regularización L1)",
    "Elastic Net",
    "Bayesian Ridge",
    "Huber (robusto a outliers)",
    "Random Forest",
    "Gradient Boosting",
]

CHART_TYPES = ["Línea", "Barra", "Área", "Dispersión"]

CHART_PALETTE = [
    "#58A6FF", "#F85149", "#3FB950", "#D29922", "#BC8CFF",
    "#79B8FF", "#FF7B72", "#7EE787", "#FFA657", "#D2A8FF",
]


def ordered_temporal_chart_frame(frame: pd.DataFrame, date_column: str):
    """Keep dates and every plotted series aligned in chronological order."""
    if date_column not in frame.columns:
        return frame, None
    dates = pd.to_datetime(frame[date_column], errors="coerce", utc=True)
    valid_positions = np.flatnonzero(dates.notna().to_numpy())
    if not len(valid_positions):
        return frame, None
    ordered_positions = valid_positions[np.argsort(
        dates.iloc[valid_positions].to_numpy(), kind="stable")]
    return (frame.iloc[ordered_positions].reset_index(drop=True),
            dates.iloc[ordered_positions].reset_index(drop=True))


def event_dates_from_indices(frame: pd.DataFrame, date_column: str,
                             start_idx: int, end_idx: int):
    """Resuelve índices del gráfico a fechas inclusivas del contrato común."""
    if date_column not in frame.columns:
        return None, None
    dates = pd.to_datetime(frame[date_column], errors="coerce").dropna()
    dates = dates.sort_values(ignore_index=True)
    if dates.empty:
        return None, None
    i0 = max(0, min(int(round(start_idx)), len(dates) - 1))
    i1 = max(0, min(int(round(end_idx)), len(dates) - 1))
    lo, hi = sorted((i0, i1))
    return pd.Timestamp(dates.iloc[lo]), pd.Timestamp(dates.iloc[hi])

class RegressionDialog(tk.Toplevel):
    """
    Diálogo de regresión con:
      - Pestaña Configurar: gráfico + variables + anomalías
      - Pestaña Historial: ejecuciones anteriores
      - Estado global en la cabecera
      - Validación pre-flight antes de ejecutar
      - Persistencia automática de la última configuración
    """

    def __init__(self, parent, df: pd.DataFrame, dataset_name: str):
        super().__init__(parent)
        self.title("Regresión · configuración")
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()

        self.df_full = df
        self.df = self._sample_for_preview(df)
        self.dataset_name = dataset_name
        self.events = load_events(dataset_name)
        self.marking_mode = False
        self.drag_start = None
        self.result = None
        self._tooltips: list = []

        # Mantener el diálogo dentro de la pantalla que contiene la app.
        w = min(1360, max(900, self.winfo_screenwidth() - 80))
        h = min(820, max(620, self.winfo_screenheight() - 100))
        self.minsize(min(1180, w), min(700, h))
        center_popup(self, parent, w, h)
        self._chart_dates = None

        # Estado
        self.var_cfg: dict[str, dict] = {}
        self.anomaly_cfg: dict[str, dict] = {}
        self.history = load_regression_history(dataset_name)

        # --- Construcción ---
        self._build_ui()
        self._restore_last_config()
        self._update_method_note()
        self._refresh_variables()
        self._refresh_events_list()
        self._refresh_history()
        self._refresh_chart()
        self._refresh_global_status()
        self._regression_active = False
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    @staticmethod
    def _sample_for_preview(df):
        """Muestra distribuida para los controles; el modelo usa df_full."""
        step = max(1, (len(df) + 49_999) // 50_000)
        return df.iloc[::step].head(50_000)

    @staticmethod
    def _sample_for_chart(df):
        """Bound Matplotlib drawing on the Tk thread to 2,000 points."""
        step = max(1, (len(df) + 1_999) // 2_000)
        return df.iloc[::step].head(2_000)

    # ==============================================================
    # UI
    # ==============================================================
    def _build_ui(self):
        # ---------- HEADER ----------
        header = tk.Frame(self, bg=COLORS["bg_card"])
        header.pack(fill="x", side="top")
        tk.Frame(header, bg=COLORS["primary"], height=2).pack(fill="x")

        hd = tk.Frame(header, bg=COLORS["bg_card"])
        hd.pack(fill="x", padx=20, pady=10)

        tk.Label(hd, text="Regresión · configuración y estimación",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")

        self.lbl_method_note = tk.Label(
            self, text="", bg=COLORS["bg"], fg=COLORS["text_muted"],
            font=FONTS["small"], anchor="w", justify="left")
        self.lbl_method_note.pack(fill="x", padx=20, pady=(0, 2))

        self.lbl_global_status = tk.Label(
            hd, text="", bg=COLORS["bg_card"],
            fg=COLORS["text_muted"], font=FONTS["small"])
        self.lbl_global_status.pack(side="right")

        # ---------- NOTEBOOK ----------
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=16, pady=(8, 0))

        self.tab_cfg = tk.Frame(self.nb, bg=COLORS["bg"])
        self.tab_hist = tk.Frame(self.nb, bg=COLORS["bg"])
        self.nb.add(self.tab_cfg, text="  ⚙️  Configurar  ")
        self.nb.add(self.tab_hist, text="  📋  Historial  ")

        self._build_config_tab()
        self._build_history_tab()

        # ---------- FOOTER ----------
        footer = tk.Frame(self, bg=COLORS["bg_card"])
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=COLORS["border"], height=1).pack(fill="x")

        fr = tk.Frame(footer, bg=COLORS["bg_card"])
        fr.pack(fill="x", padx=20, pady=12)

        self.lbl_footer_status = tk.Label(
            fr, text="", bg=COLORS["bg_card"], fg=COLORS["text_muted"],
            font=FONTS["small"])
        self.lbl_footer_status.pack(side="left")

        ttk.Button(fr, text="Cancelar",
                   command=self._on_cancel).pack(side="right", padx=(6, 0))

        self.btn_run = ttk.Button(fr, text="▶  Estimar modelo",
                                   style="Primary.TButton",
                                   command=self._on_execute)
        self.btn_run.pack(side="right")

    def _update_method_note(self):
        method = self.reg_type_var.get()
        if str(method).lower().startswith("automático"):
            note = ("Selección automática: compara modelos con validación "
                    "temporal OOS y conserva el más simple si rinde dentro "
                    "del 2 % del mejor. No implica causalidad.")
        elif str(method).lower().startswith("evento / its"):
            note = ("Evento / ITS: requiere un único evento/grupo marcado. Estima "
                    "cambios de nivel y pendiente; interpretar con cautela si "
                    "coinciden otros cambios.")
        else:
            note = ("Regresión predictiva: explica variación del modelo. Sus "
                    "contribuciones no representan incrementalidad causal.")
        label = getattr(self, "lbl_method_note", None)
        if label is not None:
            label.config(text=note)

    # ---------------- Pestaña Configurar ----------------
    def _build_config_tab(self):
        # --- Barra superior: tipo + fecha ---
        top = tk.Frame(self.tab_cfg, bg=COLORS["bg"], padx=16, pady=10)
        top.pack(fill="x", side="top")

        tk.Label(top, text="Método de estimación:", bg=COLORS["bg"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).grid(
            row=0, column=0, sticky="w")
        self.reg_type_var = tk.StringVar(value=REGRESSION_TYPES[0])
        self.cb_reg_type = ttk.Combobox(top, textvariable=self.reg_type_var,
                                values=REGRESSION_TYPES, state="readonly",
                                width=44)
        self.cb_reg_type.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        self.cb_reg_type.bind("<<ComboboxSelected>>",
                              lambda _event: (self._update_method_note(),
                                              self._refresh_global_status()))
        self._update_method_note()
        top.columnconfigure(1, weight=1)
        self._tooltips.append(Tooltip(
            self.cb_reg_type,
            "OLS: interpretable con p-values.\n"
            "Ridge/Lasso: regularizan y reducen overfitting.\n"
            "Bayesian Ridge: incertidumbre.\n"
            "Huber: robusto a outliers.\n"
            "Random Forest / Gradient Boosting: no lineales."))

        tk.Label(top, text="Columna de fecha:", bg=COLORS["bg"],
                 fg=COLORS["text_muted"], font=FONTS["small"]).grid(
            row=0, column=2, sticky="w")
        date_cols = get_date_columns(self.df)
        self.date_var = tk.StringVar(
            value=str(date_cols[0]) if date_cols else "")
        cb_date = ttk.Combobox(top, textvariable=self.date_var,
                                values=[str(c) for c in date_cols],
                                state="readonly", width=20)
        cb_date.grid(row=0, column=3, sticky="w", padx=(6, 0))
        cb_date.bind("<<ComboboxSelected>>", lambda _e: self._refresh_chart())

        # --- Cuerpo: izquierda gráfico, derecha controles ---
        body = tk.Frame(self.tab_cfg, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        # ============ IZQUIERDA: gráfico ============
        left = tk.Frame(body, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        left.pack(side="left", fill="both", expand=True)

        chart_head = tk.Frame(left, bg=COLORS["bg_card"])
        chart_head.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(chart_head, text="Serie temporal",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")

        # Botón: añadir evento manualmente (con fechas escritas)
        self.btn_add_manual = tk.Button(
            chart_head, text="➕ Añadir evento",
            relief="flat", bg=COLORS["bg_input"], fg=COLORS["text"],
            activebackground=COLORS["bg_hover"],
            font=FONTS["small"], padx=10, pady=4, cursor="hand2",
            command=self._open_manual_event_dialog)
        self.btn_add_manual.pack(side="right", padx=(0, 6))

        # Botón: modo marcado con arrastre
        self.btn_mark = tk.Button(
            chart_head, text="🔵 Marcar eventos",
            relief="flat", bg=COLORS["bg_input"], fg=COLORS["text"],
            activebackground=COLORS["bg_hover"],
            font=FONTS["small"], padx=10, pady=4, cursor="hand2",
            command=self._toggle_marking)
        self.btn_mark.pack(side="right", padx=(0, 6))

        self._tooltips.append(Tooltip(
            self.btn_mark,
            "Actívalo y arrastra sobre el gráfico para marcar\n"
            "un evento (promoción, rotura, apagón...)."))

        # Barra de opciones
        opt = tk.Frame(left, bg=COLORS["bg_card"])
        opt.pack(fill="x", padx=10, pady=(0, 4))

        tk.Label(opt, text="Tipo por defecto:",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["tiny"]).pack(side="left")
        self.default_type = tk.StringVar(value="Línea")
        cb_def = ttk.Combobox(opt, textvariable=self.default_type,
                               values=CHART_TYPES, state="readonly", width=11)
        cb_def.pack(side="left", padx=(6, 12))
        cb_def.bind("<<ComboboxSelected>>",
                    lambda e: self._apply_default_type())

        self.show_anomalies = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Eventos",
                        variable=self.show_anomalies,
                        command=self._refresh_chart).pack(side="left")

        self.show_grid = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Rejilla",
                        variable=self.show_grid,
                        command=self._refresh_chart).pack(
            side="left", padx=(10, 0))

        tk.Label(opt, text="rueda: zoom · doble clic: reset",
                 bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"]).pack(side="right")

        # Canvas
        chart_wrap = tk.Frame(left, bg=COLORS["bg_card"])
        chart_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.fig = Figure(figsize=(8, 5), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_wrap)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.canvas.mpl_connect("button_press_event", self._on_double_click)

        # ============ DERECHA: controles ============
        right_wrap = tk.Frame(body, bg=COLORS["bg"], width=440)
        right_wrap.pack(side="right", fill="y", padx=(12, 0))
        right_wrap.pack_propagate(False)

        r_canvas = tk.Canvas(right_wrap, bg=COLORS["bg"],
                             highlightthickness=0, borderwidth=0)
        r_scroll = ttk.Scrollbar(right_wrap, orient="vertical",
                                 command=r_canvas.yview)
        right = tk.Frame(r_canvas, bg=COLORS["bg"])
        right.bind("<Configure>",
                   lambda e: r_canvas.configure(
                       scrollregion=r_canvas.bbox("all")))
        win_id = r_canvas.create_window((0, 0), window=right, anchor="nw")
        r_canvas.configure(yscrollcommand=r_scroll.set)
        r_canvas.bind("<Configure>",
                      lambda e: r_canvas.itemconfig(win_id, width=e.width))
        r_canvas.pack(side="left", fill="both", expand=True)
        r_scroll.pack(side="right", fill="y")

        # --- Variable a predecir ---
        hdr = tk.Frame(right, bg=COLORS["bg"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="Variable a predecir", bg=COLORS["bg"],
                 fg=COLORS["text"], font=FONTS["h3"]).pack(side="left")
        tk.Label(hdr, text="(target del modelo)", bg=COLORS["bg"],
                 fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(
            side="left", padx=(6, 0))

        num_cols = self.df.select_dtypes("number").columns.tolist()
        self.target_var = tk.StringVar(
            value=str(num_cols[0]) if num_cols else "")
        cb_target = ttk.Combobox(right, textvariable=self.target_var,
                                  values=[str(c) for c in num_cols],
                                  state="readonly")
        cb_target.pack(fill="x", pady=(4, 12))
        cb_target.bind("<<ComboboxSelected>>",
                       lambda e: self._on_target_change())

        # --- Variables explicativas ---
        hdr2 = tk.Frame(right, bg=COLORS["bg"])
        hdr2.pack(fill="x")
        tk.Label(hdr2, text="Variables explicativas", bg=COLORS["bg"],
                 fg=COLORS["text"], font=FONTS["h3"]).pack(side="left")
        tk.Label(hdr2, text="(marca las que quieras incluir)",
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"]).pack(side="left", padx=(6, 0))

        vars_wrap = tk.Frame(right, bg=COLORS["bg_card"],
                             highlightbackground=COLORS["border"],
                             highlightthickness=1)
        vars_wrap.pack(fill="x", pady=(4, 12))

        v_canvas = tk.Canvas(vars_wrap, bg=COLORS["bg_card"],
                             highlightthickness=0, height=240)
        v_scroll = ttk.Scrollbar(vars_wrap, orient="vertical",
                                 command=v_canvas.yview)
        self.vars_frame = tk.Frame(v_canvas, bg=COLORS["bg_card"])
        self.vars_frame.bind(
            "<Configure>",
            lambda e: v_canvas.configure(scrollregion=v_canvas.bbox("all")))
        v_win = v_canvas.create_window((0, 0), window=self.vars_frame,
                                        anchor="nw")
        v_canvas.configure(yscrollcommand=v_scroll.set)
        v_canvas.bind("<Configure>",
                      lambda e: v_canvas.itemconfig(v_win, width=e.width))
        v_canvas.pack(side="left", fill="both", expand=True,
                      padx=6, pady=6)
        v_scroll.pack(side="right", fill="y")

        # --- Eventos registrados ---
        head_row = tk.Frame(right, bg=COLORS["bg"])
        head_row.pack(fill="x", pady=(0, 4))
        tk.Label(head_row, text="Eventos registrados",
                 bg=COLORS["bg"], fg=COLORS["text"],
                 font=FONTS["h3"]).pack(side="left")
        self.lbl_anom_count = tk.Label(head_row, text="",
                                        bg=COLORS["bg"],
                                        fg=COLORS["text_dim"],
                                        font=FONTS["tiny"])
        self.lbl_anom_count.pack(side="right")

        ev_wrap = tk.Frame(right, bg=COLORS["bg_card"],
                           highlightbackground=COLORS["border"],
                           highlightthickness=1)
        ev_wrap.pack(fill="x", pady=(4, 12))

        self.events_frame = tk.Frame(ev_wrap, bg=COLORS["bg_card"])
        self.events_frame.pack(fill="x", padx=6, pady=6)

    # ---------------- Pestaña Historial ----------------
    def _build_history_tab(self):
        head = tk.Frame(self.tab_hist, bg=COLORS["bg"])
        head.pack(fill="x", padx=20, pady=(16, 8))

        tk.Label(head, text="Ejecuciones anteriores",
                 bg=COLORS["bg"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")

        ttk.Button(head, text="🗑  Limpiar historial",
                   command=self._clear_history).pack(side="right")

        self.hist_frame = tk.Frame(self.tab_hist, bg=COLORS["bg"])
        self.hist_frame.pack(fill="both", expand=True, padx=20, pady=(0, 16))

    def _refresh_history(self):
        for w in self.hist_frame.winfo_children():
            w.destroy()

        if not self.history:
            tk.Label(self.hist_frame,
                     text="Aún no hay ejecuciones registradas.\n"
                          "Cada vez que pulses 'Estimar modelo' se guardará aquí.",
                     bg=COLORS["bg"], fg=COLORS["text_dim"],
                     font=FONTS["body"], justify="left").pack(
                anchor="w", pady=20)
            return

        # Cabecera de tabla
        hdr = tk.Frame(self.hist_frame, bg=COLORS["bg_card_top"])
        hdr.pack(fill="x")

        cols = [("Hora", 18), ("Método", 28), ("Target", 22),
                ("Vars", 6), ("Anom", 6), ("R²", 8), ("MAPE", 8), ("", 14)]
        for name, width in cols:
            tk.Label(hdr, text=name, bg=COLORS["bg_card_top"],
                     fg=COLORS["text_muted"], font=FONTS["tiny"],
                     width=width, anchor="w").pack(side="left", padx=2, pady=4)

        # Filas
        for i, entry in enumerate(self.history[:30]):
            bg = COLORS["bg_card"] if i % 2 == 0 else COLORS["bg_card_alt"]
            row = tk.Frame(self.hist_frame, bg=bg)
            row.pack(fill="x")

            values = [
                str(entry.get("timestamp", ""))[:16],
                str(entry.get("regression_type", ""))[:28],
                str(entry.get("target_col", ""))[:22],
                str(len(entry.get("input_cols", []))),
                str(len(entry.get("anomaly_cols", []))),
                f"{entry.get('r2', 0):.3f}",
                f"{entry.get('mape', 0):.1f}%",
            ]
            for (_, width), v in zip(cols[:-1], values):
                tk.Label(row, text=v, bg=bg, fg=COLORS["text"],
                         font=FONTS["tiny"], width=width,
                         anchor="w").pack(side="left", padx=2, pady=3)

            ttk.Button(row, text="↻ Restaurar",
                       command=lambda e=entry: self._restore_entry(e)).pack(
                side="left", padx=4, pady=1)

    def _restore_entry(self, entry: dict):
        self.reg_type_var.set(entry.get("regression_type", REGRESSION_TYPES[0]))
        self._update_method_note()
        self.target_var.set(entry.get("target_col", self.target_var.get()))
        # Restaurar variables
        for name, cfg in self.var_cfg.items():
            cfg["visible"] = name in entry.get("input_cols", [])
        self._refresh_variables()
        self._refresh_chart()
        self._refresh_global_status()
        self.nb.select(self.tab_cfg)
        self._toast_local("Configuración restaurada", "info")

    def _clear_history(self):
        if messagebox.askyesno("Limpiar historial",
                                "¿Borrar todas las ejecuciones guardadas?",
                                parent=self):
            clear_regression_history(self.dataset_name)
            self.history = []
            self._refresh_history()

    # ==============================================================
    # PERSISTENCIA
    # ==============================================================
    def _restore_last_config(self):
        cfg = load_last_regression_config(self.dataset_name)
        if not cfg:
            return
        try:
            if cfg.get("regression_type"):
                self.reg_type_var.set(cfg["regression_type"])
            if cfg.get("date_col"):
                self.date_var.set(cfg["date_col"])
            if cfg.get("target_col"):
                self.target_var.set(cfg["target_col"])
            for v in cfg.get("variables", []):
                self.var_cfg[v["name"]] = {
                    "visible": v.get("visible", False),
                    "type": v.get("type", "Línea"),
                    "color": v.get("color", "#58A6FF"),
                }
        except Exception:
            pass

    def _save_last_config(self):
        try:
            save_last_regression_config(self.dataset_name, {
                "regression_type": self.reg_type_var.get(),
                "date_col": self.date_var.get(),
                "target_col": self.target_var.get(),
                "variables": [
                    {"name": n, **cfg} for n, cfg in self.var_cfg.items()
                ],
            })
        except Exception:
            pass

    # ==============================================================
    # ESTADO GLOBAL
    # ==============================================================
    def _refresh_global_status(self):
        inputs = [name for name, cfg in self.var_cfg.items()
                  if cfg.get("visible") and name in self.df_full.columns]
        regression_type = getattr(self, "reg_type_var", None)
        is_its = bool(
            regression_type is not None
            and regression_type.get().lower().startswith("evento / its"))
        n_obs = len(self.df_full)
        txt = f"{len(inputs)} variable(s)  ·  {n_obs:,} observaciones"
        self.lbl_global_status.config(text=txt, fg=COLORS["text"])
        errors = []
        if self.target_var.get() not in self.df_full.columns:
            errors.append("Selecciona la variable objetivo")
        if not inputs and not is_its:
            errors.append("Marca una variable explicativa")
        if is_its and not getattr(self, "events", {}).get("sub_events"):
            errors.append("Marca un único evento para ITS")
        if n_obs < 30:
            errors.append("Se necesitan al menos 30 observaciones")
        if errors:
            short = " · ".join(errors[:2])
            if len(errors) > 2:
                short += f" (+{len(errors)-2})"
            self.lbl_footer_status.config(
                text=f"❌ {short}", fg=COLORS["danger"])
            self.btn_run.state(["disabled"])
        else:
            self.lbl_footer_status.config(
                text="✓ Listo para estimar",
                fg=COLORS["success"])
            self.btn_run.state(["!disabled"])
    # ==============================================================
    # PRE-FLIGHT CHECK
    # ==============================================================
    def _preflight_check_silent(self) -> tuple[list, list, dict]:
        """
        Comprueba la configuración antes de ejecutar.
        Devuelve (errors, warnings, summary).
        """
        errors, warnings = [], []

        # ---------- Target ----------
        target = self.target_var.get()
        if not target:
            errors.append("No hay variable objetivo seleccionada.")
        elif target not in self.df.columns:
            errors.append(
                f"La columna objetivo '{target}' no existe en la tabla.")

        # ---------- Predictores marcados ----------
        inputs = [n for n, c in self.var_cfg.items()
                  if c.get("visible") and n in self.df.columns]
        is_its = self.reg_type_var.get().lower().startswith("evento / its")

        if not inputs and not is_its:
            errors.append("Marca al menos una variable explicativa.")
        if is_its and not self.events.get("sub_events"):
            errors.append("Marca un único evento/grupo para ITS.")

        # ---------- Nº de observaciones ----------
        n_obs = len(self.df_full)
        if n_obs < 30:
            errors.append(
                f"Solo {n_obs} observaciones. Se necesitan ≥30.")
        elif n_obs < 100:
            warnings.append(
                f"Pocas observaciones ({n_obs}). Los coeficientes "
                "pueden ser inestables.")

        # ---------- Constantes ----------
        for name in inputs:
            try:
                s = pd.to_numeric(self.df[name], errors="coerce")
                if s.std(ddof=0) < 1e-12:
                    warnings.append(
                        f"'{name}' es constante. No aporta al modelo.")
            except Exception:
                pass

        # ---------- NaN excesivos ----------
        for name in inputs:
            try:
                s = pd.to_numeric(self.df[name], errors="coerce")
                pct = s.isna().mean() * 100
                if pct > 20:
                    warnings.append(
                        f"'{name}' tiene {pct:.0f}% de NaN.")
            except Exception:
                pass

        # ---------- Colinealidad (VIF) ----------
        if len(inputs) >= 2:
            try:
                vif_map = self._compute_vif(inputs)
                for name, v in vif_map.items():
                    if not np.isfinite(v):
                        warnings.append(
                            f"'{name}' tiene VIF infinito "
                            "(colinealidad perfecta).")
                    elif v > 20:
                        warnings.append(
                            f"'{name}' tiene VIF {v:.1f} → colinealidad severa.")
                    elif v > 10:
                        warnings.append(
                            f"'{name}' tiene VIF {v:.1f} → colinealidad alta.")
            except Exception:
                pass

        # ---------- Anomalías que cubren demasiado ----------
        try:
            anom_cols = generate_anomaly_columns(
                self.events, self.df, self.date_var.get())
            if anom_cols:
                for col, series in anom_cols.items():
                    frac = (series > 0).mean()
                    if frac > 0.4:
                        warnings.append(
                            f"'{col}' cubre {frac*100:.0f}% del periodo.")
        except Exception:
            pass

        # ---------- Summary para el pre-flight dialog ----------
        summary = {
            "Variable objetivo": target or "—",
            "Variables explicativas": len(inputs),
            "Eventos marcados": len(self.events.get("sub_events", [])),
            "Observaciones": n_obs,
            "Método": self.reg_type_var.get(),
        }

        return errors, warnings, summary


    # ==============================================================
    # VARIABLES DE ENTRADA
    # ==============================================================
    def _refresh_variables(self):
        for w in self.vars_frame.winfo_children():
            w.destroy()

        num_cols = self.df.select_dtypes("number").columns.tolist()
        target = self.target_var.get()

        selected = [n for n, c in self.var_cfg.items()
                    if c.get("visible") and n in self.df.columns]
        vif_map = {}
        if len(selected) >= 2:
            try:
                vif_map = self._compute_vif(selected)
            except Exception:
                vif_map = {}

        corr_map = {}
        if target and target in self.df.columns:
            y = pd.to_numeric(self.df[target], errors="coerce")
            for name in num_cols:
                if name == target:
                    continue
                try:
                    x = pd.to_numeric(self.df[name], errors="coerce")
                    corr_map[name] = x.corr(y)
                except Exception:
                    corr_map[name] = float("nan")

        # Grid
        self.vars_frame.columnconfigure(1, weight=1, minsize=110)
        self.vars_frame.columnconfigure(2, weight=0, minsize=60)
        self.vars_frame.columnconfigure(3, weight=0, minsize=45)
        self.vars_frame.columnconfigure(4, weight=0, minsize=45)
        self.vars_frame.columnconfigure(5, weight=0, minsize=24)

        headers = ["", "Variable", "Tipo", "r", "VIF", ""]
        for j, h in enumerate(headers):
            tk.Label(self.vars_frame, text=h,
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=FONTS["tiny"], anchor="w").grid(
                row=0, column=j, sticky="w", padx=2, pady=(0, 4))

        row_idx = 1
        for i, c in enumerate(num_cols):
            name = str(c)
            if name == target:
                continue

            if name not in self.var_cfg:
                self.var_cfg[name] = {
                    "visible": False,
                    "type": "Línea",
                    "color": CHART_PALETTE[i % len(CHART_PALETTE)],
                }
            cfg = self.var_cfg[name]
            bg = COLORS["bg_card"]

            vis_var = tk.BooleanVar(value=cfg["visible"])

            def _on_vis(v=vis_var, n=name):
                self.var_cfg[n]["visible"] = v.get()
                self._refresh_chart()
                self._refresh_global_status()

            cb = ttk.Checkbutton(self.vars_frame, variable=vis_var,
                                  command=_on_vis)
            cb.grid(row=row_idx, column=0, sticky="w", padx=2, pady=1)
            self._tooltips.append(Tooltip(
                cb, f"Marca para incluir '{name}' como variable explicativa."))

            tk.Label(self.vars_frame, text=name, bg=bg, fg=COLORS["text"],
                     font=FONTS["small"], anchor="w").grid(
                row=row_idx, column=1, sticky="ew", padx=2, pady=1)

            type_var = tk.StringVar(value=cfg["type"])

            def _on_type(_e=None, n=name, v=type_var):
                self.var_cfg[n]["type"] = v.get()
                self._refresh_chart()

            cb_type = ttk.Combobox(self.vars_frame, textvariable=type_var,
                                    values=CHART_TYPES, state="readonly",
                                    width=7)
            cb_type.grid(row=row_idx, column=2, padx=2, pady=1)
            cb_type.bind("<<ComboboxSelected>>", _on_type)

            # r
            r = corr_map.get(name, float("nan"))
            if r == r:
                r_txt = f"{r:+.2f}"
                if abs(r) >= 0.7:
                    r_col = COLORS["danger"]
                elif abs(r) >= 0.4:
                    r_col = COLORS["warning"]
                else:
                    r_col = COLORS["text_muted"]
            else:
                r_txt, r_col = "—", COLORS["text_dim"]
            lbl_r = tk.Label(self.vars_frame, text=r_txt, bg=bg, fg=r_col,
                             font=FONTS["small"], anchor="e")
            lbl_r.grid(row=row_idx, column=3, sticky="e", padx=2, pady=1)
            self._tooltips.append(Tooltip(
                lbl_r,
                "Correlación de Pearson con la variable a predecir.\n"
                "|r| > 0.7 sugiere relación fuerte, pero no causalidad."))

            # VIF
            if name in vif_map:
                v = vif_map[name]
                if v != v:
                    vif_txt, vif_col = "—", COLORS["text_dim"]
                elif not np.isfinite(v):
                    vif_txt, vif_col = "∞", COLORS["danger"]
                else:
                    vif_txt = f"{v:.1f}"
                    if v > 10:
                        vif_col = COLORS["danger"]
                    elif v > 5:
                        vif_col = COLORS["warning"]
                    else:
                        vif_col = COLORS["success"]
            elif name in selected:
                vif_txt, vif_col = "—", COLORS["text_dim"]
            else:
                vif_txt, vif_col = "", COLORS["text_dim"]

            lbl_vif = tk.Label(self.vars_frame, text=vif_txt, bg=bg,
                               fg=vif_col, font=FONTS["small"], anchor="e")
            lbl_vif.grid(row=row_idx, column=4, sticky="e", padx=2, pady=1)
            self._tooltips.append(Tooltip(
                lbl_vif,
                "VIF: factor de inflación de la varianza.\n"
                "> 10 → colinealidad alta con otras variables marcadas."))

            # Swatch
            swatch = tk.Frame(self.vars_frame, bg=cfg["color"],
                              width=16, height=16,
                              highlightbackground=COLORS["border"],
                              highlightthickness=1)
            swatch.grid(row=row_idx, column=5, padx=4, pady=1)
            swatch.grid_propagate(False)

            def _pick_color(n=name, sw=swatch):
                from tkinter import colorchooser
                cc = colorchooser.askcolor(color=self.var_cfg[n]["color"],
                                            title=f"Color de {n}", parent=self)
                if cc and cc[1]:
                    self.var_cfg[n]["color"] = cc[1]
                    sw.configure(bg=cc[1])
                    self._refresh_chart()
            swatch.bind("<Button-1>", lambda e, f=_pick_color: f())
            swatch.configure(cursor="hand2")

            row_idx += 1

    def _compute_vif(self, cols):
        if not cols or len(cols) < 2:
            return {}
        try:
            X = pd.DataFrame(index=self.df.index)
            for c in cols:
                X[c] = pd.to_numeric(self.df[c], errors="coerce")
        except Exception:
            return {}
        X_clean = X.dropna()
        if len(X_clean) < 10:
            X_clean = X.copy()
            for c in X_clean.columns:
                m = X_clean[c].mean()
                X_clean[c] = X_clean[c].fillna(m if np.isfinite(m) else 0)
        if len(X_clean) < 5:
            return {}
        Xv = X_clean.values.astype(float)
        vifs = {}
        for i, col in enumerate(X_clean.columns):
            y = Xv[:, i]
            Xo = np.delete(Xv, i, axis=1)
            if np.std(y) < 1e-12:
                vifs[col] = 1.0
                continue
            Xo_ = np.column_stack([np.ones(len(Xo)), Xo])
            try:
                coef, *_ = np.linalg.lstsq(Xo_, y, rcond=None)
                y_pred = Xo_ @ coef
                ss_res = float(np.sum((y - y_pred) ** 2))
                ss_tot = float(np.sum((y - y.mean()) ** 2))
                if ss_tot < 1e-12:
                    vifs[col] = 1.0
                    continue
                r2 = 1.0 - ss_res / ss_tot
                r2 = max(0.0, min(r2, 1.0 - 1e-12))
                vifs[col] = 1.0 / (1.0 - r2)
            except Exception:
                vifs[col] = float("nan")
        return vifs

    # ==============================================================
    # GRÁFICO
    # ==============================================================
    def _refresh_chart(self):
        self.fig.clear()
        self._chart_dates = None
        date_col = self.date_var.get()
        target = self.target_var.get()

        if not target or target not in self.df.columns:
            self.canvas.draw_idle()
            return

        chart_df = self.df
        use_dates = bool(date_col and date_col in chart_df.columns)
        date_series = None
        if use_dates:
            chart_df, date_series = ordered_temporal_chart_frame(
                chart_df, date_col)
            use_dates = date_series is not None
        chart_df = self._sample_for_chart(chart_df)
        if use_dates:
            date_series = date_series.iloc[chart_df.index].reset_index(drop=True)
            self._chart_dates = date_series

        n_pts = len(chart_df)
        if n_pts == 0:
            self.canvas.draw_idle()
            return

        x_num = np.arange(n_pts)

        y_target = pd.to_numeric(chart_df[target], errors="coerce").to_numpy()

        visible_vars = [(n, c) for n, c in self.var_cfg.items()
                        if c.get("visible") and n in chart_df.columns]

        norm_data = {}
        for name, _cfg in visible_vars:
            y = pd.to_numeric(chart_df[name], errors="coerce")
            mu, sd = y.mean(), y.std(ddof=0)
            if np.isfinite(sd) and sd > 1e-12:
                z = ((y - mu) / sd).fillna(0).values
            else:
                z = np.zeros(n_pts)
            norm_data[name] = z

        ax = self.fig.add_subplot(111)
        ax2 = ax.twinx() if visible_vars else None

        # Variables en eje derecho
        if ax2 is not None:
            for name, cfg in visible_vars:
                z = norm_data[name]
                color = self._safe_color(cfg["color"])
                tipo = cfg["type"]

                if tipo == "Línea":
                    ax2.plot(x_num, z, color=color, lw=1.6,
                             alpha=0.95, label=name, zorder=3)
                elif tipo == "Área":
                    ax2.fill_between(x_num, 0, z, color=color,
                                     alpha=0.25, zorder=2)
                    ax2.plot(x_num, z, color=color, lw=1.6,
                             alpha=0.95, label=name, zorder=3)
                elif tipo == "Barra":
                    ax2.bar(x_num, z, color=color, alpha=0.6,
                            label=name, width=0.8, zorder=3)
                elif tipo == "Dispersión":
                    ax2.scatter(x_num, z, color=color, s=10,
                                alpha=0.85, label=name, zorder=3)

            ax2.set_ylabel("Variables normalizadas (z-score)",
                           color="#8B949E", fontsize=9)
            ax2.tick_params(axis="y", labelsize=8, colors="#8B949E")

            try:
                all_z = np.concatenate(
                    [norm_data[n] for n, _ in visible_vars if n in norm_data])
                zmin, zmax = float(np.nanmin(all_z)), float(np.nanmax(all_z))
            except Exception:
                zmin, zmax = -3.0, 3.0
            pad = max(0.5, (zmax - zmin) * 0.15)
            ax2.set_ylim(min(-0.5, zmin - pad), max(0.5, zmax + pad))
            ax2.axhline(0, color="#8B949E", lw=0.6,
                        linestyle="--", alpha=0.5, zorder=1)
            ax2.set_zorder(1)

        # Target en eje izquierdo
        TARGET_COLOR = "#FFE066"
        ax.plot(x_num, y_target, color=TARGET_COLOR, lw=2.6,
                marker="o", markersize=3, markeredgewidth=0,
                label=target, zorder=20)
        ax.set_zorder(3)
        ax.patch.set_visible(False)
        ax.set_ylabel(target, color=TARGET_COLOR, fontsize=9,
                      fontweight="bold")
        ax.tick_params(axis="y", labelsize=8, colors=TARGET_COLOR)
        ax.set_title(f"Serie · {target} vs variables normalizadas",
                     fontsize=11, fontweight="bold")

        # Fechas en eje X
        if use_dates and date_series is not None:
            self._apply_date_ticks(ax, date_series, n_pts)
        else:
            step = max(1, n_pts // 10)
            ax.set_xticks(x_num[::step])

        # Anomalías
        if self.show_anomalies.get() and use_dates and date_series is not None:
            for se in self.events.get("sub_events", []):
                try:
                    s = pd.Timestamp(se["start"])
                    e = pd.Timestamp(se["end"])
                except Exception:
                    continue
                mask = ((date_series >= s) & (date_series <= e)).values
                if not mask.any():
                    continue
                idxs = np.where(mask)[0]
                x0, x1 = idxs[0] - 0.5, idxs[-1] + 0.5
                g = se.get("group", "")
                own = se.get("own", False)
                color = self.events["groups"].get(g, {}).get("color",
                                                              "#F85149")
                ax.axvspan(x0, x1, color=color,
                           alpha=0.25 if own else 0.12, zorder=0)

        if self.show_grid.get():
            ax.grid(True, alpha=0.25)

        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ([], [])
        if ax2 is not None:
            h2, l2 = ax2.get_legend_handles_labels()
        handles, labels = h1 + h2, l1 + l2
        if handles:
            leg = ax.legend(handles, labels, loc="upper left",
                            fontsize=8, ncol=2, framealpha=0.9,
                            facecolor="#1A2029", edgecolor="#333B45")
            for t in leg.get_texts():
                t.set_color("#E6EDF3")

        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _apply_date_ticks(self, ax, date_series, n_pts):
        try:
            valid = date_series.notna()
            if not valid.any():
                return
            n_ticks = min(8, n_pts)
            positions = np.linspace(0, n_pts - 1, n_ticks).astype(int)
            positions = [p for p in positions if 0 <= p < n_pts]
            labels = []
            for p in positions:
                if valid.iloc[p]:
                    labels.append(date_series.iloc[p].strftime("%Y-%m-%d"))
                else:
                    labels.append("")
            ax.set_xticks(positions)
            rotation = 25 if n_ticks > 5 else 0
            ax.set_xticklabels(labels, rotation=rotation,
                               ha="right" if rotation else "center",
                               fontsize=8)
        except Exception:
            pass

    def _safe_color(self, color: str) -> str:
        if not color or not isinstance(color, str) or not color.startswith("#"):
            return "#58A6FF"
        try:
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
        except Exception:
            return "#58A6FF"
        bg_r, bg_g, bg_b = 10, 13, 18
        dist = ((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2) ** 0.5
        if dist < 80:
            return "#58A6FF"
        return color

    # ==============================================================
    # MARCADO DE EVENTOS
    # ==============================================================
    def _toggle_marking(self):
        self.marking_mode = not self.marking_mode
        if self.marking_mode:
            self.btn_mark.config(
                text="🔴 MODO MARCADO · arrastra y suelta",
                bg=COLORS["danger"], fg="#FFFFFF")
            self._toast_local(
                "Modo marcado ACTIVO · arrastra sobre el gráfico "
                "para crear eventos. Pulsa de nuevo para salir.",
                "info")
        else:
            self.btn_mark.config(
                text="🔵 Marcar eventos",
                bg=COLORS["bg_input"], fg=COLORS["text"])
    def _on_press(self, event):
        if not self.marking_mode or event.inaxes is None or event.xdata is None:
            return
        self.drag_start = float(event.xdata)

    def _on_motion(self, event):
        if self.drag_start is None or event.inaxes is None or event.xdata is None:
            return
        self._draw_preview(self.drag_start, float(event.xdata))

    def _on_release(self, event):
        if self.drag_start is None:
            return
        if event.inaxes is None or event.xdata is None:
            self.drag_start = None
            return
        end = float(event.xdata)
        start = self.drag_start
        self.drag_start = None
        if abs(end - start) < 1e-6:
            return
        s, e = sorted([start, end])
        self._create_event_quick(s, e)

    def _create_event_quick(self, x_start, x_end):
        """Crea un evento directamente desde el arrastre, sin diálogo."""
        date_col = self.date_var.get()
        if not date_col or date_col not in self.df.columns:
            return
        # El eje X usa la serie ordenada y muestreada dibujada, no self.df.
        if self._chart_dates is None:
            return
        start_ts, end_ts = event_dates_from_indices(
            pd.DataFrame({date_col: self._chart_dates}),
            date_col, x_start, x_end)
        if start_ts is None or end_ts is None:
            return

        # Nombre automático: Evento N
        n_existing = len(self.events.get("sub_events", []))
        auto_name = f"Evento {n_existing + 1}"

        try:
            add_sub_event(self.events,
                           name=auto_name,
                           start=start_ts,
                           end=end_ts,
                           intensity=1.0,
                           group="",
                           own=False)
            save_events(self.dataset_name, self.events)
        except Exception as e:
            messagebox.showerror("Error al guardar evento", str(e), parent=self)
            return

        self._refresh_events_list()
        self._refresh_chart()
        self._refresh_global_status()
        self._toast_local(
            f"'{auto_name}' añadido · {start_ts.date()} → {end_ts.date()}",
            "success")

    def _draw_preview(self, x0, x1):
        s, e = sorted([x0, x1])
        self._refresh_chart()
        ax = self.fig.axes[0] if self.fig.axes else None
        if ax is not None:
            ax.axvspan(s, e, alpha=0.35, color="#FFFFFF", zorder=4)
        self.canvas.draw_idle()

    def _open_manual_event_dialog(self):
        """Diálogo para añadir un evento escribiendo fechas manualmente."""
        date_col = self.date_var.get()
        if not date_col or date_col not in self.df.columns:
            messagebox.showwarning(
                "Sin fecha",
                "Selecciona primero una columna de fecha.", parent=self)
            return

        # Rango disponible
        try:
            dates = pd.to_datetime(self.df[date_col], errors="coerce").dropna()
            if dates.empty:
                messagebox.showwarning(
                    "Sin fechas",
                    "La columna de fecha no tiene valores válidos.", parent=self)
                return
            dmin = dates.min().date()
            dmax = dates.max().date()
        except Exception:
            dmin = dmax = None

        dlg = tk.Toplevel(self)
        dlg.title("Añadir evento manualmente")
        dlg.configure(bg=COLORS["bg"])
        dlg.transient(self)
        dlg.grab_set()
        center_popup(dlg, self, 520, 460)

        # --- Header ---
        tk.Label(dlg, text="Añadir evento manualmente",
                 bg=COLORS["bg"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(anchor="w", padx=16,
                                        pady=(12, 4))
        tk.Label(dlg,
                 text=("Selecciona el rango de fechas en el que se "
                       "activa la variable dummy del evento."),
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["small"], justify="left",
                 wraplength=480).pack(anchor="w", padx=16,
                                       pady=(0, 12))

        form = tk.Frame(dlg, bg=COLORS["bg"])
        form.pack(fill="x", padx=16)
        form.columnconfigure(1, weight=1)

        # --- Nombre ---
        tk.Label(form, text="Nombre:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).grid(row=0, column=0,
                                            sticky="w", pady=6)
        name_var = tk.StringVar(
            value=f"Evento {len(self.events.get('sub_events', [])) + 1}")
        ttk.Entry(form, textvariable=name_var).grid(
            row=0, column=1, columnspan=2, sticky="ew",
            pady=6, padx=(6, 0))

        # --- Fecha inicio ---
        tk.Label(form, text="Fecha inicio:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).grid(row=1, column=0,
                                            sticky="w", pady=6)
        start_var = tk.StringVar(
            value=str(dmin) if dmin else "2025-01-01")
        ttk.Entry(form, textvariable=start_var).grid(
            row=1, column=1, sticky="ew", pady=6, padx=(6, 0))
        tk.Label(form, text="(YYYY-MM-DD)",
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"]).grid(row=1, column=2,
                                          sticky="w", padx=(6, 0))

        # --- Fecha fin ---
        tk.Label(form, text="Fecha fin:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).grid(row=2, column=0,
                                            sticky="w", pady=6)
        end_var = tk.StringVar(
            value=str(dmax) if dmax else "2025-01-31")
        ttk.Entry(form, textvariable=end_var).grid(
            row=2, column=1, sticky="ew", pady=6, padx=(6, 0))
        tk.Label(form, text="(YYYY-MM-DD)",
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"]).grid(row=2, column=2,
                                          sticky="w", padx=(6, 0))

        if dmin and dmax:
            tk.Label(form,
                     text=f"Rango disponible: {dmin} → {dmax}",
                     bg=COLORS["bg"], fg=COLORS["text_dim"],
                     font=FONTS["tiny"]).grid(
                row=3, column=1, columnspan=2,
                sticky="w", pady=(0, 6))

        # --- Grupo ---
        tk.Label(form, text="Grupo:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).grid(row=4, column=0,
                                            sticky="w", pady=6)

        existing_groups = sorted(self.events.get("groups", {}).keys())
        group_var = tk.StringVar(value="(sin grupo)")
        group_cb = ttk.Combobox(
            form, textvariable=group_var,
            values=["(sin grupo)"] + existing_groups,
            state="normal", width=22)
        group_cb.grid(row=4, column=1, sticky="ew", pady=6, padx=(6, 0))

        def _new_group():
            nm = simpledialog.askstring("Nuevo grupo",
                                         "Nombre del grupo:",
                                         parent=dlg)
            if nm and nm.strip():
                nm = nm.strip()
                create_group(self.events, nm)
                save_events(self.dataset_name, self.events)
                group_cb["values"] = ["(sin grupo)"] + sorted(
                    self.events["groups"].keys())
                group_var.set(nm)

        ttk.Button(form, text="+ Nuevo", width=9,
                   command=_new_group).grid(
            row=4, column=2, sticky="e", pady=6, padx=(4, 0))

        tk.Label(form,
                 text=("'(sin grupo)' = variable independiente. "
                       "'+ Nuevo' = crear un grupo."),
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], anchor="w").grid(
            row=5, column=1, columnspan=2, sticky="w",
            pady=(0, 6))

        # --- Intensidad ---
        tk.Label(form, text="Intensidad:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).grid(row=6, column=0,
                                            sticky="w", pady=6)
        intensity_var = tk.StringVar(value="1")
        ttk.Entry(form, textvariable=intensity_var,
                   width=10).grid(row=6, column=1, sticky="w",
                                   pady=6, padx=(6, 0))

        # --- Propia ---
        own_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            form,
            text="Tratar como variable independiente",
            variable=own_var).grid(
            row=7, column=0, columnspan=3, sticky="w",
            pady=(10, 0))

        # --- Botones ---
        def _ok():
            try:
                start_ts = pd.Timestamp(start_var.get().strip())
                end_ts = pd.Timestamp(end_var.get().strip())
            except Exception as e:
                messagebox.showerror(
                    "Fechas inválidas",
                    f"No se pudieron interpretar las fechas.\n"
                    f"Formato esperado: YYYY-MM-DD\n\n{e}", parent=dlg)
                return

            if end_ts < start_ts:
                start_ts, end_ts = end_ts, start_ts

            try:
                intensity = float(
                    str(intensity_var.get()).replace(",", "."))
            except Exception:
                intensity = 1.0

            name = name_var.get().strip() or "Evento"
            g = group_var.get().strip()
            if g == "(sin grupo)":
                g = ""

            try:
                add_sub_event(self.events, name, start_ts, end_ts,
                              intensity=intensity, group=g,
                              own=own_var.get())
                save_events(self.dataset_name, self.events)
            except Exception as e:
                messagebox.showerror("Error al guardar", str(e), parent=dlg)
                return

            dlg.destroy()
            self._refresh_events_list()
            self._refresh_chart()
            self._refresh_global_status()

        btn_row = tk.Frame(dlg, bg=COLORS["bg"])
        btn_row.pack(fill="x", side="bottom", pady=12, padx=16)
        ttk.Button(btn_row, text="Cancelar",
                   command=dlg.destroy).pack(side="right",
                                              padx=(6, 0))
        ttk.Button(btn_row, text="✓  Añadir",
                   style="Primary.TButton",
                   command=_ok).pack(side="right")

    # ==============================================================
    # ZOOM
    # ==============================================================
    def _on_scroll(self, event):
        if event.inaxes is None or event.xdata is None or event.ydata is None:
            return
        step = 1 if getattr(event, "step", 0) > 0 else -1
        scale = 1.0 / 1.2 if step > 0 else 1.2
        ax = event.inaxes
        x, y = float(event.xdata), float(event.ydata)
        xlim = ax.get_xlim()
        new_x0 = x - (x - xlim[0]) * scale
        new_x1 = x + (xlim[1] - x) * scale
        for a in self.fig.axes:
            try:
                a.set_xlim(new_x0, new_x1)
            except Exception:
                pass
        try:
            ylim = ax.get_ylim()
            ax.set_ylim(y - (y - ylim[0]) * scale, y + (ylim[1] - y) * scale)
        except Exception:
            pass
        event.canvas.draw_idle()

    def _on_double_click(self, event):
        if not getattr(event, "dblclick", False):
            return
        if event.inaxes is None:
            return
        for a in self.fig.axes:
            try:
                a.relim()
                a.autoscale(enable=True, axis="both", tight=False)
            except Exception:
                pass
        event.canvas.draw_idle()

    # ==============================================================
    # LISTA DE EVENTOS
    # ==============================================================
    def _refresh_events_list(self):
        for w in self.events_frame.winfo_children():
            w.destroy()

        subs = self.events.get("sub_events", [])
        self.lbl_anom_count.config(text=f"{len(subs)} eventos")

        if not subs:
            tk.Label(self.events_frame,
                     text="(ninguno · arrastra sobre el gráfico "
                          "o pulsa '➕ Añadir evento')",
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=FONTS["small"], justify="left",
                     wraplength=250).pack(padx=8, pady=8)
            return

        # Botón deshacer último
        top = tk.Frame(self.events_frame, bg=COLORS["bg_card"])
        top.pack(fill="x", pady=(0, 4))
        ttk.Button(top, text="↩ Deshacer último",
                   command=self._undo_last_event).pack(side="left")
        ttk.Button(top, text="🗑 Vaciar",
                   command=self._clear_all_events).pack(side="left",
                                                          padx=(6, 0))

        # Agrupamos por grupo
        by_group = {}
        for se in subs:
            g = se.get("group") or ""
            by_group.setdefault(g, []).append(se)

        for g_name, items in by_group.items():
            color = (self.events["groups"].get(g_name, {})
                     .get("color", "#8B949E") if g_name else "#8B949E")

            header = tk.Frame(self.events_frame, bg=COLORS["bg_card"])
            header.pack(fill="x", pady=(6, 2))
            tk.Frame(header, bg=color, width=4, height=14).pack(
                side="left", padx=(0, 6))
            tk.Label(header,
                     text=f"{g_name or 'Sin grupo'} ({len(items)})",
                     bg=COLORS["bg_card"], fg=COLORS["text"],
                     font=FONTS["h3"]).pack(side="left")

            for se in items:
                row = tk.Frame(self.events_frame, bg=COLORS["bg_card"])
                row.pack(fill="x", padx=(20, 4), pady=1)

                tag = " [★]" if se.get("own", False) else ""
                txt = f"{se['name']}{tag}\n{se['start']} → {se['end']}"

                # Nombre clicable para renombrar
                lbl = tk.Label(row, text=txt, bg=COLORS["bg_card"],
                               fg=COLORS["text_muted"],
                               font=FONTS["tiny"], anchor="w",
                               justify="left", wraplength=250,
                               cursor="hand2")
                lbl.pack(side="left", fill="x", expand=True)
                lbl.bind("<Button-1>",
                         lambda e, sid=se["id"],
                         n=se["name"]: self._rename_event(sid, n))

                tk.Button(row, text="×", relief="flat",
                          bg=COLORS["bg_card"], fg=COLORS["danger"],
                          activebackground=COLORS["bg_hover"],
                          font=FONTS["small"], cursor="hand2",
                          command=lambda sid=se["id"]:
                              self._delete_event(sid)).pack(side="right")

                tk.Button(row, text="★", relief="flat",
                          bg=COLORS["bg_card"],
                          fg=COLORS["primary"] if se.get("own", False)
                          else COLORS["text_dim"],
                          activebackground=COLORS["bg_hover"],
                          font=FONTS["small"], cursor="hand2",
                          command=lambda sid=se["id"]:
                              self._toggle_own(sid)).pack(side="right")
    def _toggle_own(self, sub_id):
        toggle_own(self.events, sub_id)
        save_events(self.dataset_name, self.events)
        self._refresh_events_list()
        self._refresh_chart()

    def _delete_event(self, sub_id):
        remove_sub_event(self.events, sub_id)
        save_events(self.dataset_name, self.events)
        self._refresh_events_list()
        self._refresh_chart()
        self._refresh_global_status()

    def _undo_last_event(self):
        subs = self.events.get("sub_events", [])
        if not subs:
            return
        last = subs[-1]
        remove_sub_event(self.events, last["id"])
        save_events(self.dataset_name, self.events)
        self._refresh_events_list()
        self._refresh_chart()
        self._refresh_global_status()
        self._toast_local(f"Eliminado: {last['name']}", "info")

    def _clear_all_events(self):
        if not self.events.get("sub_events"):
            return
        if not messagebox.askyesno(
                "Vaciar eventos",
                "¿Eliminar todos los eventos registrados?", parent=self):
            return
        self.events["sub_events"] = []
        save_events(self.dataset_name, self.events)
        self._refresh_events_list()
        self._refresh_chart()
        self._refresh_global_status()

    def _rename_event(self, sub_id: str, current_name: str):
        nm = simpledialog.askstring(
            "Renombrar evento",
            "Nuevo nombre:",
            initialvalue=current_name,
            parent=self)
        if not nm or not nm.strip():
            return
        nm = nm.strip()
        update_sub_event(self.events, sub_id, name=nm)
        save_events(self.dataset_name, self.events)
        self._refresh_events_list()
        self._refresh_chart()

    # ==============================================================
    # EXECUTE
    # ==============================================================
    def _on_execute(self):
        errors, warnings, summary = self._preflight_check_silent()

        # Mostrar el diálogo pre-flight
        pf = PreflightDialog(self, summary, warnings, errors)
        self.wait_window(pf)
        if not pf.proceed:
            return

        inputs = [n for n, c in self.var_cfg.items()
                  if c.get("visible") and n in self.df_full.columns]
        source = self.df_full
        events = copy.deepcopy(self.events)
        date_col = self.date_var.get()
        target_col = self.target_var.get()
        regression_type = self.reg_type_var.get()

        # Guardar config persistente
        self._save_last_config()

        def work(cancel, progress):
            return prepare_regression_data(source, events, date_col, cancel)

        def done(value):
            self._regression_active = False
            aug, anomaly_cols = value
            entry = {
                "timestamp": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                "regression_type": regression_type,
                "target_col": target_col,
                "date_col": date_col,
                "input_cols": inputs,
                "anomaly_cols": anomaly_cols,
                "n_obs": len(source),
                "r2": 0.0,
                "mape": 0.0,
            }
            self.result = {
                "df": aug,
                "input_cols": inputs,
                "target_col": target_col,
                "anomaly_cols": anomaly_cols,
                "regression_type": regression_type,
                "date_col": date_col,
                "_history_entry": entry,
            }
            self.destroy()

        def failed(exc):
            self._regression_active = False
            self.btn_run.state(["!disabled"])
            messagebox.showerror("Error al preparar regresión", str(exc),
                                 parent=self)

        def cancelled():
            self._regression_active = False
            self.result = None
            self.destroy()

        if self.master.tasks.start(work, on_result=done,
                                   on_error=failed, on_cancel=cancelled):
            self._regression_active = True
            self.btn_run.state(["disabled"])
        else:
            messagebox.showwarning("Aviso", "Ya hay una tarea en curso.",
                                   parent=self)

    def _on_cancel(self):
        if getattr(self, "_regression_active", False):
            self.master.tasks.cancel()
            return
        self.result = None
        self.destroy()

    def _on_target_change(self):
        self._refresh_variables()
        self._refresh_chart()
        self._refresh_global_status()

    def _apply_default_type(self):
        t = self.default_type.get()
        for cfg in self.var_cfg.values():
            cfg["type"] = t
        self._refresh_variables()
        self._refresh_chart()

    # ==============================================================
    # UTILS
    # ==============================================================
    def _toast_local(self, msg: str, kind: str = "info"):
        try:
            Toast(self.master, msg, kind=kind, duration=1800)
        except Exception:
            pass
# ==================================================================
# DIÁLOGO DE CONFIGURACIÓN DE ANÁLISIS
# ==================================================================
