"""Causal Impact configuration dialog."""

import copy
import hashlib
import json
import os
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from core import engine as db_engine
from core.atomic import write_json_atomic
from core.events import add_sub_event, load_events, remove_sub_event, save_events
from core.loader import _DATE_KEYWORDS, _lossless_numeric, get_date_columns
from services.analysis_service import prepare_causal_data
from theme import COLORS, FONTS, Toast
from ui.window_position import center_popup
from ui.dialogs.regression_dialog import RegressionDialog


def _causal_config_path(dataset_name: str) -> Path:
    """Ruta persistente de preferencias, independiente del ZIP portable."""
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or str(Path.home() / ".medicion_agil"))
    dataset_text = str(dataset_name)
    slug = "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in dataset_text).strip("_")[:48] or "dataset"
    digest = hashlib.sha256(dataset_text.encode("utf-8")).hexdigest()[:12]
    return (Path(base) / "MedicionAgil" / "causal_impact"
            / f"{slug}-{digest}.json")


def _load_causal_config(dataset_name: str) -> dict:
    try:
        payload = json.loads(_causal_config_path(dataset_name).read_text(
            encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _dims_fingerprint(dim_dict) -> str:
    """Huella ordenada de un dim_dict para comparar binomios."""
    try:
        items = []
        for k in sorted(dim_dict.keys()):
            v = dim_dict[k]
            if isinstance(v, list):
                vals = tuple(sorted(str(x) for x in v))
            else:
                vals = (str(v),)
            items.append((str(k), vals))
        return "|".join(f"{k}={','.join(vs)}" for k, vs in items)
    except Exception:
        return str(dim_dict)


def _parse_task_filter(task_str: str) -> dict[str, list[str]]:
    """Convierte ``dim=a|b;otra=c`` en un filtro apto para el preview."""
    parsed = {}
    for part in str(task_str or "").split(";"):
        if "=" not in part:
            continue
        column, raw_values = part.split("=", 1)
        values = [v.strip() for v in raw_values.split("|") if v.strip()]
        if column.strip() and values:
            parsed[column.strip()] = values
    return parsed

class CausalImpactDialog(tk.Toplevel):
    """
    Diálogo de Causal Impact con:
      - Selección gráfica y manual de PRE/POST
      - Series binomiales con buscador y chips
      - Exclusión de binomios como controles
      - Variables externas sin KPIs
    """

    def __init__(self, parent, df: pd.DataFrame, dataset_name: str):
        super().__init__(parent)
        self.title("Causal Impact · configuración")
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()

        # --- Auto-mapeo destino (evita copy si no hace falta) ---
        # Usamos referencia directa. Si hace falta mapear, se copia
        # solo una vez al aplicar el mapeo.
        self.df_full = df
        self.df = RegressionDialog._sample_for_preview(df)
        if len(self.df) < len(df):
            self.title(f"Causal Impact · vista de {len(self.df):,} "
                       f"de {len(df):,} filas")
        print(f"[CI Dialog] df recibido: {self.df.shape[0]:,} filas × "
              f"{self.df.shape[1]} cols".replace(",", "."))

        try:
            from core import destination_mapping as dm
            if "destination_area_mapped" not in self.df.columns:
                for c in ["Hotel_short_name", "hotel_short_name",
                           "Hotel", "hotel", "Hotel_code"]:
                    if c in self.df.columns:
                        self.df = dm.add_destination_column(
                            self.df, hotel_col=c,
                            dest_col="destination_area_mapped",
                            fallback="otros", inplace=False)
                        print(f"[CI Dialog] Auto-mapeo: {c} -> "
                              f"destination_area_mapped")
                        break
        except Exception as e:
            print(f"[CI Dialog] Auto-mapeo falló: {e}")

        # ⬇⬇⬇ AQUÍ VA LA LÍNEA NUEVA ⬇⬇⬇
        # ✅ Forzar todas las columnas numéricas antes de construir la UI
        try:
            self._coerce_numeric_columns()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[CI Dialog] ⚠ _coerce_numeric_columns falló: {e}")

        # ⬇⬇⬇ El resto sigue igual ⬇⬇⬇
        self.dataset_name = dataset_name
        self.result = None
        self._tooltips = []
        # ... (todo lo demás del __init__ tal cual lo tienes)

        # --- Estado de marcado ---
        self.mark_mode = None
        self.drag_start = None

        # --- Índices y fechas seleccionadas ---
        self.pre_start_idx = None
        self.pre_end_idx = None
        self.post_start_idx = None
        self.post_end_idx = None

        # Fechas manuales (independientes del drag)
        self.pre_start_date_var = tk.StringVar(value="")
        self.pre_end_date_var = tk.StringVar(value="")
        self.post_start_date_var = tk.StringVar(value="")
        self.post_end_date_var = tk.StringVar(value="")

        # Selectores independientes para decidir qué combinación se
        # previsualiza sobre el gráfico de periodos.
        self.preview_target_var = tk.StringVar(value="")
        self.preview_kpi_var = tk.StringVar(value="")

        # --- Series ---
        self._series_vars = {}
        self._series_data = []
        self._series_selected = []
        self._last_series_signature = ""
        self.search_series_var = tk.StringVar()
        self._series_page = 500    # ← NUEVO

                # --- Grupos de binomios a analizar (targets) ---
        # Cada item: {"label": str, "task_str": str, "dims": list[dict]}
        self._target_groups: list[dict] = []

        # --- Controles a excluir (binomios) ---
        self._excluir_data = []
        self._excluir_vars = {}
        self._excluir_selected = []
        self.search_excluir_var = tk.StringVar()

        # --- Eventos ---
        try:
            from core.events import load_events
            self.events = load_events(dataset_name)
        except Exception:
            self.events = {"groups": {}, "sub_events": []}
        self._saved_config = _load_causal_config(dataset_name)

        # --- Caché del agregado ---
        self._agg_cache = None
        self._agg_cache_key = None

        # --- Geometría ---
        w, h = 1480, 900
        center_popup(self, parent, w, h)
        self.minsize(1240, 780)

        # --- Estado general ---
        self._dim_vars = {}
        self._sesgo_vars = {}
        self._kpi_cols_computed = []

        # Variables BSTS
        self.alpha_var = tk.StringVar(value="0.05")
        self.series_sep_var = tk.StringVar(value="_")

        # --- 1 · Construir solo el esqueleto (rápido) ---
        self._build_skeleton()
        self._causal_active = False
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # --- 2 · Cargar el contenido pesado en after() ---
        #       La ventana ya es visible antes de bloquearse
        self.after(30, self._deferred_init)

    def _coerce_numeric_columns(self):
        """Convierte columnas de la muestra solo si no pierde valores."""
        convertidas = []
        for c in self.df.columns:
            if pd.api.types.is_numeric_dtype(self.df[c]):
                continue
            if pd.api.types.is_datetime64_any_dtype(self.df[c]):
                continue
            if any(k in str(c).lower() for k in _DATE_KEYWORDS):
                continue
            dtype = self.df[c].dtype
            if not (dtype == object or str(dtype) in ("string", "str")
                    or isinstance(dtype, pd.CategoricalDtype)):
                continue
            try:
                converted = _lossless_numeric(self.df[c])
                if converted is not None:
                    self.df[c] = converted
                    convertidas.append(c)
            except Exception as e:
                print(f"[CI Dialog] coerce '{c}': {e}")
        if convertidas:
            print(f"[CI Dialog] Forzadas a numerico: {convertidas}")
        num_final = self.df.select_dtypes("number").columns.tolist()
        print(f"[CI Dialog] Columnas numéricas disponibles: {num_final}")

    def _build_skeleton(self):
        """Solo pinta header, notebook y footer. Instantáneo."""
        import time as _t
        t0 = _t.time()

        try:
            self._build_ui()
            print(f"[CI Dialog] esqueleto UI construido en "
                  f"{_t.time()-t0:.2f}s")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[CI Dialog] ❌ Error construyendo UI: {e}")

            # Limpiar el frame principal por si quedó a medias
            try:
                for w in self.winfo_children():
                    w.destroy()
            except Exception:
                pass

            # Mostrar el error en un panel legible
            try:
                err_frame = tk.Frame(self, bg=COLORS["bg"])
                err_frame.pack(fill="both", expand=True,
                               padx=40, pady=40)
                tk.Label(err_frame,
                         text="⚠ Error al construir el diálogo",
                         bg=COLORS["bg"], fg=COLORS["danger"],
                         font=FONTS["h2"]).pack(anchor="w")
                tk.Label(err_frame,
                         text=f"{type(e).__name__}: {e}",
                         bg=COLORS["bg"], fg=COLORS["text"],
                         font=FONTS["body"],
                         wraplength=900, justify="left").pack(
                    anchor="w", pady=(8, 0))
                tb = traceback.format_exc()[-1500:]
                tk.Label(err_frame, text=tb,
                         bg=COLORS["bg"], fg=COLORS["text_dim"],
                         font=FONTS["mono"],
                         wraplength=900, justify="left").pack(
                    anchor="w", pady=(12, 0))
            except Exception:
                pass

        self.update_idletasks()

    def _deferred_init(self):
        """
        Carga pesada: se ejecuta con la ventana ya visible.

        ORDEN:
          1º chart (puebla _agg_cache)
          2º defaults PRE/POST
          3º resto (eventos, grupos, estado global)
        """
        import time as _t
        t0 = _t.time()

        # 1º · Restaurar preferencias antes de calcular la vista previa.
        try:
            restored_periods = self._restore_last_config()
        except Exception as e:
            restored_periods = False
            print(f"[CI Dialog] ❌ restaurando configuración: {e}")

        # 2º · Chart
        try:
            self._refresh_chart()
            print(f"[CI Dialog] _refresh_chart: {_t.time()-t0:.2f}s")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[CI Dialog] ❌ _refresh_chart: {e}")
        self.update_idletasks()

        # 3º · Defaults PRE/POST cuando no hay periodos guardados.
        try:
            if restored_periods:
                self._apply_manual_dates()
            else:
                self._initialize_defaults()
            print(f"[CI Dialog] periodos inicializados: "
                  f"{_t.time()-t0:.2f}s")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[CI Dialog] ❌ inicializando periodos: {e}")
        self.update_idletasks()

        # 4º · Resto (eventos, grupos, estado global)
        tareas = (
            ("_refresh_events_list",   self._refresh_events_list),
            ("_render_targets_list",   self._render_targets_list),
            ("_refresh_global_status", self._refresh_global_status),
        )
        for name, fn in tareas:
            try:
                fn()
                print(f"[CI Dialog] {name}: {_t.time()-t0:.2f}s")
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[CI Dialog] ❌ {name}: {e}")

        print(f"[CI Dialog] Total: {_t.time()-t0:.2f}s")
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

        tk.Label(hd, text="Causal Impact · configuración y estimación",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")

        tk.Label(self, text=("Para intervenciones ya ejecutadas: necesita controles "
                             "estables y no afectados. Una correlación PRE alta "
                             "por sí sola no valida el contrafactual."),
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"], anchor="w", justify="left").pack(
                     fill="x", padx=20, pady=(0, 2))

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

        self.btn_run = ttk.Button(fr, text="▶  Estimar CI",
                                   style="Primary.TButton",
                                   command=self._on_execute)
        self.btn_run.pack(side="right")

    def _build_config_tab(self):
        # Barra superior con botones de marcado
        top = tk.Frame(self.tab_cfg, bg=COLORS["bg"], padx=16, pady=10)
        top.pack(fill="x", side="top")

        tk.Label(top, text="Método:", bg=COLORS["bg"],
                 fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        tk.Label(top, text="BSTS (pycausalimpact / statsmodels)",
                 bg=COLORS["bg"], fg=COLORS["primary"],
                 font=FONTS["small"], padx=8).pack(side="left")

        self.btn_mark_pre = tk.Button(
            top, text="🔵 Marcar PRE", relief="flat",
            bg=COLORS["bg_input"], fg=COLORS["text"],
            activebackground=COLORS["bg_hover"],
            font=FONTS["small"], padx=10, pady=4, cursor="hand2",
            command=lambda: self._toggle_mode("pre"))
        self.btn_mark_pre.pack(side="left", padx=(20, 6))

        self.btn_mark_post = tk.Button(
            top, text="🔴 Marcar POST", relief="flat",
            bg=COLORS["bg_input"], fg=COLORS["text"],
            activebackground=COLORS["bg_hover"],
            font=FONTS["small"], padx=10, pady=4, cursor="hand2",
            command=lambda: self._toggle_mode("post"))
        self.btn_mark_post.pack(side="left", padx=(0, 6))

        self.btn_mark_event = tk.Button(
            top, text="➕ Añadir evento", relief="flat",
            bg=COLORS["bg_input"], fg=COLORS["text"],
            activebackground=COLORS["bg_hover"],
            font=FONTS["small"], padx=10, pady=4, cursor="hand2",
            command=lambda: self._toggle_mode("event"))
        self.btn_mark_event.pack(side="left", padx=(0, 6))

        ttk.Button(top, text="↺ Limpiar marcas",
                   command=self._clear_marks).pack(side="left",
                                                     padx=(12, 0))
        ttk.Button(top, text="🗑 Limpiar sesión",
                   command=self._clear_last_config).pack(side="left",
                                                          padx=(6, 0))

        tk.Label(top, text="rueda: zoom · doble clic: reset",
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"]).pack(side="right")

        # Cuerpo: gráfico + panel
        body = tk.Frame(self.tab_cfg, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        # IZQUIERDA: gráfico
        left = tk.Frame(body, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        left.pack(side="left", fill="both", expand=True)

        chart_head = tk.Frame(left, bg=COLORS["bg_card"])
        chart_head.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(chart_head, text="Serie temporal y selección de periodos",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")

        selector_wrap = tk.Frame(chart_head, bg=COLORS["bg_card"])
        selector_wrap.pack(side="right", padx=(12, 0))
        tk.Label(selector_wrap, text="Target", bg=COLORS["bg_card"],
                 fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(
                     side="left", padx=(0, 4))
        self.preview_target_combo = ttk.Combobox(
            selector_wrap, textvariable=self.preview_target_var,
            values=[], state="readonly", width=25)
        self.preview_target_combo.pack(side="left", padx=(0, 10))
        self.preview_target_combo.bind(
            "<<ComboboxSelected>>", self._on_preview_selection)

        tk.Label(selector_wrap, text="KPI", bg=COLORS["bg_card"],
                 fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(
                     side="left", padx=(0, 4))
        self.preview_kpi_combo = ttk.Combobox(
            selector_wrap, textvariable=self.preview_kpi_var,
            values=[], state="readonly", width=20)
        self.preview_kpi_combo.pack(side="left")
        self.preview_kpi_combo.bind(
            "<<ComboboxSelected>>", self._on_preview_selection)

        self.lbl_marks = tk.Label(
            chart_head, text="", bg=COLORS["bg_card"],
            fg=COLORS["text_dim"], font=FONTS["tiny"])
        self.lbl_marks.pack(side="right", padx=(12, 0))

        chart_wrap = tk.Frame(left, bg=COLORS["bg_card"])
        chart_wrap.pack(fill="both", expand=True, padx=10,
                          pady=(0, 10))

        self.fig = Figure(figsize=(9, 5.5), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_wrap)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.canvas.mpl_connect("button_press_event",
                                  self._on_double_click)

        # DERECHA: panel scrollable
        right_wrap = tk.Frame(body, bg=COLORS["bg"], width=520)
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

        # Secciones
        self._build_section_datos(right)
        self._build_section_series(right)
        self._build_section_periodo(right)
        self._build_section_bsts(right)
        self._build_section_controles(right)
        self._build_section_excluir_binomios(right)
        self._build_section_externas(right)
        self._build_section_eventos(right)

        tk.Frame(right, bg=COLORS["bg"], height=20).pack()

    # ---------------- Helpers de sección ----------------
    def _panel_section(self, parent, title):
        tk.Frame(parent, bg=COLORS["border"], height=1).pack(
            fill="x", pady=(14, 0))
        tk.Label(parent, text=title, bg=COLORS["bg"],
                 fg=COLORS["primary"], font=FONTS["h3"]).pack(
            anchor="w", pady=(4, 6))

    def _panel_number(self, parent, label, varname):
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, bg=COLORS["bg"],
                 fg=COLORS["text_muted"],
                 font=FONTS["small"], anchor="w").pack(
            side="left", fill="x", expand=True)
        var = getattr(self, varname, None)
        if var is None:
            var = tk.StringVar(value="0")
            setattr(self, varname, var)
        ttk.Entry(row, textvariable=var, width=10).pack(side="right")

    # ==============================================================
    # SECCIONES
    # ==============================================================
    def _build_section_datos(self, parent):
        self._panel_section(parent, "📊 Datos")

        # Fecha
        tk.Label(parent, text="Columna de fecha",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(anchor="w", pady=(4, 2))
        date_cols = get_date_columns(self.df)
        self.date_var = tk.StringVar(
            value=str(date_cols[0]) if date_cols else "")
        cb = ttk.Combobox(parent, textvariable=self.date_var,
                           values=[str(c) for c in date_cols],
                           state="readonly")
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>",
                lambda e: self._on_date_kpi_change())

        # ==========================================================
        # KPIs a analizar (CHECKBOXES multi-selección)
        # ==========================================================
        kpi_hdr = tk.Frame(parent, bg=COLORS["bg"])
        kpi_hdr.pack(fill="x", pady=(8, 2))
        tk.Label(kpi_hdr, text="KPIs a analizar",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        self.lbl_kpi_count = tk.Label(
            kpi_hdr, text="0 seleccionados",
            bg=COLORS["bg"], fg=COLORS["warning"],
            font=FONTS["tiny"])
        self.lbl_kpi_count.pack(side="right")

        num_cols = self.df.select_dtypes("number").columns.tolist()
        # Detectar KPIs (para excluirlos del combo de externas)
        self._kpi_cols_computed = self._detect_kpis(num_cols)

        # Estado: dict col → BooleanVar
        self._kpi_vars: dict[str, tk.BooleanVar] = {}

        # Contenedor scrollable
        kpi_wrap = tk.Frame(parent, bg=COLORS["bg_card"],
                             highlightbackground=COLORS["border"],
                             highlightthickness=1)
        kpi_wrap.pack(fill="x")

        kpi_canvas = tk.Canvas(kpi_wrap, bg=COLORS["bg_card"],
                                highlightthickness=0, height=140)
        kpi_scroll = ttk.Scrollbar(kpi_wrap, orient="vertical",
                                    command=kpi_canvas.yview)
        kpi_inner = tk.Frame(kpi_canvas, bg=COLORS["bg_card"])
        kpi_inner.bind(
            "<Configure>",
            lambda e: kpi_canvas.configure(
                scrollregion=kpi_canvas.bbox("all")))
        kpi_canvas.create_window((0, 0), window=kpi_inner,
                                   anchor="nw")
        kpi_canvas.configure(yscrollcommand=kpi_scroll.set)
        kpi_canvas.pack(side="left", fill="both", expand=True,
                         padx=4, pady=4)
        kpi_scroll.pack(side="right", fill="y")

        def _make_toggle(col_name):
            def _toggle():
                self._refresh_preview_selectors()
                try:
                    self._on_date_kpi_change()
                except Exception:
                    pass
                self._refresh_kpi_count()
                self._refresh_global_status()
            return _toggle

        for c in num_cols:
            cs = str(c)
            v = tk.BooleanVar(value=False)
            self._kpi_vars[cs] = v
            cb = ttk.Checkbutton(kpi_inner, text=cs, variable=v,
                                  command=_make_toggle(cs))
            cb.pack(anchor="w", padx=4, pady=1)

        # Por defecto marcamos el primero para que el gráfico no esté vacío
        self.kpi_var = self.preview_kpi_var
        self.kpi_var.set(str(num_cols[0]) if num_cols else "")
        if num_cols:
            self._kpi_vars[str(num_cols[0])].set(True)

        self._refresh_kpi_count()
        self._refresh_preview_selectors()

        # Granularidad
        tk.Label(parent, text="Granularidad",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(anchor="w", pady=(8, 2))
        self.gran_var = tk.StringVar(value="semanal")
        cb_g = ttk.Combobox(parent, textvariable=self.gran_var,
                             values=["diaria", "semanal", "mensual"],
                             state="readonly")
        cb_g.pack(fill="x")
                # --- Aviso: columnas "no numéricas que parecen numéricas" ---
        try:
            num_cols_now = self.df.select_dtypes("number").columns.tolist()
            posibles = []
            for c in self.df.columns:
                if c in num_cols_now:
                    continue
                s = self.df[c].dropna().astype(str).head(50)
                if s.empty:
                    continue
                has_digit = s.str.contains(r"\d", regex=True).mean()
                has_sep = s.str.contains(r"\d[,.]\d", regex=True).mean()
                if has_digit >= 0.9 and has_sep >= 0.5:
                    posibles.append(str(c))

            if posibles:
                tk.Label(
                    parent,
                    text=("⚠ Estas columnas parecen numéricas pero se "
                          "cargan como texto (probablemente por usar "
                          "separadores de miles):"),
                    bg=COLORS["bg"], fg=COLORS["warning"],
                    font=FONTS["tiny"], wraplength=490,
                    justify="left",
                ).pack(anchor="w", pady=(10, 2))
                for c in posibles:
                    tk.Label(
                        parent, text=f"   · {c}",
                        bg=COLORS["bg"], fg=COLORS["warning"],
                        font=FONTS["tiny"], anchor="w",
                    ).pack(anchor="w")
        except Exception:
            pass
        cb_g.bind("<<ComboboxSelected>>",
                  lambda e: self._refresh_chart())

    def _detect_kpis(self, num_cols):
        """Detecta qué columnas numéricas son KPIs (para excluirlas de
        variables externas)."""
        kpi_keywords = [
            "revenue", "ingresos", "ventas", "sales", "sessions",
            "usuarios", "users", "transactions", "checkouts",
            "conversions", "conversiones", "leads", "bookings",
            "orders", "pedidos", "carrito", "cart", "engaged",
            "itemview", "active_users", "new_users", "coste", "cost",
            "spend", "inversión", "inversion", "clicks", "impressions",
            "viewability", "ctr", "cpc", "cpm", "roas", "roi",
        ]
        kpis = []
        for c in num_cols:
            name_lower = str(c).lower()
            if any(k in name_lower for k in kpi_keywords):
                kpis.append(str(c))
        return kpis

    def _get_selected_kpis(self) -> list[str]:
        """Devuelve la lista de KPIs marcados."""
        if not hasattr(self, "_kpi_vars"):
            k = getattr(self, "kpi_var", None)
            v = k.get() if k else ""
            return [v] if v else []
        return [c for c, var in self._kpi_vars.items() if var.get()]

    def _refresh_kpi_count(self):
        """Actualiza el contador de KPIs seleccionados."""
        if not hasattr(self, "lbl_kpi_count"):
            return
        try:
            n = len(self._get_selected_kpis())
            total = len(getattr(self, "_kpi_vars", {}))
            self.lbl_kpi_count.config(
                text=f"{n} de {total} seleccionados",
                fg=COLORS["primary"] if n > 0 else COLORS["warning"])
        except Exception:
            pass

    
    def _build_section_series(self, parent):
        self._panel_section(parent, "🎯 Series (binomiales)")

        tk.Label(parent,
                 text="Marca las columnas que identifican cada serie. "
                      "Se combinarán para formar el identificador único.",
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], wraplength=490,
                 justify="left").pack(anchor="w", pady=(4, 4))

        # Columnas candidatas
        date_cols = get_date_columns(self.df)
        num_cols = self.df.select_dtypes("number").columns.tolist()

        sample_size = 50_000
        df_sample = (self.df.head(sample_size)
                     if len(self.df) > sample_size else self.df)

        dim_candidates = []
        for c in self.df.columns:
            if c in date_cols or c in num_cols:
                continue
            try:
                n = df_sample[c].nunique(dropna=True)
                if 2 <= n <= 500:
                    dim_candidates.append(c)
            except Exception:
                pass

        dim_wrap = tk.Frame(parent, bg=COLORS["bg_card"],
                            highlightbackground=COLORS["border"],
                            highlightthickness=1)
        dim_wrap.pack(fill="x")
        dim_inner = tk.Frame(dim_wrap, bg=COLORS["bg_card"])
        dim_inner.pack(fill="x", padx=8, pady=6)

        self._dim_vars = {}
        for c in dim_candidates:
            v = tk.BooleanVar(value=False)
            self._dim_vars[str(c)] = v
            ttk.Checkbutton(dim_inner, text=str(c), variable=v,
                             command=self._on_dim_change).pack(anchor="w")

        # --- Separador + Detectar ---
        sep_row = tk.Frame(parent, bg=COLORS["bg"])
        sep_row.pack(fill="x", pady=(8, 4))
        tk.Label(sep_row, text="Separador:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        sep_cb = ttk.Combobox(sep_row,
                               textvariable=self.series_sep_var,
                               values=["_", " | ", "-", "/", " · "],
                               state="readonly", width=8)
        sep_cb.pack(side="left", padx=(6, 0))
        sep_cb.bind("<<ComboboxSelected>>",
                    lambda e: self._on_dim_change())

        ttk.Button(sep_row, text="🔄 Detectar",
                   style="Primary.TButton",
                   command=self._calcular_series).pack(
            side="left", padx=(12, 0))

        # --- Buscador ---
        search_row = tk.Frame(parent, bg=COLORS["bg"])
        search_row.pack(fill="x", pady=(4, 4))
        tk.Label(search_row, text="🔍 Buscar:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        search_entry = ttk.Entry(search_row,
                                   textvariable=self.search_series_var)
        search_entry.pack(side="left", fill="x", expand=True,
                            padx=(6, 0))
        self.search_series_var.trace_add(
            "write", lambda *a: self._render_series_list())

        # --- Estado ---
        self.lbl_series_status = tk.Label(
            parent, text="(sin series detectadas)",
            bg=COLORS["bg"], fg=COLORS["text_dim"],
            font=FONTS["tiny"], anchor="w",
            justify="left", wraplength=490)
        self.lbl_series_status.pack(fill="x", pady=(0, 4))

        # --- Lista con CHECKBOXES ---
        series_wrap = tk.Frame(parent, bg=COLORS["bg_card"],
                                highlightbackground=COLORS["border"],
                                highlightthickness=1)
        series_wrap.pack(fill="x")

        self.series_tree = ttk.Treeview(
            series_wrap,
            columns=("check", "name"),
            show="headings",
            height=7,
            selectmode="browse",
        )
        self.series_tree.heading("check", text="✓")
        self.series_tree.heading("name", text="Serie")
        self.series_tree.column("check", width=34,
                                  anchor="center", stretch=False)
        self.series_tree.column("name", width=400, anchor="w")

        sv = ttk.Scrollbar(series_wrap, orient="vertical",
                            command=self.series_tree.yview)
        self.series_tree.configure(yscrollcommand=sv.set)
        self.series_tree.pack(side="left", fill="both", expand=True,
                               padx=(6, 0), pady=6)
        sv.pack(side="right", fill="y", pady=6, padx=(0, 6))

        self.series_tree.bind("<Button-1>", self._on_series_tree_click)
        self.series_tree.bind("<Double-1>",
                                self._on_series_tree_doubleclick)

        # --- Botones rápidos ---
        quick_row = tk.Frame(parent, bg=COLORS["bg"])
        quick_row.pack(fill="x", pady=(4, 0))

        ttk.Button(quick_row, text="Marcar todo",
                   command=lambda: self._set_all_series(True)).pack(
            side="left", padx=(0, 4))
        ttk.Button(quick_row, text="Desmarcar",
                   command=lambda: self._set_all_series(False)).pack(
            side="left", padx=(0, 4))
        ttk.Button(quick_row, text="Invertir",
                   command=self._invert_series).pack(
            side="left", padx=(0, 4))

        # --- Panel de GRUPOS ---
        self._build_targets_panel(parent)

    def _build_targets_panel(self, parent):
        self._panel_section(parent, "📋 Grupos a analizar")

        tk.Label(parent,
                 text="Cada fila es un análisis independiente. "
                      "Marca binomios arriba y pulsa 🔗 o ➕.",
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], wraplength=490,
                 justify="left").pack(anchor="w", pady=(4, 4))

        # Botones
        act = tk.Frame(parent, bg=COLORS["bg"])
        act.pack(fill="x", pady=(0, 4))

        ttk.Button(act, text="➕ Añadir como suelto",
                   command=self._add_selected_as_individual).pack(
            side="left", padx=(0, 4), fill="x", expand=True)

        ttk.Button(act, text="🔗 Unir en grupo",
                   style="Primary.TButton",
                   command=self._add_selected_as_group).pack(
            side="left", fill="x", expand=True)

        # Lista de grupos
        self.targets_frame = tk.Frame(
            parent, bg=COLORS["bg_input"],
            highlightbackground=COLORS["border"],
            highlightthickness=1)
        self.targets_frame.pack(fill="x", pady=(4, 0))

        self._targets_inner = tk.Frame(self.targets_frame,
                                        bg=COLORS["bg_input"])
        self._targets_inner.pack(fill="x", padx=6, pady=6)

        ttk.Button(parent, text="🗑 Vaciar lista de grupos",
                   command=self._clear_targets).pack(
            anchor="e", pady=(4, 0))

        self._render_targets_list()

    def _render_targets_list(self):
        if not hasattr(self, "_targets_inner"):
            print("[CI Dialog] ⚠ _targets_inner no existe")
            return

        try:
            self._targets_inner.update_idletasks()
        except Exception:
            pass

        for w in self._targets_inner.winfo_children():
            w.destroy()

        if not self._target_groups:
            tk.Label(self._targets_inner,
                     text="(vacío · marca binomios y añádelos aquí)",
                     bg=COLORS["bg_input"], fg=COLORS["text_dim"],
                     font=FONTS["tiny"], wraplength=440,
                     justify="left").pack(anchor="w", pady=4)
            return

        for i, grp in enumerate(self._target_groups, start=1):
            row = tk.Frame(self._targets_inner, bg=COLORS["bg_input"])
            row.pack(fill="x", pady=2)

            tk.Label(row, text=f"{i}.",
                     bg=COLORS["bg_input"], fg=COLORS["text_dim"],
                     font=FONTS["small"], width=3,
                     anchor="w").pack(side="left")
            tk.Label(row, text=grp["label"],
                     bg=COLORS["bg_input"], fg=COLORS["text"],
                     font=FONTS["small"], anchor="w",
                     justify="left", wraplength=440).pack(
                side="left", fill="x", expand=True)

            def _rm(idx=i - 1):
                self._target_groups.pop(idx)
                self._render_targets_list()
                self._refresh_preview_selectors()
                self._refresh_global_status()

            tk.Button(row, text="×", relief="flat",
                      bg=COLORS["bg_input"], fg=COLORS["danger"],
                      activebackground=COLORS["bg_hover"],
                      font=("Segoe UI", 10, "bold"),
                      cursor="hand2", width=2,
                      command=_rm).pack(side="right", padx=(4, 0))

        # ✅ Forzar repintado
        try:
            self._targets_inner.update_idletasks()
            self.targets_frame.update_idletasks()
        except Exception:
            pass

        print(f"[CI Dialog] targets list refrescada · "
              f"{len(self._target_groups)} grupos")
    def _selected_tree_series(self) -> list[dict]:
        """Devuelve la lista de items seleccionados en el árbol."""
        sel_ids = self.series_tree.selection()
        out = []
        for rid in sel_ids:
            ts = self.series_tree.item(rid, "tags")[0] \
                if self.series_tree.item(rid, "tags") else None
            if not ts:
                continue
            info = next((i for i in self._series_data
                         if i["task_str"] == ts), None)
            if info:
                out.append(info)
        return out

    def _task_str_from_infos(self, infos):
        dims_list = []
        for info in infos:
            d = info["dim_dict"]
            clean = {}
            for k, v in d.items():
                clean[k] = list(v) if isinstance(v, list) else [v]
            dims_list.append(clean)

        common = set(dims_list[0].keys())
        for d in dims_list[1:]:
            common &= set(d.keys())
        common = sorted(common)

        combined = {}
        for col in common:
            vals = []
            for d in dims_list:
                for v in d[col]:
                    if v not in vals:
                        vals.append(v)
            combined[col] = vals

        task_str = ";".join(
            f"{c}={'|'.join(v)}" for c, v in combined.items()
        )
        return task_str, common, combined

    def _label_from_infos(self, infos):
        sep = self.series_sep_var.get() or "_"
        if len(infos) == 1:
            return infos[0]["display"]

        try:
            _, common, combined = self._task_str_from_infos(infos)
        except Exception:
            return " + ".join(i["display"] for i in infos)

        parts = []
        for c in common:
            vals = combined[c]
            if len(vals) == 1:
                parts.append(vals[0])
            else:
                parts.append("(" + " | ".join(vals) + ")")
        return sep.join(parts)

    def _add_selected_as_individual(self):
        if not self._series_selected:
            messagebox.showinfo(
                "Añadir como suelto",
                "Marca al menos un binomio (✓) en la lista.", parent=self)
            return

        existing_tasks = {g.get("task_str") for g in self._target_groups}

        added = 0
        for ts in list(self._series_selected):
            info = next((i for i in self._series_data
                         if i["task_str"] == ts), None)
            if info is None:
                continue
            if ts in existing_tasks:
                continue

            self._target_groups.append({
                "label": info["display"],
                "task_str": ts,
                "dims": [info["dim_dict"]],
            })
            existing_tasks.add(ts)
            added += 1

            if ts in self._series_vars:
                self._series_vars[ts].set(False)

        self._refresh_tree_checkmarks()
        self._on_series_toggle()
        self._render_targets_list()
        self._refresh_preview_selectors()
        self._refresh_global_status()
        print(f"[CI Dialog] Añadidos {added} sueltos "
              f"(total {len(self._target_groups)})")

    def _add_selected_as_group(self):
        print(f"[DIAG] add_group: _series_selected={self._series_selected}")
        print(f"[DIAG] add_group: len(_target_groups) antes="
              f"{len(self._target_groups)}")
        if len(self._series_selected) < 2:
            messagebox.showinfo(
                "Unir en grupo",
                "Marca al menos 2 binomios (✓) en la lista.", parent=self)
            return

        infos = []
        for ts in self._series_selected:
            info = next((i for i in self._series_data
                         if i["task_str"] == ts), None)
            if info is None:
                continue
            infos.append(info)

        if len(infos) < 2:
            messagebox.showinfo(
                "Unir en grupo",
                "Marca al menos 2 binomios válidos en la lista.", parent=self)
            return

        try:
            task_str, _, _ = self._task_str_from_infos(infos)
        except Exception as e:
            messagebox.showerror("Unir en grupo", str(e), parent=self)
            return

        if not task_str:
            messagebox.showinfo(
                "Unir en grupo",
                "Las series marcadas no comparten columnas.", parent=self)
            return

        if any(g["task_str"] == task_str
               for g in self._target_groups):
            messagebox.showinfo(
                "Unir en grupo",
                "Ese grupo ya está en la lista.", parent=self)
            for ts in list(self._series_selected):
                if ts in self._series_vars:
                    self._series_vars[ts].set(False)
            self._refresh_tree_checkmarks()
            self._on_series_toggle()
            return

        label = self._label_from_infos(infos)
        self._target_groups.append({
            "label": label,
            "task_str": task_str,
            "dims": [i["dim_dict"] for i in infos],
        })

        for info in infos:
            ts = info["task_str"]
            if ts in self._series_vars:
                self._series_vars[ts].set(False)

        self._refresh_tree_checkmarks()
        self._on_series_toggle()
        self._render_targets_list()
        self._refresh_preview_selectors()
        self._refresh_global_status()
        print(f"[CI Dialog] Grupo añadido: {label}  ·  {task_str}")
    
    def _dims_fingerprint(dim_dict: dict) -> str:
        """
        Devuelve una huella única y ordenada de un dim_dict, para
        detectar binomios ya presentes en algún grupo.
        """
        try:
            items = []
            for k in sorted(dim_dict.keys()):
                v = dim_dict[k]
                if isinstance(v, list):
                    vals = tuple(sorted(str(x) for x in v))
                else:
                    vals = (str(v),)
                items.append((str(k), vals))
            return "|".join(f"{k}={','.join(vs)}" for k, vs in items)
        except Exception:
            return str(dim_dict)

    def _clear_tree_selection(self):
        """
        Deselecciona TODAS las filas del treeview.

        En Tkinter, `selection_remove` necesita los IDs como
        argumentos separados, no como tupla. Hay que desempaquetar.
        """
        try:
            ids = self.series_tree.selection()
            if ids:
                self.series_tree.selection_remove(*ids)
        except Exception as e:
            print(f"[CI Dialog] _clear_tree_selection: {e}")

    def _clear_targets(self):
        if not self._target_groups:
            return
        if not messagebox.askyesno(
                "Vaciar grupos",
                "¿Vaciar la lista de grupos a analizar?", parent=self):
            return
        self._target_groups.clear()
        self._render_targets_list()
        self._refresh_preview_selectors()
        self._refresh_global_status()
    def _select_all_series(self, value: bool):
        """Selecciona o deselecciona todos los items del árbol."""
        try:
            if value:
                self.series_tree.selection_set(
                    self.series_tree.get_children())
            else:
                self.series_tree.selection_remove(
                    self.series_tree.get_children())
        except Exception:
            pass

    def _build_section_periodo(self, parent):
        self._panel_section(parent, "📅 Periodo (drag o manual)")

        # PRE
        tk.Label(parent, text="PRE (entrenamiento):",
                 bg=COLORS["bg"], fg=COLORS["primary"],
                 font=FONTS["h3"]).pack(anchor="w", pady=(4, 2))

        pre_row = tk.Frame(parent, bg=COLORS["bg"])
        pre_row.pack(fill="x", pady=2)
        tk.Label(pre_row, text="Desde:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        e1 = ttk.Entry(pre_row, textvariable=self.pre_start_date_var,
                        width=13)
        e1.pack(side="left", padx=(4, 8))
        tk.Label(pre_row, text="Hasta:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        e2 = ttk.Entry(pre_row, textvariable=self.pre_end_date_var,
                        width=13)
        e2.pack(side="left", padx=(4, 0))

        for w in (e1, e2):
            w.bind("<FocusOut>",
                   lambda ev: self._apply_manual_dates())
            w.bind("<Return>",
                   lambda ev: self._apply_manual_dates())

        self.lbl_pre_selected = tk.Label(
            parent, text="PRE:  (sin marcar)", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["tiny"], anchor="w")
        self.lbl_pre_selected.pack(fill="x", pady=(2, 8))

        # POST
        tk.Label(parent, text="POST (efecto):",
                 bg=COLORS["bg"], fg=COLORS["danger"],
                 font=FONTS["h3"]).pack(anchor="w", pady=(4, 2))

        post_row = tk.Frame(parent, bg=COLORS["bg"])
        post_row.pack(fill="x", pady=2)
        tk.Label(post_row, text="Desde:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        e3 = ttk.Entry(post_row, textvariable=self.post_start_date_var,
                        width=13)
        e3.pack(side="left", padx=(4, 8))
        tk.Label(post_row, text="Hasta:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        e4 = ttk.Entry(post_row, textvariable=self.post_end_date_var,
                        width=13)
        e4.pack(side="left", padx=(4, 0))

        for w in (e3, e4):
            w.bind("<FocusOut>",
                   lambda ev: self._apply_manual_dates())
            w.bind("<Return>",
                   lambda ev: self._apply_manual_dates())

        self.lbl_post_selected = tk.Label(
            parent, text="POST: (sin marcar)", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["tiny"], anchor="w")
        self.lbl_post_selected.pack(fill="x", pady=(2, 6))

        tk.Label(parent,
                 text="Formato: YYYY-MM-DD. También puedes arrastrar "
                      "en el gráfico.",
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], wraplength=490,
                 justify="left").pack(anchor="w", pady=(4, 0))

    def _build_section_bsts(self, parent):
        self._panel_section(parent, "🧠 Modelo causal")
        self._panel_number(parent, "alpha (significación)",
                            "alpha_var")
        tk.Label(parent,
                 text=("La estacionalidad se detecta con el periodo PRE; "
                       "los parámetros internos del modelo se fijan con "
                       "valores reproducibles."),
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], wraplength=490,
                 justify="left").pack(anchor="w", pady=(8, 2))

    def _build_section_controles(self, parent):
        self._panel_section(parent, "🔍 Selección de controles automática")
        tk.Label(parent,
                 text=("Se filtran y ordenan controles con histórico PRE y "
                       "backtesting temporal. Excluye aquí controles tratados "
                       "o con riesgo conocido de contaminación."),
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], wraplength=490,
                 justify="left").pack(anchor="w", pady=(6, 2))

    def _build_section_excluir_binomios(self, parent):
        """Selector de BINOMIOS a excluir como controles."""
        self._panel_section(parent, "⛔ Excluir binomios como controles")

        tk.Label(parent,
                 text="Marca binomios que NO deben usarse como control "
                      "en ningún análisis.",
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], wraplength=490,
                 justify="left").pack(anchor="w", pady=(4, 4))

        # Chips de binomios excluidos
        tk.Label(parent, text="Excluidos:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(anchor="w", pady=(4, 2))

        self.chips_excluir_frame = tk.Frame(
            parent, bg=COLORS["bg_input"],
            highlightbackground=COLORS["border"],
            highlightthickness=1)
        self.chips_excluir_frame.pack(fill="x", pady=(0, 6))

        # Buscador
        search_row = tk.Frame(parent, bg=COLORS["bg"])
        search_row.pack(fill="x", pady=(4, 4))
        tk.Label(search_row, text="🔍 Buscar:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        ttk.Entry(search_row,
                   textvariable=self.search_excluir_var).pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self.search_excluir_var.trace_add(
            "write", lambda *a: self._render_excluir_list())

        # --- Lista de excluir con TREEVIEW (sin límite) ---
        excl_wrap = tk.Frame(parent, bg=COLORS["bg_card"],
                              highlightbackground=COLORS["border"],
                              highlightthickness=1)
        excl_wrap.pack(fill="x")

        self.excluir_tree = ttk.Treeview(
            excl_wrap,
            columns=("check", "name"),
            show="headings",
            height=14,
            selectmode="browse",
        )
        self.excluir_tree.heading("check", text="✓")
        self.excluir_tree.heading("name", text="Serie a excluir")
        self.excluir_tree.column("check", width=30, anchor="center",
                                   stretch=False)
        self.excluir_tree.column("name", width=380, anchor="w")

        sv2 = ttk.Scrollbar(excl_wrap, orient="vertical",
                             command=self.excluir_tree.yview)
        self.excluir_tree.configure(yscrollcommand=sv2.set)
        self.excluir_tree.pack(side="left", fill="both", expand=True,
                                padx=(6, 0), pady=6)
        sv2.pack(side="right", fill="y", pady=6, padx=(0, 6))

        # Click → toggle
        self.excluir_tree.bind("<Button-1>",
                                 self._on_excluir_tree_click)
        self.excluir_tree.bind("<Double-1>",
                                 self._on_excluir_tree_doubleclick)

        self.lbl_excluir_status = tk.Label(
            parent, text="(detecta series primero)",
            bg=COLORS["bg"], fg=COLORS["text_dim"],
            font=FONTS["tiny"], anchor="w")
        self.lbl_excluir_status.pack(fill="x", pady=(4, 0))

    def _build_section_externas(self, parent):
        self._panel_section(parent, "📈 Variables externas (regresores)")

        tk.Label(parent,
                 text="Columnas numéricas que estarán SIEMPRE en el "
                      "modelo (excluye KPIs automáticamente).",
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], wraplength=490,
                 justify="left").pack(anchor="w", pady=(4, 4))

        num_cols = self.df.select_dtypes("number").columns.tolist()
        # Excluir los KPIs detectados
        kpis_set = set(self._kpi_cols_computed)
        externas_candidates = [c for c in num_cols
                                if str(c) not in kpis_set]

        wrap = tk.Frame(parent, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        wrap.pack(fill="x")
        inner = tk.Frame(wrap, bg=COLORS["bg_card"])
        inner.pack(fill="x", padx=8, pady=6)

        self._sesgo_vars = {}
        if not externas_candidates:
            tk.Label(inner,
                     text="(no hay variables externas candidatas · "
                          "todos los numéricos parecen KPIs)",
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=FONTS["tiny"], wraplength=460,
                     justify="left").pack(anchor="w")
        else:
            for c in externas_candidates:
                v = tk.BooleanVar(value=False)
                self._sesgo_vars[str(c)] = v
                ttk.Checkbutton(inner, text=str(c), variable=v).pack(
                    anchor="w")

    def _build_section_eventos(self, parent):
        self._panel_section(parent, "🔔 Eventos externos (dummies)")

        self.events_frame = tk.Frame(parent, bg=COLORS["bg_input"],
                                      highlightbackground=COLORS["border"],
                                      highlightthickness=1)
        self.events_frame.pack(fill="x", pady=(4, 0))

    def _build_history_tab(self):
        head = tk.Frame(self.tab_hist, bg=COLORS["bg"])
        head.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(head, text="Ejecuciones anteriores",
                 bg=COLORS["bg"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")

        self.hist_frame = tk.Frame(self.tab_hist, bg=COLORS["bg"])
        self.hist_frame.pack(fill="both", expand=True, padx=20,
                              pady=(0, 16))
        tk.Label(self.hist_frame,
                 text="Aún no hay ejecuciones registradas.",
                 bg=COLORS["bg"], fg=COLORS["text_dim"],
                 font=FONTS["body"]).pack(anchor="w", pady=20)

    # ==============================================================
    # INICIALIZACIÓN
    # ==============================================================
    def _initialize_defaults(self):
        """
        Inicializa PRE (70%) y POST (30%) sobre la REJILLA AGREGADA
        (la misma que usa el gráfico).

        Evita el bug de calcular índices sobre el df bruto (320k filas)
        cuando el gráfico tiene solo la rejilla agregada (87 puntos).
        """
        date_col = self.date_var.get()
        if not date_col or date_col not in self.df.columns:
            return

        # Asegurar que la caché agregada está poblada
        if self._agg_cache is None or len(self._agg_cache) == 0:
            try:
                self._refresh_chart()
            except Exception as e:
                print(f"[CI Dialog] init defaults → chart: {e}")

        # Determinar el tamaño REAL de la rejilla agregada
        if (self._agg_cache is not None
                and len(self._agg_cache) > 0):
            n = len(self._agg_cache)
        else:
            # Fallback conservador
            n = min(len(self.df), 500)

        if n < 10:
            print(f"[CI Dialog] init defaults: n={n} < 10, omitiendo")
            return

        # Asignar índices en la rejilla agregada
        split = int(n * 0.7)
        self.pre_start_idx = 0
        self.pre_end_idx = max(split - 1, 0)
        self.post_start_idx = min(split, n - 1)
        self.post_end_idx = n - 1

        # Sincronizar los Entry con las fechas reales
        self._sync_dates_from_index()

    def _sync_dates_from_index(self):
        """
        Sincroniza los Entry de fecha con los índices del drag.

        IMPORTANTE: hace CLAMP de los índices a [0, n-1] para evitar
        IndexError cuando el índice viene del df bruto (320k) pero la
        rejilla agregada es más pequeña (87).
        """
        date_col = self.date_var.get()
        if not date_col or date_col not in self.df.columns:
            return

        try:
            # --- Obtener la rejilla de fechas (misma que el gráfico) ---
            if (self._agg_cache is not None
                    and date_col in self._agg_cache.columns
                    and len(self._agg_cache) > 0):
                fechas = pd.to_datetime(
                    self._agg_cache[date_col]).values
            else:
                s = (pd.to_datetime(self.df[date_col], errors="coerce")
                       .dropna().sort_values().reset_index(drop=True))
                fechas = s.values

            n = len(fechas)
            if n == 0:
                return

            # --- Clamp de índices a [0, n-1] ---
            def _clamp(idx):
                if idx is None:
                    return None
                try:
                    return max(0, min(int(idx), n - 1))
                except Exception:
                    return None

            pre_s = _clamp(self.pre_start_idx)
            pre_e = _clamp(self.pre_end_idx)
            post_s = _clamp(self.post_start_idx)
            post_e = _clamp(self.post_end_idx)

            # Guardar índices ajustados
            self.pre_start_idx = pre_s
            self.pre_end_idx = pre_e
            self.post_start_idx = post_s
            self.post_end_idx = post_e

            # --- Volcar a los Entry de fecha ---
            if pre_s is not None:
                self.pre_start_date_var.set(
                    str(pd.Timestamp(fechas[pre_s]).date()))
            if pre_e is not None:
                self.pre_end_date_var.set(
                    str(pd.Timestamp(fechas[pre_e]).date()))
            if post_s is not None:
                self.post_start_date_var.set(
                    str(pd.Timestamp(fechas[post_s]).date()))
            if post_e is not None:
                self.post_end_date_var.set(
                    str(pd.Timestamp(fechas[post_e]).date()))

            self._update_marks_label()

        except Exception as e:
            print(f"[CI Dialog] sync dates: {e}")
    def _apply_manual_dates(self):
        """Aplica las fechas escritas a mano a los índices PRE/POST."""
        date_col = self.date_var.get()
        if not date_col or date_col not in self.df.columns:
            return

        def _parse(var):
            txt = var.get().strip()
            if not txt:
                return None
            try:
                return pd.Timestamp(txt)
            except Exception:
                return None

        pre_s = _parse(self.pre_start_date_var)
        pre_e = _parse(self.pre_end_date_var)
        post_s = _parse(self.post_start_date_var)
        post_e = _parse(self.post_end_date_var)

        # Obtener la grilla de fechas agregadas
        try:
            if (self._agg_cache is not None
                    and date_col in self._agg_cache.columns):
                fechas = pd.DatetimeIndex(
                    pd.to_datetime(self._agg_cache[date_col]))
            else:
                s = (pd.to_datetime(self.df[date_col], errors="coerce")
                       .dropna().sort_values().reset_index(drop=True))
                fechas = pd.DatetimeIndex(s)
        except Exception:
            return

        def _find_idx(target, how="nearest"):
            if target is None or len(fechas) == 0:
                return None
            if how == "floor":
                sub = fechas[fechas <= target]
                return int(fechas.get_loc(sub[-1])) if len(sub) else 0
            if how == "ceil":
                sub = fechas[fechas >= target]
                return int(fechas.get_loc(sub[0])) if len(sub) \
                    else len(fechas) - 1
            diffs = np.abs(fechas - target)
            return int(np.argmin(diffs))

        if pre_s is not None:
            self.pre_start_idx = _find_idx(pre_s, "ceil")
        if pre_e is not None:
            self.pre_end_idx = _find_idx(pre_e, "floor")
        if post_s is not None:
            self.post_start_idx = _find_idx(post_s, "ceil")
        if post_e is not None:
            self.post_end_idx = _find_idx(post_e, "floor")

        self._refresh_chart()
        self._refresh_global_status()

    def _update_marks_label(self):
        """Actualiza los labels inferiores con las fechas exactas."""
        try:
            if self.pre_start_idx is not None and self.pre_end_idx is not None:
                self.lbl_pre_selected.config(
                    text=f"PRE: {self.pre_start_date_var.get()} → "
                         f"{self.pre_end_date_var.get()}  "
                         f"({self.pre_end_idx - self.pre_start_idx + 1} pts)",
                    fg=COLORS["text"])
            if self.post_start_idx is not None and self.post_end_idx is not None:
                self.lbl_post_selected.config(
                    text=f"POST: {self.post_start_date_var.get()} → "
                         f"{self.post_end_date_var.get()}  "
                         f"({self.post_end_idx - self.post_start_idx + 1} pts)",
                    fg=COLORS["text"])
        except Exception:
            pass

    def _on_date_kpi_change(self):
        """Cuando cambia fecha o KPI: refresca la caché y el gráfico."""
        self._agg_cache = None
        self._agg_cache_key = None
        self._refresh_chart()
        self._sync_dates_from_index()

    def _refresh_preview_selectors(self):
        """Sincroniza los combos del gráfico con targets y KPI elegidos."""
        kpis = self._get_selected_kpis()
        current_kpi = self.preview_kpi_var.get()
        self.preview_kpi_combo.configure(values=kpis)
        if current_kpi not in kpis:
            self.preview_kpi_var.set(kpis[0] if kpis else "")

        labels = [str(group.get("label") or group.get("task_str") or "")
                  for group in self._target_groups]
        current_target = self.preview_target_var.get()
        self.preview_target_combo.configure(values=labels)
        if current_target not in labels:
            self.preview_target_var.set(labels[0] if labels else "")

    def _on_preview_selection(self, _event=None):
        """Redibuja la combinación target/KPI escogida en los combos."""
        self._agg_cache = None
        self._agg_cache_key = None
        self._refresh_chart()
        self._sync_dates_from_index()

    def _selected_preview_filter(self):
        """Obtiene el filtro completo del target visible en el gráfico."""
        label = self.preview_target_var.get()
        group = next(
            (g for g in self._target_groups
             if str(g.get("label") or g.get("task_str") or "") == label),
            None)
        return _parse_task_filter(group.get("task_str", "")) if group else {}

    # ==============================================================
    # GRÁFICO
    # ==============================================================
    def _refresh_chart(self):
        """
        Redibuja la serie del KPI.

        - Si hay exactamente 1 binomio marcado → serie SOLO de ese binomio.
        - Si hay 0 o >1 marcados → agregado global.

        ⚠ Protegida contra series vacías (evita ZeroDivisionError).
        """
        self.fig.clear()
        date_col = self.date_var.get()
        kpi_col = self.kpi_var.get()
        gran = self.gran_var.get()

        if (not date_col or date_col not in self.df.columns
                or not kpi_col or kpi_col not in self.df.columns):
            ax = self.fig.add_subplot(111)
            ax.text(0.5, 0.5, "Selecciona fecha y KPI",
                    ha="center", va="center", fontsize=11)
            ax.axis("off")
            self.canvas.draw_idle()
            return

        # ---------- Filtro del target elegido en el combo ----------
        dim_filter = self._selected_preview_filter()
        target_label = self.preview_target_var.get()
        title_suffix = f"  ·  {target_label}" if target_label else ""

        # ---------- Caché de agregación ----------
        filtro_key = tuple(sorted(
            (column, tuple(values)) for column, values in dim_filter.items()
        )) if dim_filter else ()
        cache_key = (f"{date_col}|{kpi_col}|{gran}|{len(self.df)}|"
                     f"{filtro_key}")
        if self._agg_cache_key != cache_key or self._agg_cache is None:
            try:
                if (len(self.df) >= db_engine.UMBRAL_PANDAS
                        and db_engine._DUCKDB_OK):
                    agg = self._agg_con_duckdb(date_col, kpi_col, gran,
                                                 dim_filter)
                else:
                    df = self.df
                    if dim_filter:
                        mask = pd.Series(True, index=df.index)
                        for dc, values in dim_filter.items():
                            if dc in df.columns:
                                mask = mask & df[dc].astype(str).isin(
                                    [str(value) for value in values])
                        df = df[mask]
                    df = df.copy()
                    df[date_col] = pd.to_datetime(
                        df[date_col], errors="coerce")
                    df = df.dropna(subset=[date_col]) \
                           .sort_values(date_col)
                    df[kpi_col] = pd.to_numeric(
                        df[kpi_col], errors="coerce").fillna(0)

                    freq = {"diaria": "D", "semanal": "W-MON",
                             "mensual": "MS"}.get(gran, "W-MON")
                    summed = (df.set_index(date_col)[[kpi_col]]
                                .resample(freq).sum())

                    if len(summed) > 0:
                        full_range = pd.date_range(
                            start=summed.index.min(),
                            end=summed.index.max(),
                            freq=freq,
                        )
                        summed = summed.reindex(full_range,
                                                 fill_value=0)
                        summed.index.name = date_col

                    agg = summed.reset_index()
                    agg.columns = [date_col, kpi_col]
                    agg = (agg.sort_values(date_col)
                              .reset_index(drop=True))

                self._agg_cache = agg
                self._agg_cache_key = cache_key
            except Exception as e:
                print(f"[CI Dialog] Error agregando: {e}")
                self.fig.clear()
                ax = self.fig.add_subplot(111)
                ax.text(0.5, 0.5, f"Error: {e}",
                        ha="center", va="center", fontsize=10)
                ax.axis("off")
                self.canvas.draw_idle()
                return
        else:
            agg = self._agg_cache

        # ============================================================
        # ✅ GUARDA: serie vacía → mostrar mensaje y salir
        # ============================================================
        if agg is None or len(agg) == 0:
            ax = self.fig.add_subplot(111)
            msg = "Sin datos para ese binomio / KPI"
            if dim_filter:
                msg += f"\n{dim_filter}"
            ax.text(0.5, 0.5, msg,
                    ha="center", va="center", fontsize=11)
            ax.axis("off")
            self.canvas.draw_idle()
            return

        # ============================================================
        # Dibujo
        # ============================================================
        try:
            x = np.arange(len(agg))
            y = agg[kpi_col].values
            n_pts = len(agg)

            ax = self.fig.add_subplot(111)
            ax.plot(x, y, color="#58A6FF", lw=2.0, label=kpi_col,
                    zorder=5)
            ax.scatter(x, y, s=16, color="#58A6FF", zorder=6)

            def _clamp(i):
                if i is None:
                    return None
                try:
                    return max(0, min(int(i), n_pts - 1))
                except Exception:
                    return None

            pre_s = _clamp(self.pre_start_idx)
            pre_e = _clamp(self.pre_end_idx)
            post_s = _clamp(self.post_start_idx)
            post_e = _clamp(self.post_end_idx)

            if (pre_s is not None and pre_e is not None
                    and pre_e >= pre_s):
                ax.axvspan(pre_s - 0.5, pre_e + 0.5,
                            color="#FFE082", alpha=0.25,
                            label="PRE", zorder=1)
            if (post_s is not None and post_e is not None
                    and post_e >= post_s):
                ax.axvspan(post_s - 0.5, post_e + 0.5,
                            color="#EF9A9A", alpha=0.30,
                            label="POST", zorder=2)

            # Eventos
            for se in self.events.get("sub_events", []):
                try:
                    s0 = pd.Timestamp(se["start"])
                    s1 = pd.Timestamp(se["end"])
                    mask = ((agg[date_col] >= s0)
                            & (agg[date_col] <= s1))
                    idxs = np.where(mask.values)[0]
                    if len(idxs):
                        i0 = max(0, int(idxs[0]))
                        i1 = min(n_pts - 1, int(idxs[-1]))
                        if i1 >= i0:
                            ax.axvspan(i0 - 0.5, i1 + 0.5,
                                        color="#BC8CFF", alpha=0.35,
                                        zorder=3)
                except Exception:
                    pass

            # ✅ GUARDA: n_ticks nunca puede ser 0
            n_ticks = max(1, min(12, len(agg)))
            step = max(1, len(agg) // n_ticks)
            ticks = list(range(0, len(agg), step))
            labels = [pd.Timestamp(agg[date_col].iloc[i])
                        .strftime("%Y-%m-%d") for i in ticks]
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels, rotation=25, ha="right",
                                fontsize=8)

            ax.set_ylabel(kpi_col, fontsize=10)
            ax.set_title(f"Serie · {kpi_col}{title_suffix}  ({gran})",
                         fontsize=11, fontweight="bold")
            ax.grid(True, alpha=0.3)
            ax.legend(loc="upper left", fontsize=8, ncol=3)

            try:
                self.fig.tight_layout()
            except Exception:
                pass
            self.canvas.draw_idle()

        except Exception as e:
            print(f"[CI Dialog] Error gráfico: {e}")
            import traceback
            traceback.print_exc()
    def _agg_con_duckdb(self, date_col, kpi_col, gran,
                         dim_filter: dict | None = None):
        """
        Agrega con DuckDB aplicando, si se pasa, un filtro por binomio.
        """
        freq_map = {"diaria": "day", "semanal": "week",
                     "mensual": "month"}
        trunc_unit = freq_map.get(gran, "week")

        s = self.df[date_col]
        if not pd.api.types.is_datetime64_any_dtype(s):
            self.df[date_col] = pd.to_datetime(s, errors="coerce")
        try:
            if getattr(self.df[date_col].dt, "tz", None) is not None:
                self.df[date_col] = self.df[date_col].dt.tz_convert(None)
        except Exception:
            pass

        t = db_engine.register(self.df, "_ci_tmp")
        try:
            # --- WHERE: fecha no nula + filtros del binomio ---
            where_parts = [f'"{date_col}" IS NOT NULL']
            if dim_filter:
                for dc, values in dim_filter.items():
                    if dc in self.df.columns:
                        escaped = [
                            "'" + str(value).replace("'", "''") + "'"
                            for value in values
                        ]
                        if escaped:
                            where_parts.append(
                                f'CAST("{dc}" AS VARCHAR) IN '
                                f'({", ".join(escaped)})')
            where_sql = " AND ".join(where_parts)

            sql = f"""
                SELECT
                    DATE_TRUNC('{trunc_unit}', "{date_col}") AS fecha,
                    SUM(CAST("{kpi_col}" AS DOUBLE)) AS kpi
                FROM {t}
                WHERE {where_sql}
                GROUP BY 1
                ORDER BY 1
            """
            agg = db_engine.query(sql)
        finally:
            db_engine.unregister(t)

        agg = agg.rename(columns={"kpi": kpi_col, "fecha": date_col})

        # --- Relleno de huecos temporales ---
        try:
            agg[date_col] = pd.to_datetime(agg[date_col])
            agg = agg.sort_values(date_col).reset_index(drop=True)

            pd_freq = {"diaria": "D", "semanal": "W-MON",
                        "mensual": "MS"}.get(gran, "W-MON")

            if len(agg) > 0:
                full_range = pd.date_range(
                    start=agg[date_col].min(),
                    end=agg[date_col].max(),
                    freq=pd_freq,
                )
                agg = (agg.set_index(date_col)[kpi_col]
                           .reindex(full_range, fill_value=0)
                           .reset_index())
                agg.columns = [date_col, kpi_col]
                agg = agg.sort_values(date_col).reset_index(drop=True)

        except Exception as e:
            print(f"[CI Dialog] Relleno de huecos falló: {e}")

        return agg
    # ==============================================================
    # INTERACCIÓN CON EL RATÓN
    # ==============================================================
    def _toggle_mode(self, mode):
        self.mark_mode = None if self.mark_mode == mode else mode
        colors = {
            None: (COLORS["bg_input"], COLORS["text"]),
            "pre": (COLORS["primary"], "#FFFFFF"),
            "post": (COLORS["danger"], "#FFFFFF"),
            "event": (COLORS["purple"], "#FFFFFF"),
        }
        for btn, m in [(self.btn_mark_pre, "pre"),
                        (self.btn_mark_post, "post"),
                        (self.btn_mark_event, "event")]:
            bg, fg = colors[m] if self.mark_mode == m else colors[None]
            btn.config(bg=bg, fg=fg)

        msgs = {None: "", "pre": "MODO PRE · arrastra",
                "post": "MODO POST · arrastra",
                "event": "MODO EVENTO · arrastra"}
        self.lbl_marks.config(text=msgs.get(self.mark_mode, ""))

    def _on_press(self, event):
        if self.mark_mode is None or event.inaxes is None:
            return
        if event.xdata is None:
            return
        self.drag_start = float(event.xdata)

    def _on_motion(self, event):
        if self.drag_start is None or event.inaxes is None:
            return
        if event.xdata is None:
            return
        self._draw_drag_preview(self.drag_start, float(event.xdata))

    def _on_release(self, event):
        if self.drag_start is None:
            return
        if event.inaxes is None or event.xdata is None:
            self.drag_start = None
            return
        x_end = float(event.xdata)
        x_start = self.drag_start
        self.drag_start = None
        s, e = sorted([x_start, x_end])
        idx_s, idx_e = int(round(s)), int(round(e))
        if idx_s == idx_e:
            return

        if self.mark_mode == "pre":
            self.pre_start_idx = max(0, idx_s)
            self.pre_end_idx = max(0, idx_e)
            self._sync_dates_from_index()
        elif self.mark_mode == "post":
            self.post_start_idx = max(0, idx_s)
            self.post_end_idx = max(0, idx_e)
            self._sync_dates_from_index()
        elif self.mark_mode == "event":
            self._create_event_from_drag(idx_s, idx_e)

        self._refresh_chart()
        self._refresh_global_status()

    def _draw_drag_preview(self, x0, x1):
        """Previsualización sin re-render."""
        ax = self.fig.axes[0] if self.fig.axes else None
        if ax is None:
            return
        if getattr(self, "_drag_patch", None) is not None:
            try:
                self._drag_patch.remove()
            except Exception:
                pass
        color = {"pre": "#FFE082", "post": "#EF9A9A",
                 "event": "#BC8CFF"}.get(self.mark_mode, "#FFFFFF")
        self._drag_patch = ax.axvspan(min(x0, x1), max(x0, x1),
                                        color=color, alpha=0.5, zorder=4)
        self.canvas.draw_idle()

    def _on_scroll(self, event):
        if event.inaxes is None or event.xdata is None:
            return
        step = 1 if getattr(event, "step", 0) > 0 else -1
        scale = 1.0 / 1.2 if step > 0 else 1.2
        ax = event.inaxes
        x = float(event.xdata)
        xlim = ax.get_xlim()
        ax.set_xlim(x - (x - xlim[0]) * scale,
                     x + (xlim[1] - x) * scale)
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

    def _clear_marks(self):
        self.pre_start_idx = None
        self.pre_end_idx = None
        self.post_start_idx = None
        self.post_end_idx = None
        self.pre_start_date_var.set("")
        self.pre_end_date_var.set("")
        self.post_start_date_var.set("")
        self.post_end_date_var.set("")
        self._refresh_chart()
        self._refresh_global_status()

    # ==============================================================
    # SERIES
    # ==============================================================
    def _on_dim_change(self):
        self._last_series_signature = ""

    def _calcular_series(self):
        """
        Detecta TODAS las series únicas combinando las columnas marcadas.
        NO hace split del nombre combinado: usa dicts directamente.
        """
        dim_cols = [c for c, v in self._dim_vars.items() if v.get()]
        if not dim_cols:
            self._series_data = []
            self._excluir_data = []
            self._render_series_list()
            self._render_excluir_list()
            self.lbl_series_status.config(
                text="⚠ Marca al menos una columna",
                fg=COLORS["warning"])
            return

        sep = self.series_sep_var.get() or "_"
        sig = f"{dim_cols}|{sep}"
        if sig == self._last_series_signature and self._series_data:
            self._render_series_list()
            self._render_excluir_list()
            return
        self._last_series_signature = sig

        self.lbl_series_status.config(
            text="Calculando series únicas...",
            fg=COLORS["text_muted"])
        self.update_idletasks()

        try:
            if (len(self.df) >= db_engine.UMBRAL_PANDAS
                    and db_engine._DUCKDB_OK):
                # Ya viene como lista de dicts con display + dim_dict
                self._series_data = self._unique_series_duckdb(
                    dim_cols, sep)
            else:
                sample = self.df
                df_s = sample[dim_cols].fillna("(vacío)").astype(str)
                unique_tuples = df_s.drop_duplicates()
                self._series_data = []
                for _, row in unique_tuples.iterrows():
                    dim_dict = {c: str(row[c]).strip()
                                 for c in dim_cols}
                    display = sep.join(dim_dict[c] for c in dim_cols)
                    self._series_data.append({
                        "display": display,
                        "dim_dict": dim_dict,
                        "task_str": ";".join(
                            f"{c}={v}" for c, v in dim_dict.items()),
                    })
                self._series_data.sort(
                    key=lambda x: x["display"])

            self._excluir_data = list(self._series_data)

            self._render_series_list()
            self._render_excluir_list()

            self.lbl_series_status.config(
                text=f"✓ {len(self._series_data)} series detectadas "
                     f"({len(dim_cols)} col. con '{sep}')",
                fg=COLORS["text"])
            self.lbl_excluir_status.config(
                text=f"{len(self._excluir_data)} series disponibles "
                     f"para excluir",
                fg=COLORS["text"])

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[CI Dialog] Error series: {e}")
            self._series_data = []
            self._excluir_data = []
            self._render_series_list()
            self._render_excluir_list()
            self.lbl_series_status.config(
                text=f"❌ {str(e)[:100]}", fg=COLORS["danger"])
    def _unique_series_duckdb(self, dim_cols, sep):
        """
        Devuelve la lista de series únicas SIN hacer split posterior.

        Devuelve una lista de dicts:
            [{"display": "ES_Madrid",
              "dim_dict": {"Country": "ES",
                            "destination_area_mapped": "Madrid"}},
             ...]

        Así evitamos el bug de tener que reconstruir las columnas
        haciendo split por el separador (que puede aparecer en los
        valores como "Islas_Baleares").
        """
        # Preparar el df con las columnas como str
        df_clean = self.df.copy()
        for c in dim_cols:
            if not pd.api.types.is_string_dtype(df_clean[c]):
                df_clean[c] = df_clean[c].astype(str)

        # Registrar en DuckDB
        t = db_engine.register(df_clean, "_ci_series_tmp")

        try:
            # --- 1 · Consultar los valores únicos POR COLUMNA ---
            cols_sql = ", ".join(f'"{c}"' for c in dim_cols)
            sql = (f"SELECT DISTINCT {cols_sql} FROM {t} "
                   f"ORDER BY {cols_sql}")
            res = db_engine.query(sql)

            if res is None or len(res) == 0:
                print("[CI Dialog] _unique_series_duckdb: 0 filas")
                return []

            # --- 2 · Construir display + dim_dict en Python ---
            series = []
            for _, row in res.iterrows():
                dim_dict = {}
                for c in dim_cols:
                    v = row[c]
                    if pd.isna(v) or str(v).strip() == "":
                        v = "(vacío)"
                    else:
                        v = str(v).strip()
                    dim_dict[c] = v

                display = sep.join(dim_dict[c] for c in dim_cols)
                series.append({
                    "display": display,
                    "dim_dict": dim_dict,
                    "task_str": ";".join(
                        f"{c}={v}" for c, v in dim_dict.items()),
                })

            print(f"[CI Dialog] _unique_series_duckdb OK: "
                  f"{len(series)} series")
            return series

        except Exception as e:
            print(f"[CI Dialog] _unique_series_duckdb falló: {e}")
            # Fallback pandas
            try:
                sample = (self.df if len(self.df) <= 500_000
                          else self.df.head(500_000))
                df_s = sample[dim_cols].fillna("(vacío)").astype(str)
                unique_tuples = df_s.drop_duplicates()
                series = []
                for _, row in unique_tuples.iterrows():
                    dim_dict = {c: str(row[c]).strip()
                                 for c in dim_cols}
                    display = sep.join(dim_dict[c] for c in dim_cols)
                    series.append({
                        "display": display,
                        "dim_dict": dim_dict,
                        "task_str": ";".join(
                            f"{c}={v}" for c, v in dim_dict.items()),
                    })
                series.sort(key=lambda x: x["display"])
                return series
            except Exception as e2:
                print(f"[CI Dialog] fallback también falló: {e2}")
                return []
        finally:
            try:
                db_engine.unregister(t)
            except Exception:
                pass

    def _render_series_list(self):
        try:
            for item in self.series_tree.get_children():
                self.series_tree.delete(item)
        except Exception:
            pass

        if not self._series_data:
            return

        q = self.search_series_var.get().strip().lower()
        if q:
            filtered = [i for i in self._series_data
                        if q in i["display"].lower()]
        else:
            filtered = self._series_data

        MAX_ITEMS = 20_000
        if len(filtered) > MAX_ITEMS:
            filtered = filtered[:MAX_ITEMS]
            self.lbl_series_status.config(
                text=f"⚠ Mostrando {MAX_ITEMS} de "
                     f"{len(self._series_data)} series. Usa el buscador.",
                fg=COLORS["warning"])

        for item in filtered:
            ts = item["task_str"]
            if ts not in self._series_vars:
                self._series_vars[ts] = tk.BooleanVar(value=False)
            check = "☑" if self._series_vars[ts].get() else "☐"
            self.series_tree.insert(
                "", "end",
                values=(check, item["display"]),
                tags=(ts,),
            )

        self._series_filtered = filtered

    def _on_series_tree_doubleclick(self, event):
        row_id = self.series_tree.identify_row(event.y)
        if not row_id:
            return
        self._toggle_series_row(row_id)

    def _on_series_tree_click(self, event):
        """Click simple → toggle checkbox."""
        # Identificar fila bajo el cursor
        row_id = self.series_tree.identify_row(event.y)
        col_id = self.series_tree.identify_column(event.x)

        if not row_id:
            return

        # Solo togglear si el click fue en la columna "✓"
        # (para que el click en el nombre no cambie el estado)
        if col_id != "#1":
            return

        tags = self.series_tree.item(row_id, "tags")
        if not tags:
            return

        ts = tags[0]
        if ts not in self._series_vars:
            self._series_vars[ts] = tk.BooleanVar(value=False)

        new_val = not self._series_vars[ts].get()
        self._series_vars[ts].set(new_val)
        check = "☑" if new_val else "☐"

        # Actualizar solo esta fila
        current = self.series_tree.item(row_id, "values")
        self.series_tree.item(row_id,
                                values=(check, current[1]))

        self._on_series_toggle()

    def _on_series_tree_doubleclick(self, event):
        """Doble click → toggle checkbox."""
        row_id = self.series_tree.identify_row(event.y)
        if not row_id:
            return
        tags = self.series_tree.item(row_id, "tags")
        if not tags:
            return
        ts = tags[0]
        if ts not in self._series_vars:
            self._series_vars[ts] = tk.BooleanVar(value=False)
        new_val = not self._series_vars[ts].get()
        self._series_vars[ts].set(new_val)
        check = "☑" if new_val else "☐"
        current = self.series_tree.item(row_id, "values")
        self.series_tree.item(row_id, values=(check, current[1]))
        self._on_series_toggle()

    def _toggle_series_row(self, row_id: str):
        tags = self.series_tree.item(row_id, "tags")
        if not tags:
            return
        ts = tags[0]
        if ts not in self._series_vars:
            self._series_vars[ts] = tk.BooleanVar(value=False)
        new_val = not self._series_vars[ts].get()
        self._series_vars[ts].set(new_val)
        check = "☑" if new_val else "☐"
        current = self.series_tree.item(row_id, "values")
        self.series_tree.item(row_id,
                                values=(check, current[1]))
        self._on_series_toggle()

    def _on_series_toggle(self):
        """Actualiza el estado interno y refresca el gráfico."""
        self._series_selected = [k for k, v in self._series_vars.items()
                                   if v.get()]
        self._refresh_global_status()
        try:
            self._refresh_chart()
        except Exception as e:
            print(f"[CI Dialog] _refresh_chart: {e}")

    def _refresh_tree_checkmarks(self):
        try:
            for row_id in self.series_tree.get_children():
                tags = self.series_tree.item(row_id, "tags")
                if not tags:
                    continue
                ts = tags[0]
                v = self._series_vars.get(ts)
                check = "☑" if v and v.get() else "☐"
                current = self.series_tree.item(row_id, "values")
                self.series_tree.item(row_id,
                                        values=(check, current[1]))
        except Exception:
            pass

    def _set_all_series(self, value: bool):
        filtered = getattr(self, "_series_filtered", self._series_data)
        for item in filtered:
            ts = item["task_str"]
            if ts not in self._series_vars:
                self._series_vars[ts] = tk.BooleanVar(value=False)
            self._series_vars[ts].set(value)
        self._refresh_tree_checkmarks()
        self._on_series_toggle()

    def _invert_series(self):
        filtered = getattr(self, "_series_filtered", self._series_data)
        for item in filtered:
            ts = item["task_str"]
            if ts not in self._series_vars:
                self._series_vars[ts] = tk.BooleanVar(value=False)
            self._series_vars[ts].set(not self._series_vars[ts].get())
        self._refresh_tree_checkmarks()
        self._on_series_toggle()

    def _refresh_selected_chips(self):
        """
        Obsoleta: el panel de grupos ya muestra los targets.
        Se mantiene como no-op para compatibilidad.
        """
        pass

    def _remove_target_chip(self, task_str):
        """Quita una serie de las marcadas (al pulsar la X)."""
        if task_str in self._series_vars:
            self._series_vars[task_str].set(False)
        self._on_series_toggle()

    def _show_series_preview(self):
        if not self._series_data:
            messagebox.showinfo("Series", "No hay series detectadas.", parent=self)
            return
        lines = [i["display"] for i in self._series_data[:100]]
        extra = (f"\n\n[... +{len(self._series_data)-100} más]"
                 if len(self._series_data) > 100 else "")
        messagebox.showinfo(
            f"Series detectadas ({len(self._series_data)})",
            "\n".join(lines) + extra, parent=self)

    def _unir_targets_seleccionados(self):
        """
        Combina los binomios marcados como TARGET en uno solo.

        Ejemplo:
            UK · Canarias   ┐
                            ├─→ UK-(Canarias|Baleares)
            UK · Baleares   ┘
        """
        if len(self._series_selected) < 2:
            messagebox.showinfo(
                "Unir binomios",
                "Marca al menos 2 binomios como TARGET (✓) antes de "
                "pulsar 'Unir seleccionados'.", parent=self)
            return

        # --- Recoger los dim_dict de cada task_str marcado ---
        dims_list = []
        for ts in self._series_selected:
            info = next(
                (i for i in self._series_data
                 if i["task_str"] == ts),
                None,
            )
            if info is None:
                continue
            d = info["dim_dict"]
            clean = {}
            for k, v in d.items():
                clean[k] = list(v) if isinstance(v, list) else [v]
            dims_list.append(clean)

        if len(dims_list) < 2:
            messagebox.showinfo(
                "Unir binomios",
                "No se pudieron recuperar los binomios seleccionados.", parent=self)
            return

        # --- Columnas comunes ---
        common_cols = set(dims_list[0].keys())
        for d in dims_list[1:]:
            common_cols &= set(d.keys())
        common_cols = sorted(common_cols)
        if not common_cols:
            messagebox.showinfo(
                "Unir binomios",
                "Los binomios seleccionados no comparten las mismas "
                "columnas. No se pueden unir.", parent=self)
            return

        # --- Combinar valores por columna ---
        combined = {}
        for col in common_cols:
            vals = []
            for d in dims_list:
                for v in d[col]:
                    if v not in vals:
                        vals.append(v)
            combined[col] = vals

        # --- task_str combinado ---
        new_task_str = ";".join(
            f"{c}={'|'.join(vals)}" for c, vals in combined.items()
        )

        # --- Display legible ---
        sep = self.series_sep_var.get() or "_"
        parts = []
        for c in common_cols:
            vals = combined[c]
            if len(vals) == 1:
                parts.append(vals[0])
            else:
                parts.append("(" + "|".join(vals) + ")")
        new_display = sep.join(parts)

        # --- Añadirlo (o marcarlo si ya existe) ---
        if new_task_str not in self._series_vars:
            self._series_vars[new_task_str] = tk.BooleanVar(value=True)
        else:
            self._series_vars[new_task_str].set(True)

        if not any(i["task_str"] == new_task_str
                   for i in self._series_data):
            self._series_data.append({
                "display": new_display,
                "dim_dict": combined,
                "task_str": new_task_str,
                "is_combined": True,
            })
            self._series_data.sort(key=lambda x: x["display"])

        # --- Desmarcar los originales ---
        for ts in list(self._series_selected):
            if ts == new_task_str:
                continue
            if ts in self._series_vars:
                self._series_vars[ts].set(False)

        # --- Refrescar ---
        self._render_series_list()
        self._on_series_toggle()

        print(f"[CI Dialog] ✅ Binomio combinado: {new_task_str}")
        messagebox.showinfo(
            "Unir binomios",
            f"Binomio combinado creado:\n\n  {new_display}\n\n"
            f"Se analizará como un único target (suma de las series "
            f"originales).", parent=self)

    def _get_selected_targets(self):
        return [k for k, v in self._series_vars.items() if v.get()]

    def _get_selected_targets(self):
        return [k for k, v in self._series_vars.items() if v.get()]

    # ==============================================================
    # EXCLUIR BINOMIOS
    # ==============================================================
    def _render_excluir_list(self):
        """
        Pinta las series a excluir en el Treeview.

        Carga TODAS las series de una vez (sin paginación).
        """
        try:
            for item in self.excluir_tree.get_children():
                self.excluir_tree.delete(item)
        except Exception:
            pass

        if not self._excluir_data:
            return

        # --- Filtro de búsqueda ---
        q = self.search_excluir_var.get().strip().lower()
        if q:
            filtered = [i for i in self._excluir_data
                        if q in i["display"].lower()]
        else:
            filtered = self._excluir_data

        # --- Límite de seguridad Tkinter ---
        MAX_ITEMS = 20_000
        if len(filtered) > MAX_ITEMS:
            filtered = filtered[:MAX_ITEMS]
            self.lbl_excluir_status.config(
                text=f"⚠ Mostrando {MAX_ITEMS} de "
                     f"{len(self._excluir_data)}. Usa el buscador.",
                fg=COLORS["warning"])

        for item in filtered:
            ts = item["task_str"]
            if ts not in self._excluir_vars:
                self._excluir_vars[ts] = tk.BooleanVar(value=False)
            check = "☑" if self._excluir_vars[ts].get() else "☐"
            self.excluir_tree.insert("", "end",
                                       values=(check, item["display"]),
                                       tags=(ts,))

        self._excluir_filtered = filtered

    def _on_excluir_tree_click(self, event):
        """Click simple → toggle checkbox (solo columna ✓)."""
        row_id = self.excluir_tree.identify_row(event.y)
        col_id = self.excluir_tree.identify_column(event.x)

        if not row_id or col_id != "#1":
            return

        tags = self.excluir_tree.item(row_id, "tags")
        if not tags:
            return

        ts = tags[0]
        if ts not in self._excluir_vars:
            self._excluir_vars[ts] = tk.BooleanVar(value=False)

        new_val = not self._excluir_vars[ts].get()
        self._excluir_vars[ts].set(new_val)
        check = "☑" if new_val else "☐"

        current = self.excluir_tree.item(row_id, "values")
        self.excluir_tree.item(row_id, values=(check, current[1]))

        self._on_excluir_toggle()

    def _on_excluir_tree_doubleclick(self, event):
        """Doble click → toggle checkbox."""
        row_id = self.excluir_tree.identify_row(event.y)
        if not row_id:
            return
        tags = self.excluir_tree.item(row_id, "tags")
        if not tags:
            return
        ts = tags[0]
        if ts not in self._excluir_vars:
            self._excluir_vars[ts] = tk.BooleanVar(value=False)
        new_val = not self._excluir_vars[ts].get()
        self._excluir_vars[ts].set(new_val)
        check = "☑" if new_val else "☐"
        current = self.excluir_tree.item(row_id, "values")
        self.excluir_tree.item(row_id, values=(check, current[1]))
        self._on_excluir_toggle()

    def _on_excluir_toggle(self):
        self._excluir_selected = [k for k, v in self._excluir_vars.items()
                                    if v.get()]
        self._refresh_excluir_chips()
        self._refresh_global_status()

    def _refresh_excluir_chips(self):
        for w in self.chips_excluir_frame.winfo_children():
            w.destroy()

        if not self._excluir_selected:
            tk.Label(self.chips_excluir_frame,
                     text="(ninguno excluido)",
                     bg=COLORS["bg_input"], fg=COLORS["text_dim"],
                     font=FONTS["tiny"]).pack(padx=8, pady=6)
            return

        chip_wrap = tk.Frame(self.chips_excluir_frame,
                              bg=COLORS["bg_input"])
        chip_wrap.pack(fill="x", padx=6, pady=6)

        for task_str in self._excluir_selected:
            display = next(
                (i["display"] for i in self._excluir_data
                 if i["task_str"] == task_str),
                task_str)

            chip = tk.Frame(chip_wrap, bg=COLORS["danger"],
                            highlightbackground="#FFFFFF",
                            highlightthickness=1)
            chip.pack(side="left", padx=(0, 4), pady=2)

            tk.Label(chip, text=display, bg=COLORS["danger"],
                     fg="#FFFFFF", font=FONTS["small"]).pack(
                side="left", padx=(8, 4), pady=3)

            tk.Button(chip, text="×", relief="flat",
                      bg=COLORS["danger"], fg="#FFFFFF",
                      activebackground="#000000",
                      font=("Segoe UI", 10, "bold"),
                      cursor="hand2", width=2,
                      command=lambda ts=task_str:
                          self._remove_excluir_chip(ts)).pack(
                side="left", padx=(0, 3))

    def _refresh_excluir_checkmarks(self):
        """Refresca los ☑/☐ de todas las filas del Treeview de excluir."""
        try:
            for row_id in self.excluir_tree.get_children():
                tags = self.excluir_tree.item(row_id, "tags")
                if not tags:
                    continue
                ts = tags[0]
                v = self._excluir_vars.get(ts)
                check = "☑" if v and v.get() else "☐"
                current = self.excluir_tree.item(row_id, "values")
                self.excluir_tree.item(row_id,
                                        values=(check, current[1]))
        except Exception:
            pass

    def _remove_excluir_chip(self, task_str):
        if task_str in self._excluir_vars:
            self._excluir_vars[task_str].set(False)
        self._on_excluir_toggle()

    # ==============================================================
    # EVENTOS
    # ==============================================================
    def _create_event_from_drag(self, idx_start, idx_end):
        date_col = self.date_var.get()
        if not date_col or date_col not in self.df.columns:
            return

        start_ts = None
        end_ts = None

        # Usar caché si existe
        if (self._agg_cache is not None
                and len(self._agg_cache) > 0
                and date_col in self._agg_cache.columns):
            try:
                agg_dates = pd.to_datetime(
                    self._agg_cache[date_col]).values
                n_pts = len(agg_dates)
                i0 = max(0, min(idx_start, n_pts - 1))
                i1 = max(0, min(idx_end, n_pts - 1))
                start_ts = pd.Timestamp(agg_dates[i0])
                end_ts = pd.Timestamp(agg_dates[i1])
                if end_ts < start_ts:
                    start_ts, end_ts = end_ts, start_ts
            except Exception as e:
                print(f"[CI Dialog] Error leyendo caché: {e}")

        if start_ts is None:
            try:
                df = self.df[[date_col]].copy()
                df[date_col] = pd.to_datetime(df[date_col],
                                                errors="coerce")
                df = df.dropna(subset=[date_col]).sort_values(date_col)
                gran = self.gran_var.get()
                freq = {"diaria": "D", "semanal": "W-SUN",
                         "mensual": "MS"}.get(gran, "W-SUN")
                agg_dates = (df.set_index(date_col).resample(freq)
                               .size().index)
                n_pts = len(agg_dates)
                i0 = max(0, min(idx_start, n_pts - 1))
                i1 = max(0, min(idx_end, n_pts - 1))
                start_ts = pd.Timestamp(agg_dates[i0])
                end_ts = pd.Timestamp(agg_dates[i1])
                if end_ts < start_ts:
                    start_ts, end_ts = end_ts, start_ts
            except Exception as e:
                print(f"[CI Dialog] Error creando evento: {e}")
                return

        if start_ts is None or end_ts is None:
            return

        n_existing = len(self.events.get("sub_events", []))
        name = f"Evento {n_existing + 1}"

        try:
            from core.events import add_sub_event, save_events
            add_sub_event(self.events, name=name,
                           start=start_ts, end=end_ts,
                           intensity=1.0, group="", own=False)
            save_events(self.dataset_name, self.events)
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)
            return

        self._refresh_events_list()
        self._toast_ci(f"'{name}' · {start_ts.date()} → {end_ts.date()}")

    def _refresh_events_list(self):
        """Lista de eventos con chips (X para borrar)."""
        for w in self.events_frame.winfo_children():
            w.destroy()

        subs = self.events.get("sub_events", [])
        if not subs:
            tk.Label(self.events_frame,
                     text="(ninguno · usa ➕ Añadir evento)",
                     bg=COLORS["bg_input"], fg=COLORS["text_dim"],
                     font=FONTS["small"], wraplength=460,
                     justify="left").pack(padx=8, pady=8)
            return

        chip_wrap = tk.Frame(self.events_frame, bg=COLORS["bg_input"])
        chip_wrap.pack(fill="x", padx=6, pady=6)

        for se in subs:
            chip = tk.Frame(chip_wrap, bg=COLORS["purple"],
                            highlightbackground="#FFFFFF",
                            highlightthickness=1)
            chip.pack(fill="x", padx=2, pady=2)

            txt = f"{se['name']} · {se['start']} → {se['end']}"
            tk.Label(chip, text=txt, bg=COLORS["purple"],
                     fg="#FFFFFF", font=FONTS["small"],
                     anchor="w").pack(side="left", padx=(8, 4),
                                       pady=3, fill="x", expand=True)

            tk.Button(chip, text="✎", relief="flat",
                      bg=COLORS["purple"], fg="#FFFFFF",
                      activebackground=COLORS["bg_hover"],
                      font=("Segoe UI", 10, "bold"), cursor="hand2",
                      width=2,
                      command=lambda sid=se["id"], name=se["name"]:
                          self._rename_event(sid, name)).pack(
                side="right", padx=(3, 0))

            tk.Button(chip, text="×", relief="flat",
                      bg=COLORS["purple"], fg="#FFFFFF",
                      activebackground=COLORS["danger"],
                      font=("Segoe UI", 10, "bold"),
                      cursor="hand2", width=2,
                      command=lambda sid=se["id"]:
                          self._delete_event(sid)).pack(
                side="right", padx=(3, 3))

    def _delete_event(self, sub_id):
        from core.events import remove_sub_event, save_events
        remove_sub_event(self.events, sub_id)
        save_events(self.dataset_name, self.events)
        self._refresh_events_list()
        self._refresh_chart()

    def _rename_event(self, sub_id: str, current_name: str):
        """Renombra un evento directamente desde su fila."""
        name = simpledialog.askstring(
            "Renombrar evento", "Nombre de la variable de evento:",
            initialvalue=current_name, parent=self)
        if not name or not name.strip():
            return
        from core.events import update_sub_event
        if not update_sub_event(self.events, sub_id, name=name.strip()):
            return
        try:
            save_events(self.dataset_name, self.events)
        except Exception as exc:
            update_sub_event(self.events, sub_id, name=current_name)
            messagebox.showerror("Renombrar evento", str(exc), parent=self)
            return
        self._refresh_events_list()
        self._refresh_chart()

    # ==============================================================
    # PERSISTENCIA DE LA SESIÓN
    # ==============================================================
    def _last_config_payload(self) -> dict:
        text_vars = (
            "alpha_var", "series_sep_var",
        )
        payload = {
            "version": 1,
            "date_col": self.date_var.get(),
            "kpis": self._get_selected_kpis(),
            "granularidad": self.gran_var.get(),
            "dimensions": [c for c, v in self._dim_vars.items() if v.get()],
            "external_variables": [
                c for c, v in self._sesgo_vars.items() if v.get()],
            "excluded_controls": list(self._excluir_selected),
            "target_groups": copy.deepcopy(self._target_groups),
            "pre_start": self.pre_start_date_var.get().strip(),
            "pre_end": self.pre_end_date_var.get().strip(),
            "post_start": self.post_start_date_var.get().strip(),
            "post_end": self.post_end_date_var.get().strip(),
        }
        payload.update({name: getattr(self, name).get() for name in text_vars})
        return payload

    def _save_last_config(self):
        payload = self._last_config_payload()
        write_json_atomic(_causal_config_path(self.dataset_name), payload)
        self._saved_config = payload

    def _restore_last_config(self) -> bool:
        """Aplica solo valores que todavía encajan con el dataset abierto."""
        cfg = self._saved_config
        if not cfg:
            return False

        columns = {str(c) for c in self.df.columns}
        date_col = cfg.get("date_col")
        if date_col in columns:
            self.date_var.set(date_col)
        if cfg.get("granularidad") in {"diaria", "semanal", "mensual"}:
            self.gran_var.set(cfg["granularidad"])

        for name in ("alpha_var", "series_sep_var"):
            if cfg.get(name) is not None:
                getattr(self, name).set(str(cfg[name]))
        selected_kpis = {str(c) for c in cfg.get("kpis", [])}
        if selected_kpis:
            for name, var in self._kpi_vars.items():
                var.set(name in selected_kpis and name in columns)
            restored_kpis = self._get_selected_kpis()
            if restored_kpis:
                self.kpi_var.set(restored_kpis[0])
        self._refresh_kpi_count()

        selected_dims = {str(c) for c in cfg.get("dimensions", [])}
        for name, var in self._dim_vars.items():
            var.set(name in selected_dims and name in columns)
        selected_externals = {str(c) for c in cfg.get("external_variables", [])}
        for name, var in self._sesgo_vars.items():
            var.set(name in selected_externals and name in columns)

        self._target_groups = []
        seen_tasks = set()
        for group in cfg.get("target_groups", []):
            if not isinstance(group, dict):
                continue
            task_str = str(group.get("task_str") or "").strip()
            parsed = _parse_task_filter(task_str)
            if (not task_str or task_str in seen_tasks or not parsed
                    or any(column not in columns or column not in selected_dims
                           for column in parsed)):
                continue
            dims = group.get("dims")
            self._target_groups.append({
                "label": str(group.get("label") or task_str),
                "task_str": task_str,
                "dims": copy.deepcopy(dims) if isinstance(dims, list) else [],
            })
            seen_tasks.add(task_str)

        self._on_dim_change()
        if selected_dims:
            self._calcular_series()
        excluded = {str(item) for item in cfg.get("excluded_controls", [])}
        for task_str, var in self._excluir_vars.items():
            var.set(task_str in excluded)
        self._on_excluir_toggle()
        try:
            self._refresh_preview_selectors()
        except Exception:
            pass

        periods = {
            "pre_start": self.pre_start_date_var,
            "pre_end": self.pre_end_date_var,
            "post_start": self.post_start_date_var,
            "post_end": self.post_end_date_var,
        }
        for key, var in periods.items():
            var.set(str(cfg.get(key) or "").strip())
        return all(var.get().strip() for var in periods.values())

    def _clear_last_config(self):
        if not messagebox.askyesno(
                "Limpiar sesión de Causal Impact",
                "¿Eliminar los parámetros guardados para este dataset?", parent=self):
            return
        try:
            _causal_config_path(self.dataset_name).unlink(missing_ok=True)
        except Exception as exc:
            messagebox.showerror("Limpiar sesión", str(exc), parent=self)
            return
        self._saved_config = {}
        self._reset_current_session()
        self._toast_ci("Sesión de Causal Impact limpiada")

    def _reset_current_session(self):
        """Restaura los valores iniciales del diálogo sin volver a abrirlo."""
        date_cols = [str(column) for column in get_date_columns(self.df)]
        self.date_var.set(date_cols[0] if date_cols else "")
        self.gran_var.set("semanal")
        self.series_sep_var.set("_")

        for name, value in {
                "alpha_var": "0.05"}.items():
            getattr(self, name).set(value)

        for var in self._kpi_vars.values():
            var.set(False)
        if self._kpi_vars:
            first_kpi = next(iter(self._kpi_vars))
            self._kpi_vars[first_kpi].set(True)
            self.kpi_var.set(first_kpi)
        self._refresh_kpi_count()

        for vars_by_name in (self._dim_vars, self._sesgo_vars,
                             self._series_vars, self._excluir_vars):
            for var in vars_by_name.values():
                var.set(False)
        self._series_selected = []
        self._excluir_selected = []
        self._target_groups.clear()
        self._last_series_signature = ""
        self.search_series_var.set("")
        self.search_excluir_var.set("")
        self._series_data = []
        self._excluir_data = []
        self._render_series_list()
        self._render_excluir_list()
        self._refresh_excluir_chips()
        self._render_targets_list()
        try:
            self._refresh_preview_selectors()
        except Exception:
            pass

        self.pre_start_idx = self.pre_end_idx = None
        self.post_start_idx = self.post_end_idx = None
        for var in (self.pre_start_date_var, self.pre_end_date_var,
                    self.post_start_date_var, self.post_end_date_var):
            var.set("")
        self._agg_cache = None
        self._agg_cache_key = None
        self._refresh_chart()
        self._initialize_defaults()
        self._refresh_global_status()

    # ==============================================================
    # ESTADO GLOBAL / PRE-FLIGHT
    # ==============================================================
    def _refresh_global_status(self):
        kpi = self.kpi_var.get() or "—"
        n_dims = sum(1 for v in self._dim_vars.values() if v.get())
        n_targets = len(self._target_groups)
        n_excluidos = len(self._excluir_selected)
        n_ext = sum(1 for v in self._sesgo_vars.values() if v.get())

        txt = (f"{kpi}  ·  {n_dims} col  ·  "
               f"{n_targets} target  ·  "
               f"{n_excluidos} excluidos  ·  "
               f"{n_ext} externas")
        self.lbl_global_status.config(text=txt, fg=COLORS["text"])

        errors, warnings = self._preflight_check()
        if errors:
            short = " · ".join(errors[:2])
            if len(errors) > 2:
                short += f" (+{len(errors)-2})"
            self.lbl_footer_status.config(
                text=f"❌ {short}", fg=COLORS["danger"])
            self.btn_run.state(["disabled"])
        elif warnings:
            short = " · ".join(warnings[:2])
            if len(warnings) > 2:
                short += f" (+{len(warnings)-2})"
            self.lbl_footer_status.config(
                text=f"⚠ {short}", fg=COLORS["warning"])
            self.btn_run.state(["!disabled"])
        else:
            self.lbl_footer_status.config(
                text="✓ Listo", fg=COLORS["success"])
            self.btn_run.state(["!disabled"])

    def _preflight_check(self):
        errors, warnings = [], []

        if not self.date_var.get():
            errors.append("Falta fecha")
        if not self._get_selected_kpis():
            errors.append("Marca al menos 1 KPI")

        pre_ok = (self.pre_start_date_var.get().strip()
                  and self.pre_end_date_var.get().strip())
        post_ok = (self.post_start_date_var.get().strip()
                   and self.post_end_date_var.get().strip())
        if not pre_ok:
            errors.append("Marca o escribe PRE")
        if not post_ok:
            errors.append("Marca o escribe POST")

        if pre_ok and post_ok:
            try:
                ps = pd.Timestamp(self.pre_start_date_var.get().strip())
                pe = pd.Timestamp(self.pre_end_date_var.get().strip())
                qs = pd.Timestamp(self.post_start_date_var.get().strip())
                qe = pd.Timestamp(self.post_end_date_var.get().strip())
                if ps >= pe:
                    errors.append("PRE: inicio ≥ fin")
                if qs >= qe:
                    errors.append("POST: inicio ≥ fin")
                if pe >= qs:
                    errors.append("PRE y POST se solapan")
            except Exception:
                errors.append("Formato de fecha inválido")

        if not self._target_groups:
            errors.append("Añade al menos 1 grupo a analizar")

        return errors, warnings
    # ==============================================================
    # EXECUTE
    # ==============================================================
    def _on_execute(self):
        errors, warnings = self._preflight_check()
        if errors:
            messagebox.showerror(
                "Configuración incompleta",
                "\n".join(f"· {e}" for e in errors), parent=self)
            return

        date_col = self.date_var.get()

        # ⚠ KPIs SELECCIONADOS (multi)
        kpi_cols = self._get_selected_kpis()
        print(f"[CI Dialog] ▶ KPIs a analizar ({len(kpi_cols)}): {kpi_cols}")
        if not kpi_cols:
            messagebox.showerror(
                "Sin KPIs",
                "Marca al menos un KPI con la casilla ☐.", parent=self)
            return

        # Grupos (targets)
        if not self._target_groups:
            messagebox.showerror(
                "Sin grupos",
                "Añade al menos un grupo a analizar.", parent=self)
            return

        # Fechas
        try:
            fecha_campana = pd.Timestamp(
                self.post_start_date_var.get().strip())
            fecha_fin = pd.Timestamp(
                self.post_end_date_var.get().strip())
        except Exception as e:
            messagebox.showerror("Error fechas", str(e), parent=self)
            return

        dim_cols = [c for c, v in self._dim_vars.items() if v.get()]
        sesgos_cols = [c for c, v in self._sesgo_vars.items() if v.get()]
        controles_excluidos = list(self._excluir_selected)

        target_tasks = "\n".join(
            g["task_str"] for g in self._target_groups
        )

        def _f(v, default=0.0):
            try:
                return float(str(v.get()).replace(",", "."))
            except Exception:
                return default

        def _i(v, default=0):
            try:
                return int(float(str(v.get()).replace(",", ".")))
            except Exception:
                return default

        options = {
            "date_col": date_col,
            "kpi_cols": kpi_cols,          # ← LISTA
            # La capa que lanza plugins conserva ``kpi_col``. El motor
            # acepta aquí la lista para mantener la selección múltiple.
            "kpi_col": list(kpi_cols),
            "dim_cols": dim_cols,
            "target_tasks": target_tasks,
            "sesgos_cols": sesgos_cols,
            "controles_excluidos": controles_excluidos,
            "fecha_campana": str(fecha_campana.date()),
            "fecha_fin_datos": str(fecha_fin.date()),
            "granularidad": self.gran_var.get(),
            "umbral_correlacion": 0.5,
            "min_controles": 1,
            "max_controles": 3,
            "max_combinaciones": 50,
            "top_n_controles": 10,
            "alpha": _f(self.alpha_var, 0.05),
            "prior_level_sd": 0.05,
            "dynamic_regression": True,
            "standardize_data": True,
        }
        try:
            self._save_last_config()
        except Exception as exc:
            messagebox.showwarning(
                "Preferencias no guardadas",
                f"El análisis se ejecutará, pero no se pudo guardar la sesión:\n{exc}", parent=self)
        source = self.df_full
        events = copy.deepcopy(self.events)

        def work(cancel, progress):
            return prepare_causal_data(source, events, date_col, kpi_cols, cancel)

        def done(aug):
            self._causal_active = False
            self.result = {"df": aug, **options}
            self.destroy()

        def failed(exc):
            self._causal_active = False
            self.btn_run.state(["!disabled"])
            messagebox.showerror("Error al preparar Causal Impact", str(exc), parent=self)

        def cancelled():
            self._causal_active = False
            self.result = None
            self.destroy()

        if self.master.tasks.start(work, on_result=done,
                                   on_error=failed, on_cancel=cancelled):
            self._causal_active = True
            self.btn_run.state(["disabled"])
        else:
            messagebox.showwarning("Aviso", "Ya hay una tarea en curso.", parent=self)

    def _sesgos_vars_items(self):
        return list(self._sesgo_vars.items())
    def _on_cancel(self):
        if getattr(self, "_causal_active", False):
            self.master.tasks.cancel()
            return
        self.result = None
        self.destroy()

    def _toast_ci(self, msg):
        try:
            Toast(self.master, msg, kind="info", duration=1800)
        except Exception:
            pass
