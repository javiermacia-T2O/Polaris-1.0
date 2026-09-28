"""App de escritorio (Tkinter) — Dashboard profesional."""

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
warnings.filterwarnings("ignore", category=UserWarning, module="joblib")
warnings.filterwarnings("ignore", category=RuntimeWarning, module="numpy")

import sys
import io
import traceback
import time
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import (
    FigureCanvasTkAgg, NavigationToolbar2Tk,
)

from core.loader import (
    load_file, detect_date_column, aggregate_period,
    get_date_columns, detect_granularity, coerce_date_column,
)

from core import engine as db_engine
from core import memory_budget

from core.events import append_regression_history

from core.plugin_loader import (
    discover_analyses, is_analysis_loaded, load_analysis,
)
from core.atomic import atomic_output
from core.runtime_paths import writable_root
from core.tasks import TaskManager, TaskCancelled
from core.exporter import export_dataframe, save_figure_isolated
from theme import (
    COLORS, FONTS, apply_theme, style_matplotlib,
    GlassCard, Toast,
)

from ui.figure_utils import (
    _is_matplotlib, _is_plotly, _plotly_to_matplotlib, extract_figures,
)
from ui.window_position import center_popup
from ui.dialogs.preflight_dialog import PreflightDialog
from ui.dialogs.export_dialog import ExportDialog
from ui.dialogs.analysis_config_dialog import AnalysisConfigDialog
from ui.dialogs.merge_dialog import MergeDialog
from ui.dialogs.regression_dialog import (
    CHART_PALETTE, CHART_TYPES, REGRESSION_TYPES, RegressionDialog,
)
from ui.dialogs.causal_impact_dialog import CausalImpactDialog, _dims_fingerprint
from ui.dialogs.geox_dialog import GeoXDialog
from services.table_service import (
    build_table_preview_pandas,
    build_table_view, build_table_active_snapshot,
    build_table_full_pandas, preview_to_pandas,
)
from services.data_service import (
    convert_types, detect_types, ensure_date_sorted, filter_values,
    prepare_df_for_analysis, read_dataset, limited_unique_strings, preview_page,
)
from services.analysis_service import run_analysis
from services.result_bundle import save_result_bundle
from services.active_dataset import ActiveDataset, LazyLoaded
from models.session_state import SessionState
from models.table_recipe import TableRecipe

FREQ_MAP = {
    "Original": "Original", "Diario": "D", "Semanal": "W",
    "Mensual": "M", "Trimestral": "Q", "Anual": "Y",
}
TYPE_OPTIONS = ["auto", "fecha", "numero", "categorica", "texto", "ignorar"]
AGG_OPTIONS = ["sum", "mean", "max", "min", "count"]

AXIS_OPTIONS = ["Izq", "Der"]


_ANALYSIS_CONTEXT_KEYS = (
    "Estado", "Detalle", "Diagnóstico", "Solución", "quality_status",
    "Diagnóstico de calidad", "Interpretación", "Advertencia_Metodologica",
)


def _attach_analysis_context(result):
    """Convierte estado y avisos escalares en una tabla visible/exportable."""
    if not isinstance(result, dict):
        return result
    rows = []
    for key in _ANALYSIS_CONTEXT_KEYS:
        value = result.get(key)
        if value is None or isinstance(value, (pd.DataFrame, dict, list, tuple)):
            continue
        rendered = str(value).strip()
        if rendered:
            rows.append({"Campo": key, "Valor": rendered})
    if not rows or "Contexto del análisis" in result:
        return result
    return {**result, "Contexto del análisis": pd.DataFrame(rows)}


def _regression_history_metrics(result):
    """Compatibilidad entre el nombre legado y el diagnóstico in-sample."""
    if not isinstance(result, dict):
        return None
    for key in ("Métricas del modelo", "Métricas in-sample (diagnóstico)"):
        value = result.get(key)
        if isinstance(value, pd.DataFrame):
            return value
    return None


def _collect_result_dataframes(result):
    """Recoge tablas de cualquier nivel para vista y exportación."""
    tables = {}

    def collect(name, value):
        if isinstance(value, pd.DataFrame):
            base = (name or "resultado").strip()[:60]
            sheet = base
            suffix = 1
            while sheet in tables:
                sheet = f"{base}_{suffix}"
                suffix += 1
            tables[sheet] = value
        elif isinstance(value, dict):
            for key, nested in value.items():
                collect(f"{name} · {key}" if name else str(key), nested)
        elif isinstance(value, (list, tuple)):
            for index, nested in enumerate(value, start=1):
                collect(f"{name} · {index}" if name else str(index), nested)

    collect("", result)
    return tables



# ==================================================================
# APP
# ==================================================================
class MMMApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.session = SessionState()
        self.tasks = TaskManager(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.title("ANÁLISIS MEDICIÓN")
        self.geometry("1480x920")
        self.minsize(1200, 760)
        try:
            self.attributes("-alpha", 0.985)
        except Exception:
            pass

        apply_theme(self)

        # ---------------- Estado ----------------
        # Los DataFrames y sus filtros viven en self.session. Las propiedades
        # conservan la API de MMMApp y no copian datos.
        self.tb_rows: list[str] = []
        self.tb_cols: list[str] = []
        self.tb_vals: list[dict] = []
        self.tb_pivot_enabled = False
        self._tb_preview_revision = 0
        self._tb_builder_dataset = None
        self.tb_filters: dict[str, set] = {}
        self.tb_col_types: dict[str, str] = {}
        self._tb_filter_values_cache: dict[tuple, tuple[str, ...]] = {}

        # --- Visibilidad y filtros de la pestaña Datos ---
        self.visible_columns: list[str] = []
        # --- Cachés internos ---
        self._kpi_cache = None
        self._kpi_cache_key = None
        self._dtype_cache = None
        self._dtype_cache_key = None
        self._nunique_cache = {}   # (id_df, col) → int

        # --- Resultados ---
        self.result: dict | None = None
        self.figures: list = []
        self._figures_all: list = []
        self._figure_names: dict = {}
        self._kpi_filters: list[str] = []
        self._target_filters: list[str] = []
        self._canales: list[str] = []
        self._current_canal: str = "Todos"
        self.plot_idx = 0
        self.console_buffer = io.StringIO()
        self._preview_page = 0
        self._preview_source_id = None
        self._lazy_preview_key = None
        self._pandas_filter_key = None

        self.type_vars: dict[str, tk.StringVar] = {}

        # Popup estado (pestaña Datos)
        self._vis_vars: dict[str, tk.BooleanVar] = {}
        self._filter_values_vars: dict[str, tk.BooleanVar] = {}
        self._filter_col_var: tk.StringVar | None = None
        self._last_filter_col: str | None = None

        self.analyses = discover_analyses()
        self.chosen_analysis = None
        self._analysis_chips: list = []

        # ---------------- Construcción UI ----------------
        self._build_ui()

        # ---------------- Carga de datos inicial ----------------
        self._refresh_analyses()
        self._refresh_preview()
        self._refresh_header()
        self._refresh_kpis()
        self._refresh_console()
        self._refresh_dataset_combo()

        # Configuración final de pestañas y atajos.
        try:
            self._tb_refresh_source()
            self._tb_refresh_lists()
            self._tb_update_preview()
        except Exception as e:
            print(f"[WARN] init tabla: {e}")
        self._render_plots()
        self.bind("<Control-o>", lambda e: self.on_open_file())
        self.bind("<Control-r>", lambda e: self.on_run_analysis())
        self.bind("<F5>", lambda e: self.on_run_analysis())
        self.bind("<Control-l>", lambda e: self.on_clear_console())
        self.after(300, lambda: self._toast(
            "Listo. Pulsa Ctrl+O o '📂 Abrir archivo' para empezar.",
            "info", 4200))

    @property
    def df_raw(self):
        return self.session.df_raw

    @df_raw.setter
    def df_raw(self, value):
        self.session.df_raw = value

    @property
    def df_view(self):
        return self.session.df_view

    @df_view.setter
    def df_view(self, value):
        self.session.df_view = value

    @property
    def file_path(self):
        return self.session.file_path

    @file_path.setter
    def file_path(self, value):
        self.session.file_path = value

    @property
    def _df_original(self):
        return self.session.df_original

    @_df_original.setter
    def _df_original(self, value):
        self.session.df_original = value

    @property
    def df_table(self):
        return self.session.df_table

    @df_table.setter
    def df_table(self, value):
        self.session.df_table = value

    @property
    def loaded_datasets(self):
        return self.session.loaded_datasets

    @loaded_datasets.setter
    def loaded_datasets(self, value):
        self.session.loaded_datasets = value

    @property
    def active_dataset_name(self):
        return self.session.active_dataset_name

    @active_dataset_name.setter
    def active_dataset_name(self, value):
        self.session.active_dataset_name = value

    @property
    def column_types(self):
        return self.session.column_types

    @column_types.setter
    def column_types(self, value):
        self.session.column_types = value

    @property
    def value_filters(self):
        return self.session.value_filters

    @value_filters.setter
    def value_filters(self, value):
        self.session.value_filters = value

    @property
    def _view_is_built(self):
        return self.session.view_is_built

    @_view_is_built.setter
    def _view_is_built(self, value):
        self.session.view_is_built = value

    def _on_close(self):
        self._progress_message("Cerrando tras finalizar la tarea activa...")
        self.tasks.close(self.destroy)

    def _start_task(self, work, on_result, label):
        if self.tasks.busy or self.tasks.closing:
            self._toast("Ya hay una tarea en curso.", "warning")
            return False
        from core.diagnostics import debug_enabled, log_debug, memory_snapshot
        debug = debug_enabled()
        started_at = time.perf_counter()
        if debug:
            log_debug("WORKER", "start", task=label)
            log_debug("MEM", "worker start", **memory_snapshot())
        self._log(f"[TAREA] {label}")
        self._progress_start(label)

        def result(value):
            self._progress_stop()
            elapsed = round(time.perf_counter() - started_at, 3)
            if debug:
                log_debug("WORKER", "finish", task=label, seconds=elapsed)
                log_debug("MEM", "worker finish", **memory_snapshot())
            self._log(f"[FIN] {label} ({elapsed:.1f} s)")
            try:
                on_result(value)
            except Exception as exc:
                error(exc)

        def error(exc):
            self._progress_stop()
            from core.diagnostics import log_task_error
            log_task_error(label, exc)
            if debug:
                log_debug("ERROR", "worker failure", task=label,
                          error=repr(exc))
            self._log(f"[ERROR] {exc}")
            self._log("".join(traceback.format_exception(exc)))
            self._toast(f"Error: {exc}", "error", 7000)

        def cancelled():
            self._progress_stop()
            if debug:
                log_debug("WORKER", "cancel", task=label)
            self._log(f"[CANCELADA] {label}")
            self._toast("Tarea cancelada.", "info", 2500)

        started = self.tasks.start(
            work, on_result=result, on_error=error,
            on_progress=self._progress_message, on_cancel=cancelled)
        if not started:
            self._progress_stop()
        return started
    # ==============================================================
    # ESTRUCTURA GENERAL
    # ==============================================================
    def _build_ui(self):
        self._build_menu()
        self._build_header()
        self._build_body()

    def _build_menu(self):
        """Acciones de la aplicación; las secciones 1–3 siguen en sidebar."""
        bar = tk.Menu(self)
        archivo = tk.Menu(bar, tearoff=False)
        archivo.add_command(label="Abrir archivo...", command=self.on_open_file)
        archivo.add_command(label="Añadir dataset...", command=self.on_add_file_to_pool)
        archivo.add_command(label="Unir datasets...", command=self.on_open_merge_dialog)
        archivo.add_command(label="Elegir dataset activo", command=lambda: self.combo_dataset.focus_set())
        archivo.add_command(label="Quitar del pool", command=self.on_remove_from_pool)
        archivo.add_separator()
        archivo.add_command(label="Exportar vista previa...", command=self.on_export_view)
        bar.add_cascade(label="Archivo", menu=archivo)

        editar = tk.Menu(bar, tearoff=False)
        editar.add_command(label="Elegir columna de fecha", command=lambda: self.combo_date.focus_set())
        editar.add_command(label="Elegir granularidad", command=lambda: self.combo_freq.focus_set())
        editar.add_command(label="Aplicar filtros", command=self.on_apply_filters)
        editar.add_command(label="Restablecer filtros", command=self.on_reset_filters)
        editar.add_separator()
        editar.add_command(label="Limpiar consola", command=self.on_clear_console)
        editar.add_command(label="Guardar consola", command=self.on_save_console)
        bar.add_cascade(label="Editar", menu=editar)

        analisis = tk.Menu(bar, tearoff=False)
        analisis.add_command(label="Elegir análisis", command=lambda: self.analysis_combo.focus_set())
        analisis.add_command(label="Ejecutar análisis", command=self.on_run_analysis)
        analisis.add_separator()
        analisis.add_command(label="Guardar resultados y gráficos...", command=self.on_download_result)
        analisis.add_command(label="Guardar gráfico actual...", command=self.on_save_plot)
        analisis.add_command(label="Guardar todos los gráficos...", command=self.on_save_all_plots)
        bar.add_cascade(label="Análisis", menu=analisis)

        herramientas = tk.Menu(bar, tearoff=False)
        herramientas.add_command(label="Optimizar CSV grande", command=self.on_optimize_csv)
        herramientas.add_command(label="Mapear hoteles a destinos", command=self.on_map_hotels)
        herramientas.add_command(label="Limpiar caché Parquet", command=self.on_clear_cache)
        herramientas.add_separator()
        herramientas.add_command(label="Liberar memoria", command=lambda: self.free_all_memory(hard=False))
        herramientas.add_command(label="Reset memoria (duro)", command=lambda: self.free_all_memory(hard=True))
        bar.add_cascade(label="Herramientas", menu=herramientas)
        self.config(menu=bar)
        self.main_menu = bar

    def on_download_result(self):
        """Guarda todas las tablas y figuras del último análisis juntas."""
        self._save_analysis_outputs(include_tables=True,
                                    figures=tuple(self._figures_all or self.figures))

    def _choose_result_destination(self):
        default = writable_root() / "output"
        default.mkdir(parents=True, exist_ok=True)
        root = filedialog.askdirectory(
            parent=self, title="Directorio para los resultados",
            initialdir=str(default))
        if not root:
            return None
        day = Path(root) / pd.Timestamp.now().strftime("%d-%m-%Y")
        existing = sorted(path.name for path in day.iterdir() if path.is_dir()) \
            if day.is_dir() else []
        dialog = tk.Toplevel(self)
        dialog.title("Carpeta de cliente")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=14)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Nombre nuevo o carpeta existente del cliente:").pack(
            anchor="w", pady=(0, 8))
        client = ttk.Combobox(panel, values=existing, width=42)
        client.pack(fill="x")
        client.set(getattr(self, "_result_client_name", ""))
        client.focus_set()
        choice = []

        def accept():
            name = client.get().strip()
            if name:
                choice.append(name)
                dialog.destroy()

        buttons = ttk.Frame(panel)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(
            side="right")
        ttk.Button(buttons, text="Guardar", command=accept).pack(
            side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _event: accept())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        center_popup(dialog, self, 420, 155)
        self.wait_window(dialog)
        if not choice:
            return None
        self._result_client_name = choice[0]
        return Path(root), choice[0]

    def _result_tables(self):
        return _collect_result_dataframes(self.result)

    def _save_analysis_outputs(self, *, include_tables, figures):
        tables = self._result_tables() if include_tables else {}
        if not tables and not figures:
            self._toast("No hay resultados que guardar.", "warning")
            return
        destination = self._choose_result_destination()
        if destination is None:
            return
        root, client = destination
        name = getattr(self, "_last_analysis_name", None) or (
            self.chosen_analysis["name"] if self.chosen_analysis else "Análisis")
        graphs = {}
        for index, figure in enumerate(figures, start=1):
            style_matplotlib(figure)
            label = getattr(figure, "_mmm_name", None) or \
                self._figure_names.get(id(figure), "grafico")
            graphs[f"{index:02d}_{label}"] = figure

        def work(cancel, progress):
            return save_result_bundle(
                root, client, name, tables=tables, figures=graphs,
                cancel=cancel,
                progress=lambda item: progress((round(item[0] * 100), item[1])))

        def done(bundle):
            self._last_bundle_dir = bundle.path
            self._toast(f"Resultados guardados en {bundle.path.name}",
                        "success", 4000)
            self._log(f"[OK] Resultados y gráficos guardados en {bundle.path}")

        self._start_task(work, done, f"Guardando: {name}")

    # ==============================================================
    # MAPEO HOTEL → DESTINO
    # ==============================================================
    def on_map_hotels(self):
        """
        Añade la columna 'destination_area_mapped' al dataset activo
        a partir de 'Hotel_short_name' (u otra columna seleccionable).
        """
        if self.df_raw is None:
            self._toast("Carga un dataset primero.", "warning")
            return

        from core import destination_mapping as dm

        # --- Buscar columna de hotel ---
        hotel_col = None
        for c in ["Hotel_short_name", "hotel_short_name",
                   "Hotel", "hotel", "Hotel_code"]:
            if c in self.df_raw.columns:
                hotel_col = c
                break

        if hotel_col is None:
            # Pedir al usuario
            candidates = [str(c) for c in self.df_raw.columns
                           if self.df_raw[c].dtype == object]
            if not candidates:
                self._toast("No hay columnas de texto candidatas.",
                            "warning")
                return

            dlg = tk.Toplevel(self)
            dlg.title("Columna de hotel")
            dlg.configure(bg=COLORS["bg"])
            dlg.transient(self)
            dlg.grab_set()
            center_popup(dlg, self, 420, 200)

            tk.Label(dlg, text="Selecciona la columna con códigos de hotel:",
                     bg=COLORS["bg"], fg=COLORS["text"],
                     font=FONTS["body"]).pack(pady=(16, 8), padx=16)

            var = tk.StringVar(value=candidates[0])
            ttk.Combobox(dlg, textvariable=var, values=candidates,
                          state="readonly").pack(fill="x", padx=16)

            result = {"col": None}

            def _ok():
                result["col"] = var.get()
                dlg.destroy()

            def _cancel():
                dlg.destroy()

            btn = tk.Frame(dlg, bg=COLORS["bg"])
            btn.pack(fill="x", pady=16, padx=16)
            ttk.Button(btn, text="Cancelar",
                       command=_cancel).pack(side="right", padx=(6, 0))
            ttk.Button(btn, text="✓  Aceptar",
                       style="Primary.TButton",
                       command=_ok).pack(side="right")

            self.wait_window(dlg)
            hotel_col = result["col"]
            if not hotel_col:
                return

        source = self.df_raw

        def work(cancel, progress):
            memory_budget.ensure_dataframe_operation_fits(
                source, 2, "Mapeo de hoteles")
            if cancel.is_set():
                raise TaskCancelled()
            preview = dm.preview_mapping(source[hotel_col])
            df_new = dm.add_destination_column(
                source, hotel_col=hotel_col,
                dest_col="destination_area_mapped",
                fallback="otros", inplace=False)
            n_dest = df_new["destination_area_mapped"].nunique(dropna=True)
            return preview, df_new, n_dest

        def done(value):
            preview, df_new, n_dest = value
            self._log("=" * 50)
            self._log(f"[MAPEO] Columna origen: '{hotel_col}'")
            self._log("[MAPEO] Distribución por destino:")
            for _, row in preview.iterrows():
                self._log(f"   · {row['Destino']}: {row['Count']} hoteles")
            self._log("=" * 50)
            self.df_raw = df_new
            self.df_view = df_new
            self._df_original = df_new
            self.df_table = df_new

            # Actualizar tipo de la nueva columna
            self.column_types["destination_area_mapped"] = "categorica"
            if hasattr(self, "tb_col_types"):
                self.tb_col_types["destination_area_mapped"] = "categorica"

            # Refrescos
            self._refresh_preview()
            self._refresh_type_editor()
            self._refresh_pivot_controls()
            self._refresh_kpis()

            try:
                self._tb_refresh_source()
                self._tb_refresh_lists()
            except Exception:
                pass

            self._log(f"[MAPEO] OK · {n_dest} destinos únicos")
            self._toast(f"Mapeo aplicado · {n_dest} destinos",
                        "success", 2500)
        self._start_task(work, done, "Mapeando hoteles a destinos...")


    # ==============================================================
    # EXPORTAR VISTA PREVIA (pestaña Datos)
    # ==============================================================

    # ==============================================================
    # EXPORTAR VISTA PREVIA
    # ==============================================================
    def on_export_view(self):
        """Abre el diálogo de exportación del dataset activo."""
        if self.df_raw is None:
            self._toast("Carga un dataset primero.", "warning")
            return

        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            out_dir = writable_root() / "output"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = filedialog.asksaveasfilename(
                parent=self, initialdir=str(out_dir), defaultextension=".parquet",
                filetypes=[("Parquet", "*.parquet"), ("CSV", "*.csv")])
            if not path:
                return
            fmt = Path(path).suffix.lower().lstrip(".")
            if fmt not in {"csv", "parquet"}:
                self._toast("Usa CSV o Parquet para este dataset grande.", "warning")
                return

            def work(cancel, progress):
                return active.export(Path(path), fmt, cancel, progress)

            def done(saved):
                self._log(f"[OK] Dataset activo exportado: {saved}")
                self._toast(f"Exportado: {saved.name}", "success", 3000)

            self._start_task(work, done, "Exportando dataset activo...")
            return

        # df_view puede incluir filtros temporales o de valores aplicados
        dlg = ExportDialog(self, self.df_raw, self.df_view)
        self.wait_window(dlg)
        if not self.winfo_exists():
            return

        if dlg.result is None:
            return

        path = dlg.result["path"]
        self._log(f"[OK] Exportado a {path}")
        self._toast(f"Exportado: {Path(path).name}", "success", 3000)

    def _build_body(self):
        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(fill="both", expand=True)

        # Sidebar (izquierda, ancho fijo)
        self._build_sidebar(body)

        # Separador vertical de 1px
        sep = tk.Frame(body, bg=COLORS["border"], width=1)
        sep.pack(side="left", fill="y")

        # Gap de separación
        gap = tk.Frame(body, bg=COLORS["bg"], width=6)
        gap.pack(side="left", fill="y")

        # Main (derecha, expande)
        self._build_main(body)

    def _build_header(self):
        header = tk.Frame(self, bg=COLORS["bg_card"], height=64)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        inner = tk.Frame(header, bg=COLORS["bg_card"])
        inner.pack(fill="both", expand=True, padx=20, pady=10)

        left = tk.Frame(inner, bg=COLORS["bg_card"])
        left.pack(side="left", fill="y")
        tk.Label(left, text="ANÁLISIS MEDICIÓN",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h1"]).pack(anchor="w")
        tk.Label(left, text="Panel de análisis modular",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(anchor="w")

        right = tk.Frame(inner, bg=COLORS["bg_card"])
        right.pack(side="right", fill="y")
        self.header_status = tk.Label(
            right, text="● Sin archivo cargado",
            bg=COLORS["bg_card"], fg=COLORS["text_dim"],
            font=FONTS["body"])
        self.header_status.pack(anchor="e", pady=(4, 0))

        self.progress = ttk.Progressbar(
            self, style="Thin.Horizontal.TProgressbar",
            mode="indeterminate", length=200)
        
    def _build_sidebar(self, parent):
        sidebar = tk.Frame(parent, bg=COLORS["bg_sidebar"], width=300)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # =========================================================
        # Canvas + Scrollbar vertical
        # =========================================================
        canvas = tk.Canvas(sidebar, bg=COLORS["bg_sidebar"],
                            highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(sidebar, orient="vertical",
                                command=canvas.yview)
        self.sidebar_inner = tk.Frame(canvas, bg=COLORS["bg_sidebar"])

        # Ventana interna dentro del canvas
        win_id = canvas.create_window((0, 0),
                                        window=self.sidebar_inner,
                                        anchor="nw")

        # Actualiza scrollregion al contenido
        self.sidebar_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # Fuerza el ancho del inner al ancho del canvas
        def _on_canvas_resize(event):
            canvas.itemconfig(win_id, width=event.width)
        canvas.bind("<Configure>", _on_canvas_resize)

        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # =========================================================
        # Scroll con la RUEDA del ratón — versión robusta
        # =========================================================
        self._register_wheel_zone(sidebar, canvas)

        # Guardar referencias (por si algún método las usa)
        self._sidebar_canvas = canvas
        self._sidebar_scroll = scroll
        self.sidebar_canvas = canvas

        # Guardar referencias por si hacen falta
        self._sidebar_canvas = canvas
        self._sidebar_scroll = scroll

        # Guardar también el canvas como atributo público por si algún
        # método quiere resetear la posición del scroll
        self.sidebar_canvas = canvas

        # Padding uniforme para todo el contenido
        PAD = 10

        # ---------------- 1 · Archivo ----------------
        self._section("1 · Archivo")
        self.lbl_file = tk.Label(
            self.sidebar_inner, text="(ninguno)",
            bg=COLORS["bg_sidebar"], fg=COLORS["text_muted"],
            font=FONTS["small"], wraplength=250, justify="left")
        self.lbl_file.pack(anchor="w", pady=(0, 8), padx=PAD)

        ttk.Button(self.sidebar_inner,
                   text="📂  Abrir archivo",
                   style="Primary.TButton",
                   command=self.on_open_file).pack(fill="x", padx=PAD)

        # Segunda fila: añadir al pool + unir
        row_files = tk.Frame(self.sidebar_inner, bg=COLORS["bg_sidebar"])
        row_files.pack(fill="x", padx=PAD, pady=(4, 0))
        ttk.Button(row_files, text="➕  Añadir",
                   command=self.on_add_file_to_pool).pack(
            side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(row_files, text="🔗  Unir",
                   style="Primary.TButton",
                   command=self.on_open_merge_dialog).pack(
            side="left", fill="x", expand=True, padx=(2, 0))

        # Dataset activo
        self._field_label("Dataset activo")
        self.combo_dataset = ttk.Combobox(
            self.sidebar_inner, state="readonly", font=FONTS["small"])
        self.combo_dataset.pack(fill="x", padx=PAD)
        self.combo_dataset.bind("<<ComboboxSelected>>",
                                self._on_dataset_change)

        # Quitar del pool
        ttk.Button(self.sidebar_inner, text="🗑  Quitar del pool",
                   command=self.on_remove_from_pool).pack(
            fill="x", padx=PAD, pady=(4, 0))

        # ---------------- 2 · Periodo ----------------
        self._section("2 · Periodo")

        self._field_label("Columna de fecha")
        self.combo_date = ttk.Combobox(
            self.sidebar_inner, state="readonly", font=FONTS["small"])
        self.combo_date.pack(fill="x", padx=PAD)
        self.combo_date.bind("<<ComboboxSelected>>",
                             self._on_date_col_change)

        self.lbl_range = tk.Label(
            self.sidebar_inner, text="(sin datos)",
            bg=COLORS["bg_sidebar"], fg=COLORS["text_muted"],
            font=FONTS["tiny"], wraplength=270, justify="left")
        self.lbl_range.pack(anchor="w", pady=(6, 6), padx=PAD)

        date_fields = tk.Frame(self.sidebar_inner, bg=COLORS["bg_sidebar"])
        date_fields.pack(fill="x", padx=PAD)
        date_fields.columnconfigure((0, 1), weight=1)
        for column, label in enumerate(("Desde", "Hasta")):
            tk.Label(date_fields, text=label, bg=COLORS["bg_sidebar"],
                     fg=COLORS["text_muted"], font=FONTS["tiny"]
                     ).grid(row=0, column=column, sticky="w", pady=(2, 2))
        self.entry_start = ttk.Entry(date_fields)
        self.entry_start.grid(row=1, column=0, sticky="ew", padx=(0, 3))
        self.entry_end = ttk.Entry(date_fields)
        self.entry_end.grid(row=1, column=1, sticky="ew", padx=(3, 0))

        freq_row = tk.Frame(self.sidebar_inner, bg=COLORS["bg_sidebar"])
        freq_row.pack(fill="x", padx=PAD, pady=(5, 0))
        tk.Label(freq_row, text="Granularidad", bg=COLORS["bg_sidebar"],
                 fg=COLORS["text_muted"], font=FONTS["tiny"]
                 ).pack(side="left", padx=(0, 6))
        self.combo_freq = ttk.Combobox(
            freq_row, values=list(FREQ_MAP.keys()),
            state="readonly")
        self.combo_freq.current(0)
        self.combo_freq.pack(side="left", fill="x", expand=True)

        row_btn = tk.Frame(self.sidebar_inner, bg=COLORS["bg_sidebar"])
        row_btn.pack(fill="x", padx=PAD, pady=(10, 0))
        ttk.Button(row_btn, text="Aplicar filtros",
                   style="Primary.TButton",
                   command=self.on_apply_filters).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(row_btn, text="↺ Reset",
                   command=self.on_reset_filters).pack(
            side="left", fill="x", expand=True, padx=(4, 0))

        # ---------------- 3 · Análisis ----------------
        self._section("3 · Análisis")

        self.lbl_analysis_cat = tk.Label(
            self.sidebar_inner, text="—",
            bg=COLORS["bg_sidebar"], fg=COLORS["primary"],
            font=FONTS["kpi_lbl"], anchor="w")
        self.lbl_analysis_cat.pack(anchor="w", padx=PAD, pady=(0, 2))

        self.analysis_combo = ttk.Combobox(
            self.sidebar_inner, state="readonly", font=FONTS["small"])
        self.analysis_combo.pack(fill="x", padx=PAD)
        self.analysis_combo.bind("<<ComboboxSelected>>",
                                 self._on_analysis_combo_change)

        self.lbl_analysis_desc = tk.Label(
            self.sidebar_inner, text="",
            bg=COLORS["bg_sidebar"], fg=COLORS["text_muted"],
            font=FONTS["tiny"], wraplength=270, justify="left")
        self.lbl_analysis_desc.pack(anchor="w", padx=PAD, pady=(6, 0))

        ttk.Button(self.sidebar_inner,
                   text="▶  Ejecutar análisis",
                   style="Primary.TButton",
                   command=self.on_run_analysis).pack(
            fill="x", padx=PAD, pady=(10, 0))

        # Espacio final para que el último botón no quede pegado al borde
        tk.Frame(self.sidebar_inner, bg=COLORS["bg_sidebar"],
                 height=20).pack(fill="x")
    def _section(self, title: str):
        tk.Frame(self.sidebar_inner, bg=COLORS["bg_sidebar"],
                 height=14).pack(fill="x")
        tk.Label(self.sidebar_inner, text=title,
                 bg=COLORS["bg_sidebar"], fg=COLORS["primary"],
                 font=FONTS["h3"]).pack(anchor="w", padx=8, pady=(8, 4))
    

    def _field_label(self, text: str):
        tk.Label(self.sidebar_inner, text=text,
                 bg=COLORS["bg_sidebar"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(anchor="w", padx=8, pady=(6, 2))

    # ==============================================================
    # SCROLL GLOBAL CON RUEDA DEL RATÓN
    # ==============================================================
    def _install_global_wheel_handler(self):
        """
        Instala UN único handler global de <MouseWheel> que se
        distribuye a las zonas registradas vía _register_wheel_zone().

        Ventajas:
          - No compite con unbind_all() de otros widgets.
          - Funciona sobre cualquier hijo (botones, entries, labels).
          - Soporta varias zonas scrollables simultáneas.
        """
        if getattr(self, "_wheel_handler_installed", False):
            return
        self._wheel_handler_installed = True
        self._wheel_zones: list = []

        def _on_wheel(event):
            try:
                px, py = event.x_root, event.y_root
            except AttributeError:
                return

            # La última zona registrada tiene prioridad (overlays)
            for bbox_fn, canvas in reversed(self._wheel_zones):
                try:
                    x0, y0, x1, y1 = bbox_fn()
                except Exception:
                    continue
                if x0 <= px < x1 and y0 <= py < y1:
                    delta = getattr(event, "delta", 0)
                    if delta:
                        canvas.yview_scroll(
                            int(-1 * (delta / 120)), "units")
                    elif getattr(event, "num", 0) == 4:
                        canvas.yview_scroll(-1, "units")
                    elif getattr(event, "num", 0) == 5:
                        canvas.yview_scroll(1, "units")
                    return  # consumido

        # add="+" para no pisar otros handlers globales
        self.bind_all("<MouseWheel>", _on_wheel, add="+")
        self.bind_all("<Button-4>", _on_wheel, add="+")
        self.bind_all("<Button-5>", _on_wheel, add="+")

    def _register_wheel_zone(self, container: tk.Widget,
                              canvas: tk.Canvas):
        """
        Registra (container → canvas) para que la rueda haga scroll
        vertical sobre `canvas` cuando el cursor esté sobre
        `container` o cualquiera de sus descendientes.
        """
        self._install_global_wheel_handler()

        def _bbox():
            x0 = container.winfo_rootx()
            y0 = container.winfo_rooty()
            return (x0, y0,
                    x0 + container.winfo_width(),
                    y0 + container.winfo_height())

        self._wheel_zones.append((_bbox, canvas))    

    # ---------------- Main ----------------
    def _build_main(self, parent):
        main = tk.Frame(parent, bg=COLORS["bg"])
        main.pack(side="left", fill="both", expand=True)

        self._build_kpi_cards(main)
        self._build_console(main)

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(8, 8))

        self.tab_data = tk.Frame(self.notebook, bg=COLORS["bg_card"])
        self.tab_table = tk.Frame(self.notebook, bg=COLORS["bg_card"])
        self.tab_results = tk.Frame(self.notebook, bg=COLORS["bg_card"])
        self.tab_plots = tk.Frame(self.notebook, bg=COLORS["bg_card"])

        self.notebook.add(self.tab_data, text="  📊  Datos  ")
        self.notebook.add(self.tab_table, text="  🔧  Tabla  ")
        self.notebook.add(self.tab_results, text="  📋  Resultados  ")
        self.notebook.add(self.tab_plots, text="  📈  Gráficos  ")

        self._build_data_tab()
        self._build_table_tab()
        self._build_results_tab()      # ← NUEVA
        self._build_plots_tab()

    def _build_kpi_cards(self, parent):
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(side="top", fill="x", padx=16, pady=(12, 4))

        specs = [
            ("filas", "Filas", "primary", "filas"),
            ("columnas", "Columnas", "purple", "columnas"),
            ("num_cols", "Numéricas", "success", "numericas"),
            ("cat_cols", "Categóricas", "warning", "categoricas"),
            ("analisis", "Análisis", "primary", "analisis"),
            ("graficos", "Gráficos", "success", "graficos"),
        ]

        self.kpi_cards = {}
        for i, (key, label, color_key, kind) in enumerate(specs):
            card = GlassCard(
                row, padding=(14, 10),
                accent=COLORS[color_key],
                on_click=lambda k=kind: self._show_kpi_detail(k),
            )
            card.pack(side="left", fill="both", expand=True,
                      padx=(0 if i == 0 else 4, 4))

            tk.Label(card.inner, text=label.upper(),
                     bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                     font=FONTS["kpi_lbl"]).pack(anchor="w")

            val = tk.Label(card.inner, text="—",
                           bg=COLORS["bg_card"], fg=COLORS[color_key],
                           font=FONTS["kpi_val"])
            val.pack(anchor="w")

            tk.Label(card.inner, text="ver detalle →",
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=FONTS["tiny"]).pack(anchor="w", pady=(2, 0))

            self.kpi_cards[key] = {"value": val, "card": card}

    def _build_console(self, parent):
        wrap = tk.Frame(parent, bg=COLORS["bg"])
        wrap.pack(side="bottom", fill="x", padx=16, pady=(0, 12))

        header = tk.Frame(wrap, bg=COLORS["bg"])
        header.pack(fill="x")
        tk.Label(header, text="🖥️  Consola",
                 bg=COLORS["bg"], fg=COLORS["text"],
                 font=FONTS["h3"]).pack(side="left")

        self.console_text = ScrolledText(
            wrap, height=9, font=FONTS["mono"],
            bg="#0A0E14", fg="#C9D1D9",
            insertbackground=COLORS["text"],
            selectbackground=COLORS["primary_dark"],
            relief="flat", borderwidth=0,
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            state="disabled", wrap="word",
        )
        self.console_text.pack(fill="x", pady=(4, 0))# ---------------- Pestaña Datos ----------------

    def _build_data_tab(self):
        pane = ttk.PanedWindow(self.tab_data, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=10, pady=10)

        # ============ VISTA PREVIA (izquierda) ============
        left = tk.Frame(pane, bg=COLORS["bg_card"])
        pane.add(left, weight=5)

        head = tk.Frame(left, bg=COLORS["bg_card"])
        head.pack(fill="x", pady=(0, 6))
        tk.Label(head, text="Vista previa",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")

        # --- Botón de exportar vista previa ---
        ttk.Button(head, text="💾  Exportar",
                   style="Primary.TButton",
                   command=self.on_export_view).pack(
            side="left", padx=(10, 0))

        self.lbl_shape = tk.Label(head, text="(sin datos)",
                                  bg=COLORS["bg_card"],
                                  fg=COLORS["text_muted"],
                                  font=FONTS["small"])
        self.lbl_shape.pack(side="right")

        self.lbl_preview_page = tk.Label(
            head, text="", bg=COLORS["bg_card"],
            fg=COLORS["text_muted"], font=FONTS["small"])
        self.lbl_preview_page.pack(side="right", padx=(0, 8))
        self.btn_preview_next = ttk.Button(
            head, text="Siguiente", command=lambda: self._change_preview_page(1))
        self.btn_preview_next.pack(side="right", padx=(0, 4))
        self.btn_preview_previous = ttk.Button(
            head, text="Anterior", command=lambda: self._change_preview_page(-1))
        self.btn_preview_previous.pack(side="right", padx=(0, 4))

        tree_frame = tk.Frame(left, bg=COLORS["bg_card"])
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, show="headings")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",
                            command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree.tag_configure("odd", background=COLORS["bg_card"])
        self.tree.tag_configure("even", background=COLORS["bg_card_alt"])

        # ============ PANEL DERECHO (scrollable) ============
        right_wrap = tk.Frame(pane, bg=COLORS["bg_card"])
        pane.add(right_wrap, weight=3)

        # Canvas + scrollbar para permitir overflow vertical
        r_canvas = tk.Canvas(right_wrap, bg=COLORS["bg_card"],
                             highlightthickness=0, borderwidth=0)
        r_scroll = ttk.Scrollbar(right_wrap, orient="vertical",
                                 command=r_canvas.yview)
        right = tk.Frame(r_canvas, bg=COLORS["bg_card"])

        right.bind(
            "<Configure>",
            lambda e: r_canvas.configure(scrollregion=r_canvas.bbox("all")),
        )
        win_id = r_canvas.create_window((0, 0), window=right, anchor="nw")
        r_canvas.configure(yscrollcommand=r_scroll.set)

        # Ajusta el ancho del frame interno al ancho del canvas
        def _on_canvas_resize(event):
            r_canvas.itemconfig(win_id, width=event.width)
        r_canvas.bind("<Configure>", _on_canvas_resize)

        # Scroll con la rueda — versión robusta (zona registrada)
        self._register_wheel_zone(right_wrap, r_canvas)

        r_canvas.pack(side="left", fill="both", expand=True)
        r_scroll.pack(side="right", fill="y")

        # Contenido del panel derecho
        self._build_types_card(right)
        self._build_pivot_card(right)

    def _build_types_card(self, parent):
        card = tk.Frame(parent, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        card.pack(fill="x", pady=(0, 10))

        # Header con título + contador
        head = tk.Frame(card, bg=COLORS["bg_card"])
        head.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(head, text="Tipos de columna",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")
        self.lbl_types_count = tk.Label(head, text="",
                                        bg=COLORS["bg_card"],
                                        fg=COLORS["text_dim"],
                                        font=FONTS["tiny"])
        self.lbl_types_count.pack(side="right")

        # Contenedor scrollable con altura MÁS PEQUEÑA (120 en lugar de 170)
        wrap = tk.Frame(card, bg=COLORS["bg_card"])
        wrap.pack(fill="x", padx=12)

        canvas = tk.Canvas(wrap, height=120,
                           bg=COLORS["bg_card"], highlightthickness=0)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        self.type_frame = tk.Frame(canvas, bg=COLORS["bg_card"])
        self.type_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.type_frame, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Botones a media anchura
        btn_row = tk.Frame(card, bg=COLORS["bg_card"])
        btn_row.pack(fill="x", padx=12, pady=(8, 12))
        ttk.Button(btn_row, text="Detectar",
                   command=self.on_auto_detect_types).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(btn_row, text="Aplicar", style="Primary.TButton",
                   command=self.on_apply_types).pack(
            side="left", fill="x", expand=True, padx=(4, 0))
        
    def _build_pivot_card(self, parent):
        card = tk.Frame(parent, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        card.pack(fill="x")

        tk.Label(card, text="Pivot",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(anchor="w", padx=12, pady=(10, 8))

        form = tk.Frame(card, bg=COLORS["bg_card"])
        form.pack(fill="x", padx=12, pady=(0, 12))

        # 2 columnas: etiquetas (fijas) + widgets (expandibles)
        form.columnconfigure(0, weight=0, minsize=90)
        form.columnconfigure(1, weight=1)

        # --- Fila 0 · Índice ---
        tk.Label(form, text="Índice",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"], anchor="w").grid(
            row=0, column=0, sticky="w", pady=4, padx=(0, 8))
        self.pivot_index = ttk.Combobox(form, state="readonly")
        self.pivot_index.grid(row=0, column=1, sticky="ew", pady=4)

        # --- Fila 1 · Columnas ---
        tk.Label(form, text="Columnas",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"], anchor="w").grid(
            row=1, column=0, sticky="w", pady=4, padx=(0, 8))
        self.pivot_columns = ttk.Combobox(form, state="readonly")
        self.pivot_columns.grid(row=1, column=1, sticky="ew", pady=4)

        # --- Fila 2 · Valores (listbox) ---
        tk.Label(form, text="Valores",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"], anchor="nw").grid(
            row=2, column=0, sticky="nw", pady=4, padx=(0, 8))

        vals_wrap = tk.Frame(form, bg=COLORS["bg_card"])
        vals_wrap.grid(row=2, column=1, sticky="ew", pady=4)

        self.pivot_values = tk.Listbox(
            vals_wrap, selectmode="extended", height=5,
            bg=COLORS["bg_input"], fg=COLORS["text"],
            selectbackground=COLORS["primary_dark"],
            selectforeground="#FFFFFF",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["primary"],
            borderwidth=0, exportselection=False,
            activestyle="none", font=FONTS["small"],
        )
        self.pivot_values.pack(side="left", fill="both", expand=True)

        vsb = ttk.Scrollbar(vals_wrap, orient="vertical",
                            command=self.pivot_values.yview)
        self.pivot_values.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")

        # --- Fila 3 · Hint ---
        tk.Label(form, text="Ctrl+clic para varios",
                 bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], anchor="e").grid(
            row=3, column=1, sticky="e", pady=(0, 4))

        # --- Fila 4 · Nomenclatura ---
        tk.Label(form, text="Nomenclatura",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"], anchor="w").grid(
            row=4, column=0, sticky="w", pady=4, padx=(0, 8))

        naming_row = tk.Frame(form, bg=COLORS["bg_card"])
        naming_row.grid(row=4, column=1, sticky="ew", pady=4)

        self.pivot_naming = tk.StringVar(value="metrica_soporte")
        ttk.Radiobutton(naming_row, text="Métrica - Soporte",
                        variable=self.pivot_naming,
                        value="metrica_soporte").pack(side="left")
        ttk.Radiobutton(naming_row, text="Soporte - Métrica",
                        variable=self.pivot_naming,
                        value="soporte_metrica").pack(
            side="left", padx=(10, 0))

        # --- Fila 5 · Agregación ---
        tk.Label(form, text="Agregación",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"], anchor="w").grid(
            row=5, column=0, sticky="w", pady=4, padx=(0, 8))
        self.pivot_agg = ttk.Combobox(form, values=AGG_OPTIONS,
                                      state="readonly")
        self.pivot_agg.current(0)
        self.pivot_agg.grid(row=5, column=1, sticky="ew", pady=4)

        # --- Fila 6 · Botones ---
        btn_row = tk.Frame(form, bg=COLORS["bg_card"])
        btn_row.grid(row=6, column=0, columnspan=2,
                     sticky="ew", pady=(12, 0))

        ttk.Button(btn_row, text="🔀  Pivotar tabla",
                   style="Primary.TButton",
                   command=self.on_pivot).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(btn_row, text="↩  Deshacer",
                   command=self.on_undo_pivot).pack(
            side="left", fill="x", expand=True, padx=(4, 0))
    # ---------------- Pestaña Gráficos ----------------
    
    def _on_canal_change(self, _event=None):
        canal = self.combo_canal.get()
        if canal == "Todos" or not canal:
            self.figures = list(self._figures_all)
            self._current_canal = "Todos"
        else:
            self.figures = [f for f in self._figures_all
                            if getattr(f, "_mmm_canal", None) == canal]
            self._current_canal = canal

        self.plot_idx = 0
        self._render_plots()

        n = len(self.figures)
        if canal == "Todos":
            self.lbl_canal_info.config(
                text=f"{len(self._canales)} canal(es) · "
                     f"{n} figura(s) en total")
        else:
            self.lbl_canal_info.config(
                text=f"Mostrando solo '{canal}': {n} figura(s)")

    def _apply_plot_filters(self, _event=None):
        """Aplica filtros de canal + KPI + target a las figuras."""
        canal = (self.combo_canal.get()
                 if hasattr(self, "combo_canal") else "Todos")
        kpi = (self.combo_kpi_filter.get()
               if hasattr(self, "combo_kpi_filter") else "Todos")
        target = (self.combo_target_filter.get()
                  if hasattr(self, "combo_target_filter") else "Todos")

        def _ok(f):
            if canal and canal != "Todos":
                if getattr(f, "_mmm_canal", None) != canal:
                    return False
            if kpi and kpi != "Todos":
                if getattr(f, "_mmm_kpi", None) != kpi:
                    return False
            if target and target != "Todos":
                if getattr(f, "_mmm_target", None) != target:
                    return False
            return True

        self.figures = [f for f in self._figures_all if _ok(f)]
        self.plot_idx = 0
        self._render_plots()

        try:
            self.lbl_kpi_filter_info.config(
                text=f"{len(self.figures)} figura(s)")
            self.lbl_target_filter_info.config(
                text=f"{len(self.figures)} figura(s)")
        except Exception:
            pass

    # ==============================================================
    # PESTAÑA TABLA — CONSTRUCTOR VISUAL
    # ==============================================================
    def _build_table_tab(self):
        """Constructor visual basado en tarjetas por columna."""
        # ============================================================
        # HEADER FIJO
        # ============================================================
        top = tk.Frame(self.tab_table, bg=COLORS["bg_card"])
        top.pack(fill="x", side="top")
        tk.Frame(top, bg=COLORS["primary"], height=2).pack(fill="x")

        inner = tk.Frame(top, bg=COLORS["bg_card"])
        inner.pack(fill="x", padx=16, pady=10)

        tk.Label(inner, text="🔧  Constructor de tablas",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")

        tk.Label(inner,
                 text="Asigna un rol a cada columna haciendo clic",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left", padx=(14, 0))

        # Feedback transitorio (se borra solo tras unos segundos)
        self.tb_lbl_feedback = tk.Label(
            inner, text="", bg=COLORS["bg_card"],
            fg=COLORS["primary"], font=FONTS["small"])
        self.tb_lbl_feedback.pack(side="left", padx=(16, 0))

        self.tb_lbl_status = tk.Label(
            inner, text="", bg=COLORS["bg_card"],
            fg=COLORS["text_dim"], font=FONTS["tiny"])
        self.tb_lbl_status.pack(side="right")

        # ============================================================
        # FOOTER FIJO
        # ============================================================
        bottom = tk.Frame(self.tab_table, bg=COLORS["bg_card"])
        bottom.pack(fill="x", side="bottom")
        tk.Frame(bottom, bg=COLORS["border"], height=1).pack(fill="x")

        btn_row = tk.Frame(bottom, bg=COLORS["bg_card"])
        btn_row.pack(fill="x", padx=16, pady=10)

        # Izquierda: Reset + Refrescar preview
        ttk.Button(btn_row, text="↺  Reset",
                   command=self._tb_reset).pack(side="left",
                                                  padx=(6, 0))

        ttk.Button(btn_row, text="🔄  Refrescar preview",
                   command=lambda: self._tb_update_preview(immediate=True)
                   ).pack(side="left", padx=(6, 0))

        # Derecha: Aplicar al df activo + Exportar
        ttk.Button(btn_row, text="✓  Aplicar al df activo",
                   style="Primary.TButton",
                   command=self._tb_apply).pack(side="right")

        ttk.Button(btn_row, text="💾  Exportar (df completo)",
                   command=self._tb_export).pack(side="right",
                                                    padx=(0, 6))

        # ============================================================
        # CUERPO
        # ============================================================
        body = tk.Frame(self.tab_table, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=10, pady=10)

        # ========== IZQUIERDA: tarjetas de columnas ==========
        left = tk.Frame(body, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        left.pack(side="left", fill="both", expand=True)

        # Buscador
        srow = tk.Frame(left, bg=COLORS["bg_card"])
        srow.pack(fill="x", padx=10, pady=(10, 6))
        tk.Label(srow, text="🔍", bg=COLORS["bg_card"],
                 fg=COLORS["text_dim"],
                 font=FONTS["small"]).pack(side="left")
        self.tb_search_var = tk.StringVar()
        ttk.Entry(srow, textvariable=self.tb_search_var).pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self.tb_search_var.trace_add(
            "write", lambda *a: self._tb_refresh_lists())

        # Leyenda
        tk.Label(left,
                 text="Pulsa el botón del rol · Fila = agrupar · "
                      "Columna = pivotar · Valor = métrica · "
                      "Filtro = restringir",
                 bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"], wraplength=520,
                 justify="left").pack(fill="x", padx=10, pady=(0, 6))

        # Lista scrollable de tarjetas
        list_wrap = tk.Frame(left, bg=COLORS["bg_card"])
        list_wrap.pack(fill="both", expand=True, padx=10,
                        pady=(0, 10))

        self.tb_cards_canvas = tk.Canvas(
            list_wrap, bg=COLORS["bg_card"], highlightthickness=0)
        sb = ttk.Scrollbar(list_wrap, orient="vertical",
                            command=self.tb_cards_canvas.yview)
        self.tb_cards_frame = tk.Frame(self.tb_cards_canvas,
                                        bg=COLORS["bg_card"])
        self.tb_cards_frame.bind(
            "<Configure>",
            lambda e: self.tb_cards_canvas.configure(
                scrollregion=self.tb_cards_canvas.bbox("all")))
        win = self.tb_cards_canvas.create_window(
            (0, 0), window=self.tb_cards_frame, anchor="nw")
        self.tb_cards_canvas.configure(yscrollcommand=sb.set)
        self.tb_cards_canvas.bind(
            "<Configure>",
            lambda e: self.tb_cards_canvas.itemconfig(win,
                                                        width=e.width))
        self.tb_cards_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # ========== DERECHA: preview ==========
        right = tk.Frame(body, bg=COLORS["bg_card"],
                          highlightbackground=COLORS["border"],
                          highlightthickness=1)
        right.pack(side="right", fill="both", expand=True,
                    padx=(10, 0))

        prev_head = tk.Frame(right, bg=COLORS["bg_card"])
        prev_head.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(prev_head, text="Vista previa",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h3"]).pack(side="left")
        self.tb_lbl_preview_info = tk.Label(
            prev_head, text="", bg=COLORS["bg_card"],
            fg=COLORS["text_dim"], font=FONTS["tiny"])
        self.tb_lbl_preview_info.pack(side="right")

        tree_wrap = tk.Frame(right, bg=COLORS["bg_card"])
        tree_wrap.pack(fill="both", expand=True, padx=10,
                        pady=(0, 10))

        self.tb_preview_tree = ttk.Treeview(tree_wrap,
                                              show="headings",
                                              height=20)
        pv_v = ttk.Scrollbar(tree_wrap, orient="vertical",
                              command=self.tb_preview_tree.yview)
        pv_h = ttk.Scrollbar(tree_wrap, orient="horizontal",
                              command=self.tb_preview_tree.xview)
        self.tb_preview_tree.configure(yscrollcommand=pv_v.set,
                                        xscrollcommand=pv_h.set)
        self.tb_preview_tree.grid(row=0, column=0, sticky="nsew")
        pv_v.grid(row=0, column=1, sticky="ns")
        pv_h.grid(row=1, column=0, sticky="ew")
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)

        self.tb_preview_tree.tag_configure("even",
                                            background=COLORS["bg_card"])
        self.tb_preview_tree.tag_configure("odd",
                                            background=COLORS["bg_card_alt"])
    # ---------------- Secciones del constructor ----------------
    def _tb_make_section(self, parent, title, add_callback):
        """Sección con selector + botón + chips para Filas / Columnas."""
        wrap = tk.Frame(parent, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)

        head = tk.Frame(wrap, bg=COLORS["bg_card"])
        head.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(head, text=title, bg=COLORS["bg_card"],
                 fg=COLORS["primary"], font=FONTS["kpi_lbl"]).pack(side="left")

        row = tk.Frame(wrap, bg=COLORS["bg_card"])
        row.pack(fill="x", padx=10, pady=(0, 4))

        combo = ttk.Combobox(row, state="readonly", width=22)
        combo.pack(side="left", fill="x", expand=True)
        combo["values"] = []

        def _do():
            c = combo.get()
            if c:
                add_callback(c)
        ttk.Button(row, text="+ Añadir", width=10,
                   command=_do).pack(side="left", padx=(6, 0))

        chips = tk.Frame(wrap, bg=COLORS["bg_card"])
        chips.pack(fill="x", padx=10, pady=(0, 8))

        wrap._combo = combo
        wrap._chips = chips
        return wrap

    def _tb_make_values_section(self, parent):
        wrap = tk.Frame(parent, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        head = tk.Frame(wrap, bg=COLORS["bg_card"])
        head.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(head, text="VALORES (métricas)", bg=COLORS["bg_card"],
                 fg=COLORS["primary"], font=FONTS["kpi_lbl"]).pack(side="left")
        tk.Label(head, text="Pivotar = una columna por valor de COLUMNAS",
                 bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                 font=FONTS["tiny"]).pack(side="left", padx=(6, 0))

        row = tk.Frame(wrap, bg=COLORS["bg_card"])
        row.pack(fill="x", padx=10, pady=(0, 4))

        combo = ttk.Combobox(row, state="readonly", width=16)
        combo.pack(side="left", fill="x", expand=True)

        agg = ttk.Combobox(row, values=["sum", "mean", "median", "min",
                                          "max", "count", "nunique",
                                          "std", "first", "last"],
                            state="readonly", width=8)
        agg.current(0)
        agg.pack(side="left", padx=(6, 0))

        def _column_selected(_event=None):
            if combo.get() and self._tb_default_aggregation(combo.get()) == "count":
                agg.set("count")

        combo.bind("<<ComboboxSelected>>", _column_selected)

        pivot = ttk.Combobox(row, values=["Pivotar", "No pivotar"],
                              state="readonly", width=10)
        pivot.current(0 if self.tb_pivot_enabled else 1)
        pivot.pack(side="left", padx=(6, 0))
        pivot.bind("<<ComboboxSelected>>",
                   lambda _e: self._tb_set_pivot_enabled(
                       pivot.get() == "Pivotar"))

        def _do():
            c = combo.get()
            a = agg.get() or "sum"
            if not c:
                return
            for v in self.tb_vals:
                if (v["col"] == c and v["agg"] == a
                        and v.get("pivot", self.tb_pivot_enabled)
                        == (pivot.get() == "Pivotar")):
                    return
            self.tb_vals.append({"col": c, "agg": a,
                                 "pivot": pivot.get() == "Pivotar"})
            self._tb_refresh_lists()
            self._tb_update_preview()

        ttk.Button(row, text="+", width=3,
                   command=_do).pack(side="left", padx=(6, 0))

        chips = tk.Frame(wrap, bg=COLORS["bg_card"])
        chips.pack(fill="x", padx=10, pady=(0, 8))

        wrap._combo = combo
        wrap._agg = agg
        wrap._pivot = pivot
        wrap._chips = chips
        return wrap
    def _tb_make_filters_section(self, parent):
        wrap = tk.Frame(parent, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        head = tk.Frame(wrap, bg=COLORS["bg_card"])
        head.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(head, text="FILTROS", bg=COLORS["bg_card"],
                 fg=COLORS["primary"], font=FONTS["kpi_lbl"]).pack(side="left")

        row = tk.Frame(wrap, bg=COLORS["bg_card"])
        row.pack(fill="x", padx=10, pady=(0, 4))

        combo = ttk.Combobox(row, state="readonly", width=22)
        combo.pack(side="left", fill="x", expand=True)

        def _do():
            c = combo.get()
            if c:
                self._tb_open_filter(c)
        ttk.Button(row, text="+", width=3,
                   command=_do).pack(side="left", padx=(6, 0))

        chips = tk.Frame(wrap, bg=COLORS["bg_card"])
        chips.pack(fill="x", padx=10, pady=(0, 8))

        wrap._combo = combo
        wrap._chips = chips
        return wrap

    # ==============================================================
    # REFRESCO DE LISTAS
    # ==============================================================
    def _tb_source_df(self) -> pd.DataFrame | None:
        """
        Devuelve el df sobre el que trabaja la pestaña Tabla.

        ⚠ NO hace copy() para DFs grandes: crea la vista lazy.
        Solo hace copy si el df es pequeño (<100k filas).
        """
        if self.df_table is None and self.df_raw is not None:
            if len(self.df_raw) > 100_000:
                # Referencia directa — no duplicar 15 GB
                self.df_table = self.df_raw
            else:
                self.df_table = self.df_raw.copy()
        return self.df_table

    def _cached_nunique(self, df, col: str) -> int:
        """
        Devuelve el número de valores únicos de una columna con caché.

        Evita recalcular nunique() sobre 22M de filas cada vez que la UI
        lo necesita (por ejemplo, al pintar tarjetas del constructor).

        Comportamiento:
          - df < 500k filas → nunique() exacto sobre toda la columna
          - df ≥ 500k filas → nunique() aproximado con muestra (head 200k)
          - Caché por (id_df, shape, columna) → invalidación automática
          - Si el caché supera 500 entradas, se limpia entero

        Uso:
            n = self._cached_nunique(df, "Canal")
        """
        # --- Validación de entrada ---
        if df is None or col not in df.columns:
            return 0

        # --- Preparar caché ---
        cache = getattr(self, "_nunique_cache", None)
        if cache is None:
            self._nunique_cache = {}
            cache = self._nunique_cache

        # --- Clave del caché ---
        try:
            key = (id(df), df.shape[0], df.shape[1], str(col))
        except Exception:
            # Si el df es raro, no cacheamos y calculamos directo
            try:
                return int(df[col].nunique(dropna=True))
            except Exception:
                return 0

        if key in cache:
            return cache[key]

        # --- Cálculo ---
        val = 0
        try:
            s = df[col]
            n_total = len(s)

            if n_total == 0:
                val = 0
            elif n_total > 500_000:
                # Muestra por head (instantáneo incluso con 22M filas)
                sample = s.head(200_000)
                val = int(sample.nunique(dropna=True))
            else:
                val = int(s.nunique(dropna=True))

        except Exception as e:
            print(f"[cached_nunique] Error en '{col}': {e}")
            val = 0

        # --- Guardar en caché ---
        try:
            if len(cache) > 500:
                cache.clear()
            cache[key] = val
        except Exception:
            pass

        return val
    
    def free_app_caches(self):
        """Libera las cachés internas de la app (seguro, no cierra nada)."""
        for attr in ("_kpi_cache", "_dtype_cache", "_nunique_cache",
                     "_agg_cache", "_agg_cache_key"):
            try:
                setattr(self, attr, None)
            except Exception:
                pass

        # --- Resultado anterior (DataFrames pesados) ---
        if hasattr(self, "result") and self.result is not None:
            self.result = None

        # --- Figuras antiguas de matplotlib ---
        if hasattr(self, "figures"):
            self.figures = []
        if hasattr(self, "_figures_all"):
            self._figures_all = []

        # --- Buffer de consola ---
        if hasattr(self, "console_buffer"):
            self.console_buffer = io.StringIO()

        import gc
        gc.collect()

        self._toast("Cachés liberadas", "info")
        self._log("[OK] Cachés de la app liberadas")

    def free_duckdb_cache(self):
        """Libera las tablas temporales de DuckDB."""
        try:
            conn = db_engine.get_conn()
            # Listar todas las tablas/view registradas
            df = conn.execute("SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema='main'").df()
            for name in df["table_name"]:
                try:
                    if name.startswith("_") or name.startswith("temp_"):
                        conn.execute(f'DROP TABLE IF EXISTS "{name}"')
                except Exception:
                    pass

            # Limpiar views registradas
            conn.execute("PRAGMA database_size=memory")
        except Exception as e:
            print(f"[free] DuckDB: {e}")

    def _tb_refresh_source(self):
        """
        Refresca los combos del constructor.
        Clasifica columnas usando una muestra si el DF es grande.
        """
        df = self._tb_source_df()
        if df is None:
            return

        # --- Muestra por step (instantáneo) ---
        # --- Muestra con head ---
        SAMPLE_N = 50_000
        if len(df) <= SAMPLE_N:
            df_sample = df
        else:
            df_sample = df.head(SAMPLE_N)

        # Clasificación de columnas sobre la muestra
        date_cols, num_cols, cat_cols, txt_cols = [], [], [], []
        for c in df.columns:
            s = df_sample[c]
            t = self.tb_col_types.get(str(c), "")
            if not t:
                if pd.api.types.is_datetime64_any_dtype(s):
                    t = "fecha"
                elif pd.api.types.is_bool_dtype(s):
                    t = "categorica"
                elif pd.api.types.is_numeric_dtype(s):
                    t = "numero"
                elif isinstance(s.dtype, pd.CategoricalDtype):
                    t = "categorica"
                else:
                    try:
                        n = self._cached_nunique(df, str(c))
                    except Exception:
                        n = 0
                    t = "categorica" if n <= 100 else "texto"
                self.tb_col_types[str(c)] = t

            if t == "fecha":
                date_cols.append(str(c))
            elif t == "numero":
                num_cols.append(str(c))
            elif t == "categorica":
                cat_cols.append(str(c))
            else:
                txt_cols.append(str(c))

        self._tb_date_cols = date_cols
        self._tb_num_cols = num_cols
        self._tb_cat_cols = cat_cols
        self._tb_txt_cols = txt_cols

        all_cols = [str(c) for c in df.columns]

        # Solo actualizamos los combos si existen
        for name, values in (
            ("tb_sec_rows", all_cols),
            ("tb_sec_cols", all_cols),
            ("tb_sec_vals", num_cols or all_cols),
            ("tb_sec_filters", cat_cols + txt_cols),
        ):
            section = getattr(self, name, None)
            if section is not None and hasattr(section, "_combo"):
                try:
                    section._combo["values"] = values
                except Exception:
                    pass
        if hasattr(self, "tb_sec_vals") and hasattr(self.tb_sec_vals, "_pivot"):
            self.tb_sec_vals._pivot.set(
                "Pivotar" if self.tb_pivot_enabled else "No pivotar")

    def _tb_refresh_lists(self):
        """Pinta las tarjetas de cada columna del df de trabajo."""
        if not hasattr(self, "tb_cards_frame"):
            return

        for w in self.tb_cards_frame.winfo_children():
            w.destroy()

        # Reset del diccionario de referencias (para repintar tarjetas sueltas)
        self._tb_card_widgets = {}

        df = self._tb_source_df()
        if df is None:
            tk.Label(self.tb_cards_frame,
                    text="Carga un archivo primero.",
                    bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                    font=FONTS["body"]).pack(pady=20)
            return

        # --- Muestra para calcular cardinalidades ---
        SAMPLE_N = 30_000
        if len(df) <= SAMPLE_N:
            df_sample = df
        else:
            df_sample = df.head(SAMPLE_N)

        # ✅ CACHÉ: evita recalcular nunique al repintar la lista entera
        nuniq_map = {}
        for c in df.columns:
            try:
                nuniq_map[str(c)] = self._cached_nunique(df, str(c))
            except Exception:
                nuniq_map[str(c)] = 0

        query = self.tb_search_var.get().strip().lower()
        shown = 0
        for col in df.columns:
            name = str(col)
            if query and query not in name.lower():
                continue
            self._tb_render_card(name, df[col],
                                nuniq=nuniq_map.get(name, 0))
            shown += 1

        if shown == 0:
            tk.Label(self.tb_cards_frame,
                    text="Ninguna columna coincide con la búsqueda.",
                    bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                    font=FONTS["small"]).pack(pady=20)

        self._tb_refresh_status()

    def _tb_render_card(self, name: str, series: pd.Series,
                        nuniq: int | None = None,
                        insert_before=None):
        """
        Tarjeta con nombre clicable para editar el tipo.

        Guarda referencias a los widgets clave en self._tb_card_widgets
        para que _tb_refresh_single_card pueda actualizarlos IN-PLACE
        (sin destruir/crear).
        """
        if not hasattr(self, "_tb_card_widgets"):
            self._tb_card_widgets = {}

        # ---------- Tipo ----------
        t = self.tb_col_types.get(name, "")
        if not t:
            if pd.api.types.is_datetime64_any_dtype(series):
                t = "fecha"
            elif pd.api.types.is_bool_dtype(series):
                t = "categorica"
            elif pd.api.types.is_numeric_dtype(series):
                t = "numero"
            elif isinstance(series.dtype, pd.CategoricalDtype):
                t = "categorica"
            else:
                if nuniq is None:
                    nuniq = 0
                t = "categorica" if nuniq <= 100 else "texto"
            self.tb_col_types[name] = t

        icons = {
            "fecha": "📅", "numero": "#", "categorica": "🏷",
            "texto": "📝", "booleano": "☑", "ignorar": "⊘",
        }
        icon = icons.get(t, "•")

        if nuniq is None:
            nuniq = 0
        if t == "fecha":
            tipo_txt = "fecha"
        elif t == "numero":
            tipo_txt = f"numérico · {nuniq} únicos"
        elif t == "categorica":
            tipo_txt = f"categoría · {nuniq} valores"
        elif t == "texto":
            tipo_txt = f"texto · {nuniq} valores"
        elif t == "ignorar":
            tipo_txt = "ignorada"
        else:
            tipo_txt = t

        # ---------- Rol actual ----------
        role = "ignore"
        if name in self.tb_rows:
            role = "row"
        elif name in self.tb_cols:
            role = "col"
        elif any(v["col"] == name for v in self.tb_vals):
            role = "val"
        is_filter = name in self.tb_filters

        # ---------- Tarjeta ----------
        card = tk.Frame(self.tb_cards_frame, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        if insert_before is not None:
            try:
                card.pack(fill="x", pady=3, before=insert_before)
            except Exception:
                card.pack(fill="x", pady=3)
        else:
            card.pack(fill="x", pady=3)

        # ---- Barra de acento izquierda ----
        accent_color = {
            "row": COLORS["success"],
            "col": COLORS["purple"],
            "val": COLORS["primary"],
        }.get(role, COLORS["border"])
        accent = tk.Frame(card, bg=accent_color, width=4)
        accent.pack(side="left", fill="y")

        inner = tk.Frame(card, bg=COLORS["bg_card"])
        inner.pack(side="left", fill="both", expand=True,
                    padx=10, pady=6)

        # ---- Fila 1: icono + nombre + tipo ----
        hdr = tk.Frame(inner, bg=COLORS["bg_card"])
        hdr.pack(fill="x", pady=(0, 4))

        lbl_icon = tk.Label(hdr, text=icon, bg=COLORS["bg_card"],
                            fg=COLORS["text"], font=FONTS["body"])
        lbl_icon.pack(side="left")

        lbl_name = tk.Label(hdr, text=name, bg=COLORS["bg_card"],
                            fg=COLORS["text"], font=FONTS["h3"],
                            cursor="hand2")
        lbl_name.pack(side="left", padx=(6, 8))
        lbl_name.bind("<Button-1>",
                       lambda e, n=name: self._tb_pick_type(n))

        lbl_tipo = tk.Label(hdr, text=f"({tipo_txt})",
                             bg=COLORS["bg_card"],
                             fg=COLORS["text_dim"],
                             font=FONTS["tiny"])
        lbl_tipo.pack(side="left")

        # ---- Fila 2: botones de rol ----
        btns = tk.Frame(inner, bg=COLORS["bg_card"])
        btns.pack(fill="x")

        def _mk_btn(label, role_key, active, color):
            return tk.Button(
                btns, text=label, relief="flat",
                bg=color if active else COLORS["bg_input"],
                fg="#FFFFFF" if active else COLORS["text"],
                activebackground=COLORS["bg_hover"],
                font=FONTS["small"], padx=10, pady=3, cursor="hand2",
                command=lambda r=role_key: self._tb_set_role(name, r),
            )

        btn_row = _mk_btn("📋 Fila", "row", role == "row",
                           COLORS["success"])
        btn_row.pack(side="left", padx=(0, 4))

        btn_col = _mk_btn("📊 Columna", "col", role == "col",
                           COLORS["purple"])
        btn_col.pack(side="left", padx=(0, 4))

        btn_val = _mk_btn("🔢 Valor", "val", role == "val",
                           COLORS["primary"])
        btn_val.pack(side="left", padx=(0, 4))

        filter_label = ("🔍 Filtro · " + str(len(self.tb_filters[name]))
                         if is_filter else "🔍 Filtro")
        btn_filter = tk.Button(
            btns, text=filter_label, relief="flat",
            bg=COLORS["warning"] if is_filter else COLORS["bg_input"],
            fg="#000000" if is_filter else COLORS["text"],
            activebackground=COLORS["bg_hover"],
            font=FONTS["small"], padx=10, pady=3, cursor="hand2",
            command=lambda: self._tb_open_filter(name),
        )
        btn_filter.pack(side="left", padx=(0, 4))

        btn_del = tk.Button(
            btns, text="🗑", relief="flat",
            bg=COLORS["bg_card"], fg=COLORS["danger"],
            activebackground=COLORS["bg_hover"],
            font=FONTS["small"], cursor="hand2",
            command=lambda: self._tb_set_role(name, "ignore"),
        )
        if role != "ignore" or is_filter:
            btn_del.pack(side="right")

        # ---- Panel de extras (solo si es VALOR) ----
        extras = None
        if role == "val":
            extras = self._tb_build_extras_panel(inner, name)

        # ---- Guardar referencias ----
        self._tb_card_widgets[name] = {
            "card": card,
            "accent": accent,
            "inner": inner,
            "lbl_icon": lbl_icon,
            "lbl_tipo": lbl_tipo,
            "btns": {
                "row": btn_row,
                "col": btn_col,
                "val": btn_val,
                "filter": btn_filter,
                "del": btn_del,
            },
            "extras": extras,
        }
    
    def _tb_build_extras_panel(self, parent, name):
        """
        Construye el panel de agregación + pivot que se muestra
        cuando una columna tiene rol VALOR. Devuelve el Frame raíz
        para que _tb_refresh_single_card lo pueda reutilizar.
        """
        val_data = next((v for v in self.tb_vals
                         if v["col"] == name), None)
        if val_data is None:
            return None

        extras = tk.Frame(parent, bg=COLORS["bg_card_alt"])
        extras.pack(fill="x", pady=(6, 0))

        tk.Label(extras, text="Agregación:",
                 bg=COLORS["bg_card_alt"],
                 fg=COLORS["text_muted"],
                 font=FONTS["tiny"]).pack(side="left", padx=(8, 4))

        agg_var = tk.StringVar(value=val_data["agg"])

        def _on_agg(_e=None, v=val_data, av=agg_var):
            v["agg"] = av.get()
            self._tb_update_preview()

        agg_cb = ttk.Combobox(
            extras, textvariable=agg_var, width=8,
            values=["sum", "mean", "median", "min", "max",
                    "count", "nunique", "std", "first", "last"],
            state="readonly")
        agg_cb.pack(side="left", padx=(0, 14))
        agg_cb.bind("<<ComboboxSelected>>", _on_agg)

        pivot_var = tk.BooleanVar(
            value=val_data.get("pivot", self.tb_pivot_enabled))

        def _on_pivot(pv=pivot_var, value=val_data):
            value["pivot"] = bool(pv.get())
            self._tb_render_chips_quick()
            self._tb_update_preview()

        ttk.Checkbutton(
            extras,
            text="Pivotar por COLUMNAS (desmarcar → columna total)",
            variable=pivot_var,
            command=_on_pivot,
        ).pack(side="left", padx=(0, 8))

        return extras

    def _tb_refresh_single_card(self, name: str):
        """
        Actualiza la tarjeta de una columna IN-PLACE (sin destruirla).

        Solo cambia colores, textos y visibilidad de los widgets.
        Coste: ~5 ms por tarjeta (vs 100-300 ms si se destruye/crea).

        Si la tarjeta no existe todavía (raro), la crea como fallback.
        """
        refs = self._tb_card_widgets.get(name)

        # Fallback: la tarjeta no existe → crearla desde cero
        if refs is None:
            df = self._tb_source_df()
            if df is None or name not in df.columns:
                return
            try:
                nuniq = self._cached_nunique(df, name)
            except Exception:
                nuniq = 0
            try:
                self._tb_render_card(name, df[name], nuniq=nuniq)
            except Exception as e:
                print(f"[TB] error creando tarjeta '{name}': {e}")
            return

        # ============================================================
        # 1 · Rol actual
        # ============================================================
        role = "ignore"
        if name in self.tb_rows:
            role = "row"
        elif name in self.tb_cols:
            role = "col"
        elif any(v["col"] == name for v in self.tb_vals):
            role = "val"
        is_filter = name in self.tb_filters

        # ============================================================
        # 2 · Barra de acento izquierda
        # ============================================================
        accent_color = {
            "row": COLORS["success"],
            "col": COLORS["purple"],
            "val": COLORS["primary"],
        }.get(role, COLORS["border"])
        try:
            refs["accent"].config(bg=accent_color)
        except Exception:
            pass

        # ============================================================
        # 3 · Botones de rol (bg/fg)
        # ============================================================
        btn_specs = [
            ("row", COLORS["success"]),
            ("col", COLORS["purple"]),
            ("val", COLORS["primary"]),
        ]
        for key, color in btn_specs:
            btn = refs["btns"].get(key)
            if btn is None:
                continue
            active = (role == key)
            try:
                btn.config(
                    bg=color if active else COLORS["bg_input"],
                    fg="#FFFFFF" if active else COLORS["text"],
                )
            except Exception:
                pass

        # ============================================================
        # 4 · Botón de filtro (texto + color)
        # ============================================================
        btn_filter = refs["btns"].get("filter")
        if btn_filter is not None:
            try:
                if is_filter:
                    n_act = len(self.tb_filters.get(name, []))
                    btn_filter.config(
                        text=f"🔍 Filtro · {n_act}",
                        bg=COLORS["warning"], fg="#000000")
                else:
                    btn_filter.config(
                        text="🔍 Filtro",
                        bg=COLORS["bg_input"], fg=COLORS["text"])
            except Exception:
                pass

        # ============================================================
        # 5 · Botón borrar (mostrar/ocultar)
        # ============================================================
        btn_del = refs["btns"].get("del")
        if btn_del is not None:
            try:
                show_del = (role != "ignore") or is_filter
                if show_del:
                    if not btn_del.winfo_manager():
                        btn_del.pack(side="right")
                else:
                    if btn_del.winfo_manager():
                        btn_del.pack_forget()
            except Exception:
                pass

        # ============================================================
        # 6 · Panel de extras (agregación)
        # ============================================================
        if role == "val":
            extras = refs.get("extras")
            if extras is None:
                # Crear por primera vez
                try:
                    extras = self._tb_build_extras_panel(
                        refs["inner"], name)
                    refs["extras"] = extras
                except Exception as e:
                    print(f"[TB] extras panel '{name}': {e}")
            else:
                # Asegurar visible
                try:
                    if not extras.winfo_manager():
                        extras.pack(fill="x", pady=(6, 0))
                except Exception:
                    pass
        else:
            extras = refs.get("extras")
            if extras is not None:
                try:
                    if extras.winfo_manager():
                        extras.pack_forget()
                except Exception:
                    pass

    def _tb_pick_type(self, name: str):
        """Menú desplegable para elegir el tipo de la columna."""
        # Coordenadas relativas a la ventana principal
        x = self.winfo_pointerx()
        y = self.winfo_pointery()

        menu = tk.Menu(self, tearoff=0,
                        bg=COLORS["bg_card"], fg=COLORS["text"],
                        activebackground=COLORS["primary_dark"],
                        activeforeground="#FFFFFF",
                        font=FONTS["small"], bd=1,
                        relief="solid")

        current = self.tb_col_types.get(name, "auto")

        def _set(new_type):
            self.tb_col_types[name] = new_type
            self._tb_refresh_source()
            self._tb_refresh_lists()
            self._tb_update_preview()

        options = [
            ("📅  Fecha", "fecha"),
            ("#  Numérico", "numero"),
            ("🏷  Categoría", "categorica"),
            ("📝  Texto", "texto"),
            ("⊘  Ignorar", "ignorar"),
        ]
        for label, key in options:
            prefix = "● " if current == key else "   "
            menu.add_command(label=f"{prefix}{label}",
                            command=lambda k=key: _set(k))

        menu.add_separator()
        menu.add_command(
            label="↻  Autodetectar",
            command=lambda: (_set("auto"),
                            self.tb_col_types.pop(name, None),
                            self._tb_refresh_source(),
                            self._tb_refresh_lists(),
                            self._tb_update_preview()),
        )

        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # --------------------------------------------------------------
    # Setter de rol
    # --------------------------------------------------------------
    def _tb_set_role(self, name: str, role: str):
        """
        Asigna un rol (row/col/val/ignore) a una columna.

        - Repinta SOLO la tarjeta clickeada (~5 ms).
        - Muestra feedback instantáneo en el header.
        - Programa la actualización de la preview con debouncing.
        """
        # 1. Limpiar el rol actual
        if name in self.tb_rows:
            self.tb_rows.remove(name)
        if name in self.tb_cols:
            self.tb_cols.remove(name)
        self.tb_vals = [v for v in self.tb_vals if v["col"] != name]

        # 2. Asignar nuevo rol
        label = ""
        kind = "info"
        if role == "row":
            self.tb_rows.append(name)
            label, kind = f"📋 {name} · Fila", "success"
        elif role == "col":
            self.tb_cols.append(name)
            label, kind = f"📊 {name} · Columna", "info"
        elif role == "val":
            self.tb_vals.append({"col": name,
                                 "agg": self._tb_default_aggregation(name),
                                 "pivot": self.tb_pivot_enabled})
            label, kind = f"🔢 {name} · Valor", "info"
        else:
            label, kind = f"⊘ {name} · Ignorada", "warning"

        # 3. Feedback instantáneo (barato)
        self._tb_flash(label, kind, duration=1200)

        # 4. Repintar SOLO la tarjeta clickeada
        self._tb_refresh_single_card(name)

        # 5. Refrescar chips de rol (si existen)
        try:
            self._tb_render_chips_quick()
        except Exception:
            pass
        # 6. Preview con debouncing
        self._tb_update_preview()

    def _tb_default_aggregation(self, name: str) -> str:
        """Selecciona una agregación válida para la columna elegida."""
        kind = self.tb_col_types.get(name)
        if kind == "numero":
            return "sum"
        if kind in {"texto", "categorica", "fecha", "ignorar"}:
            return "count"
        source = self._tb_source_df()
        if source is not None and name in source.columns:
            try:
                numeric = pd.api.types.is_numeric_dtype(source[name])
                return "sum" if numeric else "count"
            except (KeyError, TypeError, ValueError):
                pass
        return "count"

    def _tb_render_chips_quick(self):
        """
        Refresca los chips de rol asignados (filas/columnas/valores).

        Si no tienes frames de chips para rol, este método no hace nada
        (no lanza error).
        """
        try:
            if hasattr(self, "tb_sec_rows") and hasattr(
                    self.tb_sec_rows, "_chips"):
                items = [(r, "row") for r in self.tb_rows]
                self._tb_render_chips(self.tb_sec_rows, items)
        except Exception:
            pass

        try:
            if hasattr(self, "tb_sec_cols") and hasattr(
                    self.tb_sec_cols, "_chips"):
                items = [(c, "col") for c in self.tb_cols]
                self._tb_render_chips(self.tb_sec_cols, items)
        except Exception:
            pass

        try:
            if hasattr(self, "tb_sec_vals") and hasattr(
                    self.tb_sec_vals, "_chips"):
                items = [(v["col"], "val", v["agg"],
                          v.get("pivot", self.tb_pivot_enabled))
                        for v in self.tb_vals]
                self._tb_render_chips(self.tb_sec_vals, items)
        except Exception:
            pass

    def _tb_render_chips(self, section, items):
        chips = section._chips
        for w in chips.winfo_children():
            w.destroy()
        if not items:
            tk.Label(chips, text="(vacío)", bg=COLORS["bg_card"],
                    fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(
                side="left", padx=4)
            return
        for tup in items:
            name, kind = tup[0], tup[1]
            agg = tup[2] if len(tup) > 2 else None

            # Texto del chip
            if agg is None:
                txt = name
            else:
                is_pivot = tup[3] if len(tup) > 3 else self.tb_pivot_enabled
                txt = f"{name} [{agg}]" if is_pivot \
                    else f"{name} [{agg}] · total"

            chip = tk.Frame(chips, bg=COLORS["bg_input"],
                            highlightbackground=COLORS["border"],
                            highlightthickness=1)
            chip.pack(side="left", padx=(0, 6), pady=2)
            tk.Label(chip, text=txt, bg=COLORS["bg_input"],
                    fg=COLORS["text"], font=FONTS["small"]).pack(
                side="left", padx=(8, 4), pady=3)

            def _rm(n=name, k=kind, a=agg):
                if k == "row" and n in self.tb_rows:
                    self.tb_rows.remove(n)
                elif k == "col" and n in self.tb_cols:
                    self.tb_cols.remove(n)
                elif k == "val":
                    # Eliminamos todos los valores de esa col con esa agg
                    self.tb_vals = [v for v in self.tb_vals
                                    if not (v["col"] == n and v["agg"] == a)]
                self._tb_refresh_lists()
                self._tb_update_preview()

            tk.Button(chip, text="×", relief="flat",
                    bg=COLORS["bg_input"], fg=COLORS["danger"],
                    activebackground=COLORS["bg_hover"],
                    font=FONTS["small"], cursor="hand2",
                    command=_rm).pack(side="left", padx=(0, 4))
    def _tb_render_filter_chips(self):
        chips = self.tb_sec_filters._chips
        df = self._tb_source_df()
        for w in chips.winfo_children():
            w.destroy()
        if not self.tb_filters:
            tk.Label(chips, text="(sin filtros)", bg=COLORS["bg_card"],
                    fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(
                side="left", padx=4)
            return
        for col, allowed in self.tb_filters.items():
            try:
                total = self._cached_nunique(df, col) if df is not None else 0
            except Exception:
                total = 0
            chip = tk.Frame(chips, bg=COLORS["bg_input"],
                            highlightbackground=COLORS["border"],
                            highlightthickness=1)
            chip.pack(side="left", padx=(0, 6), pady=2)
            tk.Label(chip, text=f"{col}: {len(allowed)}/{total}",
                    bg=COLORS["bg_input"], fg=COLORS["text"],
                    font=FONTS["small"]).pack(side="left",
                                                padx=(8, 4), pady=3)

            def _rm(c=col):
                self.tb_filters.pop(c, None)
                self._tb_refresh_lists()
                self._tb_update_preview()

            tk.Button(chip, text="×", relief="flat",
                    bg=COLORS["bg_input"], fg=COLORS["danger"],
                    activebackground=COLORS["bg_hover"],
                    font=FONTS["small"], cursor="hand2",
                    command=_rm).pack(side="left", padx=(0, 4))

    def _tb_refresh_status(self):
        df = self._tb_source_df()
        if df is None:
            self.tb_lbl_status.config(text="")
            return
        txt = (f"{df.shape[0]}×{df.shape[1]}  ·  "
            f"{len(self.tb_rows)} fila(s)  ·  "
            f"{len(self.tb_cols)} col(s)  ·  "
            f"{len(self.tb_vals)} valor(es)  ·  "
            f"{len(self.tb_filters)} filtro(s)")
        self.tb_lbl_status.config(text=txt)
    
        # ==============================================================
    
    def _tb_flash(self, msg: str, kind: str = "info",
                duration: int = 1400):
        """
        Muestra un mensaje transitorio en el header del constructor.
        kind: 'info' (azul), 'success' (verde), 'warning' (ámbar).
        """
        if not hasattr(self, "tb_lbl_feedback"):
            return

        color = {
            "info": COLORS["primary"],
            "success": COLORS["success"],
            "warning": COLORS["warning"],
        }.get(kind, COLORS["primary"])

        try:
            self.tb_lbl_feedback.config(text=msg, fg=color)
        except Exception:
            return

        # Cancela el timer anterior si lo hay
        if hasattr(self, "_tb_flash_after"):
            try:
                self.after_cancel(self._tb_flash_after)
            except Exception:
                pass

        def _clear():
            try:
                self.tb_lbl_feedback.config(text="")
            except Exception:
                pass

        self._tb_flash_after = self.after(duration, _clear)

    # PREPARAR PARA REGRESIÓN (asistente rápido)
    # ==============================================================

    # ==============================================================
    # AÑADIR
    # ==============================================================
    def _tb_add_row(self, c):
        if c in self.tb_rows:
            return
        self.tb_rows.append(c)
        self._tb_refresh_lists()
        self._tb_update_preview()

    def _tb_add_col(self, c):
        if c in self.tb_cols:
            return
        self.tb_cols.append(c)
        self._tb_refresh_lists()
        self._tb_update_preview()

    def _tb_set_pivot_enabled(self, enabled: bool):
        """Choose the default for newly added metrics; keep existing choices."""
        enabled = bool(enabled)
        if self.tb_pivot_enabled == enabled:
            return
        self.tb_pivot_enabled = enabled
        section = getattr(self, "tb_sec_vals", None)
        if section is not None and hasattr(section, "_pivot"):
            section._pivot.set("Pivotar" if enabled else "No pivotar")

    # ==============================================================
    # CONSTRUCCIÓN
    # ==============================================================
    def _tb_build_df(self, preview: bool = False) -> pd.DataFrame:
        """Compatibilidad del exportador con la receta única del constructor."""
        pivot = getattr(self, "tb_pivot_enabled", False)
        recipe = TableRecipe.from_parts(
            self.tb_rows, self.tb_cols, self.tb_vals, self.tb_filters,
            pivot=pivot, col_types=self.tb_col_types)
        source = self._tb_source_df()
        if preview:
            return build_table_preview_pandas(
                source, recipe.rows, recipe.columns, self.tb_vals,
                dict(recipe.filters), recipe=recipe)
        return build_table_full_pandas(source, recipe)

    def _tb_build_snapshot(self):
        source = self._tb_source_df()
        return SimpleNamespace(
            _tb_source_df=lambda: source,
            tb_rows=list(self.tb_rows),
            tb_cols=list(self.tb_cols),
            tb_vals=[dict(value) for value in self.tb_vals],
            tb_pivot_enabled=self.tb_pivot_enabled,
            tb_filters={key: set(values) for key, values in self.tb_filters.items()},
            tb_col_types=dict(self.tb_col_types),
        )

    def _tb_recipe(self):
        return TableRecipe.from_parts(
            self.tb_rows, self.tb_cols, self.tb_vals, self.tb_filters,
            pivot=self.tb_pivot_enabled, col_types=self.tb_col_types)

    def _tb_update_preview(self, immediate: bool = False):
        """
        Programa el refresco de la preview con debouncing.

        - immediate=True → refresca ya (para llamadas explícitas).
        - immediate=False → espera 150 ms (para clics repetidos).
        """
        self._tb_preview_revision += 1
        from core.diagnostics import log_debug
        log_debug("WORKER", "table preview requested",
                  generation=self._tb_preview_revision)
        # Cancelar timer anterior
        if hasattr(self, "_tb_preview_after_id") and self._tb_preview_after_id:
            try:
                self.after_cancel(self._tb_preview_after_id)
            except Exception:
                pass

        delay = 0 if immediate else 150
        self._tb_preview_after_id = self.after(
            delay, self._tb_update_preview_now)

    def _tb_update_preview_now(self):
        """Refresca la preview realmente (con head de 20 filas)."""
        self._tb_preview_after_id = None

        if not hasattr(self, "tb_preview_tree"):
            return

        src = self._tb_source_df()
        if src is None:
            self.tb_preview_tree["columns"] = []
            try:
                self.tb_lbl_preview_info.config(
                    text="(carga un archivo para empezar)",
                    fg=COLORS["text_dim"])
            except Exception:
                pass
            return

        if self.tasks.busy:
            from core.diagnostics import log_debug
            log_debug("WORKER", "table preview pending",
                      generation=self._tb_preview_revision)
            if not self.tasks.closing:
                self._tb_preview_after_id = self.after(
                    300, self._tb_update_preview_now)
            return
        active = self._tb_builder_dataset or self.session.active_dataset
        recipe = self._tb_recipe()
        revision = self._tb_preview_revision
        started = time.perf_counter()
        self.tb_lbl_preview_info.config(
            text="Calculando preview...", fg=COLORS["text_muted"])

        def work(cancel, progress):
            if cancel.is_set():
                raise TaskCancelled()
            if active is not None and active.backend != "pandas":
                return preview_to_pandas(active, recipe)
            return build_table_preview_pandas(
                src, recipe.rows, recipe.columns,
                [{"col": col, "agg": agg, "pivot": metric_pivot}
                 for (col, agg), metric_pivot in zip(
                     recipe.values, recipe.pivots_for_values())],
                dict(recipe.filters), recipe=recipe)

        def done(result):
            current = self._tb_builder_dataset or self.session.active_dataset
            if current is not active or self._tb_preview_revision != revision:
                from core.diagnostics import log_debug
                log_debug("WORKER", "stale table preview discarded",
                          generation=revision,
                          current_generation=self._tb_preview_revision)
                return
            self._tb_render_preview_result(result, time.perf_counter() - started)

        self._start_task(work, done, "Construyendo preview...")

    def _tb_render_preview_result(self, result, dt):
        for item in self.tb_preview_tree.get_children():
            self.tb_preview_tree.delete(item)
        if result is None or len(result) == 0:
            self.tb_preview_tree["columns"] = []
            try:
                self.tb_lbl_preview_info.config(
                    text="(sin filas · ajusta filas/valores)",
                    fg=COLORS["warning"])
            except Exception:
                pass
            return

        # ==========================================================
        # PINTAR
        # ==========================================================
        cols = [str(c) for c in result.columns]
        self.tb_preview_tree["columns"] = cols
        for c in cols:
            self.tb_preview_tree.heading(c, text=c)
            width = 140 if len(c) < 22 else 200
            self.tb_preview_tree.column(c, width=width,
                                         anchor="w", stretch=False)

        n = min(20, len(result))
        for i in range(n):
            row = result.iloc[i]
            vals = []
            for c in result.columns:
                try:
                    vals.append(self._tb_fmt(row[c]))
                except Exception:
                    vals.append("")
            tag = "even" if i % 2 == 0 else "odd"
            self.tb_preview_tree.insert("", "end",
                                         values=vals, tags=(tag,))

        # Info + color según tiempo
        try:
            ms = dt * 1000
            if ms < 100:
                color = COLORS["success"]
            elif ms < 500:
                color = COLORS["text"]
            else:
                color = COLORS["warning"]
            self.tb_lbl_preview_info.config(
                text=f"Preview: {len(result)} filas × "
                     f"{len(result.columns)} cols  ·  {ms:.0f} ms"
                     + (" · aproximado" if result.attrs.get("approximate")
                        else ""),
                fg=color)
        except Exception:
            pass

    @staticmethod
    def _tb_fmt(v):
        if pd.isna(v):
            return ""
        if isinstance(v, pd.Timestamp):
            return v.strftime("%Y-%m-%d")
        if isinstance(v, (int, np.integer)):
            return f"{v:,}".replace(",", ".")
        if isinstance(v, (float, np.floating)):
            if abs(v) >= 1000:
                return f"{v:,.2f}".replace(",", "X").replace(".", ",")\
                                        .replace("X", ".")
            return f"{v:.4g}"
        return str(v)

    # ==============================================================
    # FILTRO
    # ==============================================================
    def _tb_open_filter(self, col: str):
        df = self._tb_source_df()
        if df is None or col not in df.columns:
            return

        dlg = tk.Toplevel(self)
        dlg.title(f"Filtro · {col}")
        dlg.configure(bg=COLORS["bg"])
        dlg.transient(self)
        dlg.grab_set()
        center_popup(dlg, self, 420, 600)

        tk.Label(dlg, text=f"Valores de '{col}'", bg=COLORS["bg"],
                fg=COLORS["text"], font=FONTS["h3"]).pack(
            anchor="w", padx=16, pady=(12, 4))
        tk.Label(dlg, text="Marca los que quieras conservar.",
                bg=COLORS["bg"], fg=COLORS["text_dim"],
                font=FONTS["small"]).pack(anchor="w", padx=16, pady=(0, 8))

        # --- Buscador ---
        search_var = tk.StringVar()
        srow = tk.Frame(dlg, bg=COLORS["bg"])
        srow.pack(fill="x", padx=16, pady=(0, 6))
        tk.Label(srow, text="Buscar:", bg=COLORS["bg"],
                fg=COLORS["text_muted"],
                font=FONTS["small"]).pack(side="left")
        ttk.Entry(srow, textvariable=search_var).pack(
            side="left", fill="x", expand=True, padx=(6, 0))

        filter_loaded = [False]
        loading = tk.Label(
            dlg, text="Cargando.", bg=COLORS["bg"],
            fg=COLORS["text_muted"], font=FONTS["small"])
        loading.pack(anchor="w", padx=16, pady=(6, 10))

        def animate_loading(step=0):
            if filter_loaded[0] or not dlg.winfo_exists():
                return
            loading.config(text="Cargando" + "." * (step % 3 + 1))
            dlg.after(350, lambda: animate_loading(step + 1))

        animate_loading()

        def finish_filter_dialog(uniques):
            filter_loaded[0] = True
            if not dlg.winfo_exists():
                return
            loading.destroy()
            if uniques is None:
                dlg.destroy()
                self._toast("El filtro tiene más de 500 valores. "
                            "Reduce la cardinalidad antes de abrirlo.",
                            "warning", 7000)
                return
            dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
            allowed_now = self.tb_filters.get(col, set(uniques))

            # Mantener la selección aparte del Listbox para que buscar no
            # elimine los valores seleccionados que queden fuera de pantalla.
            selected = set(allowed_now).intersection(uniques)

            # --- Botones marcar/desmarcar ---
            quick = tk.Frame(dlg, bg=COLORS["bg"])
            quick.pack(fill="x", padx=16, pady=(0, 6))

            def _mark_all():
                selected.update(uniques)
                _render(remember_existing=False)

            def _unmark_all():
                selected.clear()
                _render(remember_existing=False)

            ttk.Button(quick, text="Marcar todo",
                    command=_mark_all).pack(side="left")
            ttk.Button(quick, text="Desmarcar",
                    command=_unmark_all).pack(side="left", padx=(6, 0))

            count_label = tk.Label(
                quick, text="", bg=COLORS["bg"], fg=COLORS["text_dim"],
                font=FONTS["tiny"])
            count_label.pack(side="right")

            def _update_count():
                count_label.config(text=f"{len(selected)}/{len(uniques)} activos")

            # --- Lista nativa de selección múltiple ---
            wrap = tk.Frame(dlg, bg=COLORS["bg_input"],
                            highlightbackground=COLORS["border"],
                            highlightthickness=1)
            wrap.pack(fill="both", expand=True, padx=16, pady=(0, 6))

            value_list = tk.Listbox(
                wrap, selectmode="multiple", exportselection=False,
                bg=COLORS["bg_input"], fg=COLORS["text"],
                selectbackground=COLORS["primary"],
                selectforeground=COLORS["text"],
                highlightthickness=0, borderwidth=0,
                activestyle="none")
            scrollbar = ttk.Scrollbar(wrap, orient="vertical",
                                      command=value_list.yview)
            value_list.configure(yscrollcommand=scrollbar.set)
            value_list.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            rendering = [False]

            def _remember_visible_selection(_event=None):
                if rendering[0]:
                    return
                visible = value_list.get(0, "end")
                visible_selected = {visible[i] for i in value_list.curselection()}
                selected.difference_update(visible)
                selected.update(visible_selected)
                _update_count()

            def _render(*, remember_existing=True):
                if remember_existing and value_list.size():
                    _remember_visible_selection()
                q = search_var.get().strip().casefold()
                visible = [v for v in uniques if not q or q in v.casefold()]
                rendering[0] = True
                try:
                    value_list.delete(0, "end")
                    for index, value in enumerate(visible):
                        value_list.insert("end", value)
                        if value in selected:
                            value_list.selection_set(index)
                finally:
                    rendering[0] = False
                _update_count()

            value_list.bind("<<ListboxSelect>>", _remember_visible_selection)
            search_var.trace_add("write", lambda *a: _render())
            _render(remember_existing=False)

            # --- Botones finales ---
            def _ok():
                _remember_visible_selection()
                if len(selected) == len(uniques):
                    self.tb_filters.pop(col, None)
                else:
                    self.tb_filters[col] = set(selected)
                dlg.destroy()
                self._tb_refresh_lists()
                self._tb_update_preview()
                active = self.session.active_dataset
                if active is not None and active.backend != "pandas":
                    self._preview_page = 0
                    self._refresh_preview()

            def _cancel():
                dlg.destroy()

            def _reset_filter():
                self.tb_filters.pop(col, None)
                dlg.destroy()
                self._tb_refresh_lists()
                self._tb_update_preview()
                if self.session.active_dataset is not None:
                    self._preview_page = 0
                    self._refresh_preview()

            btn = tk.Frame(dlg, bg=COLORS["bg"])
            btn.pack(fill="x", side="bottom", pady=10, padx=16)
            ttk.Button(btn, text="Restablecer",
                    command=_reset_filter).pack(side="left")
            ttk.Button(btn, text="Cancelar",
                    command=_cancel).pack(side="right", padx=(6, 0))
            ttk.Button(btn, text="✓  Aplicar cambios",
                    style="Primary.TButton",
                    command=_ok).pack(side="right")

        active_for_filter = self._tb_builder_dataset or self.session.active_dataset
        filter_source = None
        cache_key = None
        if active_for_filter is not None and active_for_filter.backend != "pandas":
            other_filters = {key: set(values)
                             for key, values in active_for_filter.filters
                             if key != col}
            filter_source = active_for_filter.with_filters(other_filters)
            cache_key = (filter_source.version_token(), col)
            cached = self._tb_filter_values_cache.get(cache_key)
            if cached is not None:
                self._log(f"[CACHÉ] Valores del filtro {col}")
                finish_filter_dialog(cached)
                return

        def work(cancel, progress):
            if cancel.is_set():
                raise TaskCancelled()
            if filter_source is not None:
                values = filter_source.distinct_values(col, cancel=cancel)
            else:
                values = limited_unique_strings(df[col], cancel=cancel)
            if cancel.is_set():
                raise TaskCancelled()
            return values

        def loaded(uniques):
            if cache_key is not None and uniques is not None:
                cache = self._tb_filter_values_cache
                cache[cache_key] = tuple(uniques)
                if len(cache) > 24:
                    cache.pop(next(iter(cache)))
            finish_filter_dialog(uniques)

        started = [False]

        def close_dialog():
            if started[0]:
                self.tasks.cancel()
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", close_dialog)

        def start_filter():
            if not dlg.winfo_exists() or self.tasks.closing:
                return
            if self.tasks.busy:
                dlg.after(100, start_filter)
                return
            started[0] = self._start_task(
                work, loaded, f"Leyendo valores de {col}...")
            if not started[0]:
                dlg.destroy()

        start_filter()
    # ==============================================================
    # OPERACIÓN · UNIR COLUMNAS
    # ==============================================================
    def _tb_open_union(self):
        df = self._tb_source_df()
        if df is None:
            self._toast("Carga datos primero.", "warning")
            return

        dlg = tk.Toplevel(self)
        dlg.title("Unir columnas")
        dlg.configure(bg=COLORS["bg"])
        dlg.transient(self)
        dlg.grab_set()
        center_popup(dlg, self, 460, 330)

        tk.Label(dlg, text="Unir columnas",
                bg=COLORS["bg"], fg=COLORS["text"],
                font=FONTS["h2"]).pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(dlg,
                text="Concatena dos columnas en una sola con un separador.\n"
                    "Útil para crear claves como 'Fecha · Canal'.",
                bg=COLORS["bg"], fg=COLORS["text_dim"],
                font=FONTS["small"], justify="left").pack(
            anchor="w", padx=16, pady=(0, 10))

        form = tk.Frame(dlg, bg=COLORS["bg"])
        form.pack(fill="x", padx=16)

        cols = [str(c) for c in df.columns]

        tk.Label(form, text="Columna A:", bg=COLORS["bg"],
                fg=COLORS["text_muted"], font=FONTS["small"]).grid(
            row=0, column=0, sticky="w", pady=4)
        a_var = tk.StringVar(value=cols[0])
        ttk.Combobox(form, textvariable=a_var, values=cols,
                    state="readonly", width=28).grid(
            row=0, column=1, sticky="ew", pady=4, padx=(6, 0))

        tk.Label(form, text="Columna B:", bg=COLORS["bg"],
                fg=COLORS["text_muted"], font=FONTS["small"]).grid(
            row=1, column=0, sticky="w", pady=4)
        b_var = tk.StringVar(value=cols[1] if len(cols) > 1 else cols[0])
        ttk.Combobox(form, textvariable=b_var, values=cols,
                    state="readonly", width=28).grid(
            row=1, column=1, sticky="ew", pady=4, padx=(6, 0))

        tk.Label(form, text="Separador:", bg=COLORS["bg"],
                fg=COLORS["text_muted"], font=FONTS["small"]).grid(
            row=2, column=0, sticky="w", pady=4)
        sep_var = tk.StringVar(value=" · ")
        ttk.Entry(form, textvariable=sep_var).grid(
            row=2, column=1, sticky="ew", pady=4, padx=(6, 0))

        tk.Label(form, text="Nombre de la nueva columna:", bg=COLORS["bg"],
                fg=COLORS["text_muted"], font=FONTS["small"]).grid(
            row=3, column=0, sticky="w", pady=4)
        name_var = tk.StringVar(value="Nueva columna")
        ttk.Entry(form, textvariable=name_var).grid(
            row=3, column=1, sticky="ew", pady=4, padx=(6, 0))

        drop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Eliminar las columnas originales",
                        variable=drop_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        form.columnconfigure(1, weight=1)

        def _ok():
            a, b = a_var.get(), b_var.get()
            if not a or not b:
                return
            name = name_var.get().strip() or "Nueva columna"
            sep = sep_var.get()
            try:
                self.df_table[name] = (
                    df[a].astype(str) + sep + df[b].astype(str)
                )
                if drop_var.get():
                    self.df_table = self.df_table.drop(
                        columns=[a, b], errors="ignore")
                dlg.destroy()
                self._tb_refresh_source()
                self._tb_refresh_lists()
                self._tb_update_preview()
                self._toast(f"Columna '{name}' creada", "success")
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=dlg)

        btn = tk.Frame(dlg, bg=COLORS["bg"])
        btn.pack(fill="x", side="bottom", pady=10, padx=16)
        ttk.Button(btn, text="Cancelar",
                command=dlg.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btn, text="✓  Unir", style="Primary.TButton",
                command=_ok).pack(side="right")

    # ==============================================================
    # OPERACIÓN · QUITAR COLUMNAS
    # ==============================================================
    def _tb_open_drop(self):
        df = self._tb_source_df()
        if df is None:
            return

        dlg = tk.Toplevel(self)
        dlg.title("Quitar columnas")
        dlg.configure(bg=COLORS["bg"])
        dlg.transient(self)
        dlg.grab_set()
        center_popup(dlg, self, 440, 480)

        tk.Label(dlg, text="Quitar columnas",
                bg=COLORS["bg"], fg=COLORS["text"],
                font=FONTS["h2"]).pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(dlg, text="Marca las columnas a eliminar del df de trabajo.",
                bg=COLORS["bg"], fg=COLORS["text_dim"],
                font=FONTS["small"]).pack(anchor="w", padx=16, pady=(0, 8))

        wrap = tk.Frame(dlg, bg=COLORS["bg_input"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=16, pady=(0, 6))

        c = tk.Canvas(wrap, bg=COLORS["bg_input"], highlightthickness=0)
        sc = ttk.Scrollbar(wrap, orient="vertical", command=c.yview)
        inner = tk.Frame(c, bg=COLORS["bg_input"])
        inner.bind("<Configure>",
                lambda e: c.configure(scrollregion=c.bbox("all")))
        c.create_window((0, 0), window=inner, anchor="nw")
        c.configure(yscrollcommand=sc.set)
        c.pack(side="left", fill="both", expand=True)
        sc.pack(side="right", fill="y")

        vars_map = {}
        for col in df.columns:
            bv = tk.BooleanVar(value=False)
            ttk.Checkbutton(inner, text=str(col), variable=bv).pack(
                anchor="w", padx=8, pady=1)
            vars_map[str(col)] = bv

        def _ok():
            to_drop = [c for c, bv in vars_map.items() if bv.get()]
            if not to_drop:
                dlg.destroy()
                return
            # No permitir eliminar TODAS las columnas
            if len(to_drop) >= len(df.columns):
                messagebox.showwarning(
                    "Aviso", "No puedes eliminar todas las columnas.", parent=dlg)
                return
            self.df_table = self.df_table.drop(columns=to_drop,
                                                errors="ignore")
            dlg.destroy()
            self._tb_refresh_source()
            self._tb_refresh_lists()
            self._tb_update_preview()
            self._toast(f"{len(to_drop)} columnas eliminadas", "success")

        btn = tk.Frame(dlg, bg=COLORS["bg"])
        btn.pack(fill="x", side="bottom", pady=10, padx=16)
        ttk.Button(btn, text="Cancelar",
                command=dlg.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btn, text="✓  Quitar seleccionadas",
                style="Primary.TButton",
                command=_ok).pack(side="right")

    # ==============================================================
    # OPERACIÓN · AÑADIR DESDE ORIGINAL
    # ==============================================================
    def _tb_open_add_from_original(self):
        if self.df_raw is None or self.df_table is None:
            self._toast("Carga datos primero.", "warning")
            return

        df_orig = self.df_raw
        df_curr = self.df_table
        cand = [str(c) for c in df_orig.columns if c not in df_curr.columns]
        if not cand:
            messagebox.showinfo("Sin columnas",
                                "El df original no tiene columnas nuevas.", parent=self)
            return

        # Claves candidatas: columnas comunes
        keys = [str(c) for c in df_curr.columns if c in df_orig.columns]
        if not keys:
            messagebox.showwarning(
                "Sin claves",
                "No hay columnas comunes para unir. "
                "Necesitas al menos una columna en común (p.ej. Fecha).", parent=self)
            return

        dlg = tk.Toplevel(self)
        dlg.title("Añadir columna desde original")
        dlg.configure(bg=COLORS["bg"])
        dlg.transient(self)
        dlg.grab_set()
        center_popup(dlg, self, 460, 360)

        tk.Label(dlg, text="Añadir columna desde df original",
                bg=COLORS["bg"], fg=COLORS["text"],
                font=FONTS["h2"]).pack(anchor="w", padx=16, pady=(12, 4))
        tk.Label(dlg,
                text="Trae una columna del dataset cargado original "
                    "unida por una clave común.",
                bg=COLORS["bg"], fg=COLORS["text_dim"],
                font=FONTS["small"], justify="left").pack(
            anchor="w", padx=16, pady=(0, 10))

        form = tk.Frame(dlg, bg=COLORS["bg"])
        form.pack(fill="x", padx=16)

        tk.Label(form, text="Columna a traer:", bg=COLORS["bg"],
                fg=COLORS["text_muted"], font=FONTS["small"]).grid(
            row=0, column=0, sticky="w", pady=6)
        col_var = tk.StringVar(value=cand[0])
        ttk.Combobox(form, textvariable=col_var, values=cand,
                    state="readonly").grid(
            row=0, column=1, sticky="ew", pady=6, padx=(6, 0))

        tk.Label(form, text="Clave de unión:", bg=COLORS["bg"],
                fg=COLORS["text_muted"], font=FONTS["small"]).grid(
            row=1, column=0, sticky="w", pady=6)
        key_var = tk.StringVar(value="Fecha" if "Fecha" in keys else keys[0])
        ttk.Combobox(form, textvariable=key_var, values=keys,
                    state="readonly").grid(
            row=1, column=1, sticky="ew", pady=6, padx=(6, 0))

        tk.Label(form, text="Agregación:", bg=COLORS["bg"],
                fg=COLORS["text_muted"], font=FONTS["small"]).grid(
            row=2, column=0, sticky="w", pady=6)
        agg_var = tk.StringVar(value="sum")
        ttk.Combobox(form, textvariable=agg_var,
                    values=["sum", "mean", "median", "min", "max",
                            "first", "last", "count"],
                    state="readonly").grid(
            row=2, column=1, sticky="ew", pady=6, padx=(6, 0))

        form.columnconfigure(1, weight=1)

        def _ok():
            col = col_var.get()
            key = key_var.get()
            agg = agg_var.get() or "sum"
            if not col or not key:
                return
            try:
                # Comprobar que la clave existe en ambos
                if key not in df_curr.columns or key not in df_orig.columns:
                    raise ValueError(
                        f"La clave '{key}' no existe en ambos DataFrames.")

                # Agrupar el original por clave para obtener un único valor
                right = (df_orig[[key, col]]
                        .groupby(key, dropna=False, observed=True)[col]
                        .agg(agg)
                        .reset_index())

                merged = pd.merge(df_curr, right, on=key, how="left")
                self.df_table = merged
                dlg.destroy()
                self._tb_refresh_source()
                self._tb_refresh_lists()
                self._tb_update_preview()
                self._toast(f"Columna '{col}' añadida desde el original",
                            "success")
            except Exception as e:
                messagebox.showerror("Error al añadir", str(e), parent=dlg)

        btn = tk.Frame(dlg, bg=COLORS["bg"])
        btn.pack(fill="x", side="bottom", pady=10, padx=16)
        ttk.Button(btn, text="Cancelar",
                command=dlg.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btn, text="✓  Añadir", style="Primary.TButton",
                command=_ok).pack(side="right")

    # ==============================================================
    # RESET / APLICAR / EXPORTAR
    # ==============================================================
    def _tb_reset(self):
        name = self.active_dataset_name
        if not name or name not in self.loaded_datasets:
            return
        if self.tasks.busy:
            self._toast("Espera a que termine la tarea activa.", "warning")
            return
        self.result = None
        self._clear_plots()
        self._activate_dataset(name, already_sorted=True)
        self._toast("Dataset original restaurado", "info")

    def _tb_apply(self):
        """Construye la tabla completa fuera del hilo de Tkinter."""
        if self.tasks.busy:
            if getattr(self, "_tb_apply_pending", False):
                return
            self._tb_apply_pending = True
            revision_waiting = self._tb_preview_revision
            dataset_waiting = self.active_dataset_name
            self._tb_flash("⏳ esperando tarea activa...", "info",
                           duration=3000)
            self._log("[TAREA] Aplicar tabla: esperando a la vista previa")

            def apply_when_free():
                if self.tasks.closing or revision_waiting != self._tb_preview_revision \
                        or dataset_waiting != self.active_dataset_name:
                    self._tb_apply_pending = False
                    return
                if self.tasks.busy:
                    self.after(100, apply_when_free)
                    return
                self._tb_apply_pending = False
                self._tb_apply()

            self.after(100, apply_when_free)
            return
        active = self.session.active_dataset
        source_active = self._tb_builder_dataset or active
        recipe = self._tb_recipe()
        revision = self._tb_preview_revision
        if source_active is not None and source_active.backend != "pandas":
            value_filters = {key: set(values) if values is not None else None
                             for key, values in self.value_filters.items()}
            date_col = self.combo_date.get().strip()
            started_at = time.perf_counter()

            def work(cancel, progress):
                if cancel.is_set():
                    raise TaskCancelled()
                return build_table_active_snapshot(
                    source_active, recipe, value_filters, date_col,
                    cancel=cancel, progress=progress)

            def done(snapshot):
                if (self._tb_preview_revision != revision
                        or self.value_filters != value_filters):
                    self._toast("La configuración cambió; vuelve a aplicar.",
                                "warning")
                    return
                result, stats, preview = snapshot
                self._tb_builder_dataset = source_active
                self.session.active_dataset = result
                self.session.active_stats = stats
                self.session.base_view = None
                self._pandas_filter_key = None
                self._preview_page = 0
                selected_date = date_col if date_col in result.columns else None
                self._lazy_preview_key = (result.version_token(),
                                          selected_date, 0)
                self.df_view = preview
                self._view_is_built = True
                self._kpi_cache_key = None
                self._refresh_preview()
                if selected_date and stats.get("date_range"):
                    dmin, dmax = stats["date_range"]
                    if dmin is not None and dmax is not None:
                        self.lbl_range.config(
                            text=f"{dmin} → {dmax} (dataset completo)",
                            fg=COLORS["text"])
                elapsed = time.perf_counter() - started_at
                self._tb_flash("✓ tabla aplicada", "success", duration=3000)
                self._log(f"[OK] Tabla aplicada: {stats['rows']:,} filas "
                          f"en {elapsed:.1f}s".replace(",", "."))
                self._toast("Tabla aplicada al dataset activo", "success")

            self._start_task(work, done, "Aplicando tabla al dataset activo...")
            return
        import time as _t
        t0 = _t.time()
        snapshot = self._tb_build_snapshot()
        src = snapshot._tb_source_df()
        if src is None:
            self._toast("Carga un archivo primero.", "warning")
            return
        n_src = len(src)
        if n_src >= db_engine.UMBRAL_PANDAS:
            if not messagebox.askyesno(
                    "Aplicar al df completo",
                    f"Se aplicará al df COMPLETO "
                    f"({n_src:,} filas).\n\n"
                    f"Usará DuckDB para acelerar el proceso. "
                    f"Puede tardar unos segundos.\n\n¿Continuar?"
                    .replace(",", "."), parent=self):
                return
        def work(cancel, progress):
            if cancel.is_set():
                raise TaskCancelled()
            result = build_table_full_pandas(src, recipe)
            if result is None or len(result) == 0:
                raise ValueError("La tabla resultante está vacía.")
            return result

        def done(result):
            self.df_view = result
            self.session.active_dataset = ActiveDataset.from_frame(result)
            self.session.active_stats = None
            self.session.base_view = result
            self._pandas_filter_key = None
            self._view_is_built = True
            self._refresh_preview()
            self._refresh_kpis()
            dt = _t.time() - t0
            self._tb_flash(
                f"✓ aplicado · {len(result):,} filas en {dt:.1f}s"
                .replace(",", "."),
                "success", duration=3500)
            n_res = len(result)
            self._log(f"[OK] Tabla aplicada: "
                    f"{n_res:,} × {result.shape[1]} en {dt:.1f}s"
                    .replace(",", "."))
            self._toast(f"Tabla aplicada · {n_res:,} filas "
                        f"en {dt:.1f}s".replace(",", "."),
                        "success", 2500)
        if self._start_task(work, done, "Aplicando tabla al df completo..."):
            self._tb_flash("⏳ aplicando al df completo…", "info", duration=3000)

    def _tb_export(self):
        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            self._toast("Exporta el dataset activo desde la pestaña Datos.",
                        "info", 4000)
            return
        snapshot = self._tb_build_snapshot()
        src = snapshot._tb_source_df()
        if src is None:
            self._toast("Carga un archivo primero.", "warning")
            return
        out_dir = writable_root() / "output"
        out_dir.mkdir(exist_ok=True)
        path = filedialog.asksaveasfilename(
            parent=self, initialdir=str(out_dir),
            initialfile="tabla_construida.xlsx",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")],
        )
        if not path:
            return
        def work(cancel, progress):
            memory_budget.ensure_dataframe_operation_fits(
                src, 2, "Exportación de tabla")
            if cancel.is_set():
                raise TaskCancelled()
            result = MMMApp._tb_build_df(snapshot, preview=False)
            if result is None:
                raise ValueError("No se pudo construir la tabla")
            fmt = "csv" if path.lower().endswith(".csv") else "xlsx"
            export_dataframe(result, path, fmt, cancel=cancel,
                             progress=lambda item: progress(item[1]))

        def done(_):
            self._toast(f"Tabla exportada a {Path(path).name}",
                        "success")
        self._start_task(work, done, "Exportando tabla...")

    # ==============================================================
    # PESTAÑA RESULTADOS — TABLAS DEL ÚLTIMO ANÁLISIS
    # ==============================================================
    def _build_results_tab(self):
        """Muestra todos los DataFrames del último análisis en sub-pestañas."""
        # Header
        head = tk.Frame(self.tab_results, bg=COLORS["bg_card"])
        head.pack(fill="x", padx=16, pady=(10, 4))

        tk.Label(head, text="Resultados del último análisis",
                bg=COLORS["bg_card"], fg=COLORS["text"],
                font=FONTS["h3"]).pack(side="left")

        self.lbl_results_analysis = tk.Label(
            head, text="(ejecuta un análisis)",
            bg=COLORS["bg_card"], fg=COLORS["text_dim"],
            font=FONTS["small"])
        self.lbl_results_analysis.pack(side="right")

        # Sub-notebook para cada DataFrame
        self.results_nb = ttk.Notebook(self.tab_results)
        self.results_nb.pack(fill="both", expand=True,
                            padx=10, pady=(0, 10))

    def _refresh_results(self):
        if not hasattr(self, "results_nb"):
            return

        for tab_id in self.results_nb.tabs():
            try:
                self.results_nb.forget(tab_id)
            except Exception:
                pass

        name = (self.chosen_analysis["name"]
                if self.chosen_analysis else "—")
        self.lbl_results_analysis.config(text=name)

        dfs = _collect_result_dataframes(self.result)

        if not dfs:
            empty = tk.Frame(self.results_nb, bg=COLORS["bg_card"])
            self.results_nb.add(empty, text="  (sin datos)  ")
            tk.Label(empty,
                    text="Ejecuta un análisis para ver sus tablas aquí.",
                    bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                    font=FONTS["body"]).pack(expand=True, pady=40)
            return

        for sheet, df in dfs.items():
            tab = tk.Frame(self.results_nb, bg=COLORS["bg_card"])
            self.results_nb.add(tab, text=f"  {sheet[:30]}  ")
            self._render_df_in_tab(tab, df)

        try:
            self.results_nb.select(0)
        except Exception:
            pass

    def _render_df_in_tab(self, parent, df: pd.DataFrame):
        head = tk.Frame(parent, bg=COLORS["bg_card"])
        head.pack(fill="x", padx=8, pady=(8, 4))

        tk.Label(head,
                text=f"{df.shape[0]} filas × {df.shape[1]} columnas",
                bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                font=FONTS["small"]).pack(side="left")

        def _export():
            out_dir = writable_root() / "output"
            out_dir.mkdir(exist_ok=True)
            path = filedialog.asksaveasfilename(
                parent=self, initialdir=str(out_dir),
                initialfile="tabla_resultado.xlsx",
                defaultextension=".xlsx",
                filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")],
            )
            if not path:
                return
            def work(cancel, progress):
                fmt = "csv" if path.lower().endswith(".csv") else "xlsx"
                export_dataframe(df, path, fmt, cancel=cancel,
                                 progress=lambda item: progress(item[1]))

            def done(_):
                self._toast(f"Exportado a {Path(path).name}", "success")
            self._start_task(work, done, "Exportando tabla de resultados...")

        ttk.Button(head, text="💾 Exportar",
                command=_export).pack(side="right")

        wrap = tk.Frame(parent, bg=COLORS["bg_card"])
        wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        cols = [str(c) for c in df.columns]
        tree = ttk.Treeview(wrap, columns=cols, show="headings",
                            height=min(30, max(5, len(df))))
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=130, anchor="w", stretch=False)

        n = min(1000, len(df))
        for i in range(n):
            row = df.iloc[i]
            vals = [self._format_result_cell(row[c]) for c in df.columns]
            tag = "even" if i % 2 == 0 else "odd"
            tree.insert("", "end", values=vals, tags=(tag,))

        tree.tag_configure("odd", background=COLORS["bg_card"])
        tree.tag_configure("even", background=COLORS["bg_card_alt"])

    

    @staticmethod
    def _format_result_cell(v) -> str:
        if pd.isna(v):
            return ""
        if isinstance(v, pd.Timestamp):
            return v.strftime("%Y-%m-%d")
        if isinstance(v, (int, np.integer)):
            return f"{v:,}".replace(",", ".")
        if isinstance(v, (float, np.floating)):
            if abs(v) >= 1000:
                return f"{v:,.2f}".replace(",", "X") \
                                    .replace(".", ",") \
                                    .replace("X", ".")
            return f"{v:.4g}"
        return str(v)



    def _build_plots_tab(self):
        wrap = tk.Frame(self.tab_plots, bg=COLORS["bg_card"])
        wrap.pack(fill="both", expand=True, padx=10, pady=10)

        # ============================================================
        # 1 · Barra de navegación (◀ · contador · ▶)
        # ============================================================
        nav = tk.Frame(wrap, bg=COLORS["bg_card"])
        nav.pack(fill="x", pady=(0, 6))

        ttk.Button(nav, text="◀", style="Nav.TButton", width=4,
                   command=self.on_prev_plot).pack(side="left")

        self.lbl_plot_counter = tk.Label(
            nav, text="—", bg=COLORS["bg_card"], fg=COLORS["text"],
            font=FONTS["h3"])
        self.lbl_plot_counter.pack(side="left", expand=True)

        ttk.Button(nav, text="▶", style="Nav.TButton", width=4,
                   command=self.on_next_plot).pack(side="right")

        # ✅ Botones de guardar
        ttk.Button(nav, text="💾 Guardar gráfico",
                   command=self.on_save_plot).pack(
            side="right", padx=(0, 8))

        ttk.Button(nav, text="💾 Guardar todos",
                   command=self.on_save_all_plots).pack(
            side="right", padx=(0, 4))

        # ============================================================
        # 2 · Barra de filtro por CANAL (oculta hasta tener datos)
        # ============================================================
        self.canal_bar = tk.Frame(wrap, bg=COLORS["bg_card"])

        tk.Label(self.canal_bar, text="Canal:",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left", padx=(0, 6))

        self.combo_canal = ttk.Combobox(
            self.canal_bar, state="readonly",
            font=FONTS["small"], width=28)
        self.combo_canal.pack(side="left")
        self.combo_canal.bind("<<ComboboxSelected>>",
                              self._on_canal_change)

        self.lbl_canal_info = tk.Label(
            self.canal_bar, text="", bg=COLORS["bg_card"],
            fg=COLORS["text_dim"], font=FONTS["tiny"])
        self.lbl_canal_info.pack(side="left", padx=(10, 0))

        # ============================================================
        # 3 · Barra de filtro por KPI (oculta hasta tener datos)
        # ============================================================
        self.kpi_filter_bar = tk.Frame(wrap, bg=COLORS["bg_card"])

        tk.Label(self.kpi_filter_bar, text="KPI:",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left", padx=(0, 6))

        self.combo_kpi_filter = ttk.Combobox(
            self.kpi_filter_bar, state="readonly",
            font=FONTS["small"], width=28)
        self.combo_kpi_filter.pack(side="left")
        self.combo_kpi_filter.bind("<<ComboboxSelected>>",
                                    self._apply_plot_filters)

        self.lbl_kpi_filter_info = tk.Label(
            self.kpi_filter_bar, text="", bg=COLORS["bg_card"],
            fg=COLORS["text_dim"], font=FONTS["tiny"])
        self.lbl_kpi_filter_info.pack(side="left", padx=(10, 0))

        # ============================================================
        # 4 · Barra de filtro por TARGET (oculta hasta tener datos)
        # ============================================================
        self.target_filter_bar = tk.Frame(wrap, bg=COLORS["bg_card"])

        tk.Label(self.target_filter_bar, text="Target:",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left", padx=(0, 6))

        self.combo_target_filter = ttk.Combobox(
            self.target_filter_bar, state="readonly",
            font=FONTS["small"], width=44)
        self.combo_target_filter.pack(side="left")
        self.combo_target_filter.bind("<<ComboboxSelected>>",
                                        self._apply_plot_filters)

        self.lbl_target_filter_info = tk.Label(
            self.target_filter_bar, text="", bg=COLORS["bg_card"],
            fg=COLORS["text_dim"], font=FONTS["tiny"])
        self.lbl_target_filter_info.pack(side="left", padx=(10, 0))

        # ============================================================
        # 5 · Área del gráfico (SIEMPRE al final para expandirse)
        # ============================================================
        self.canvas_frame = tk.Frame(wrap, bg=COLORS["bg_card"])
        self.canvas_frame.pack(fill="both", expand=True)

        self.canvas = None
        self.toolbar = None

        # ============================================================
        # 6 · Ocultar barras opcionales inicialmente
        #     (se muestran al detectar canales / KPIs / targets)
        # ============================================================
        # No las empaquetamos nunca aquí, así no hay que hacer
        # pack_forget() después. Se mostrarán con .pack(...) desde
        # on_run_analysis usando before=self.canvas_frame.
    # ==============================================================
    # TOASTS Y PROGRESO
    # ==============================================================
    def _toast(self, message: str, kind: str = "info", duration: int = 3000):
        Toast(self, message, kind=kind, duration=duration)

    def _progress_start(self, message: str = "Procesando..."):
        try:
            if not hasattr(self, "progress_bar_wrap"):
                host = self
                if message.startswith("Análisis:"):
                    dialog = tk.Toplevel(self)
                    dialog.title(message)
                    dialog.configure(bg=COLORS["bg"])
                    dialog.transient(self)
                    dialog.resizable(False, False)
                    center_popup(dialog, self, 520, 130)
                    dialog.protocol("WM_DELETE_WINDOW", self.tasks.cancel)
                    dialog.lift(self)
                    self._analysis_progress_dialog = dialog
                    host = dialog
                self.progress_bar_wrap = tk.Frame(
                    host, bg=COLORS["bg_card"],
                    highlightbackground=COLORS["border_hi"],
                    highlightthickness=1)
                self.progress_bar_wrap.pack(fill="x", padx=8, pady=8)

                heading = tk.Frame(self.progress_bar_wrap,
                                   bg=COLORS["bg_card"])
                heading.pack(fill="x", padx=12, pady=(5, 0))
                self.progress_title = tk.Label(
                    heading, text=message, bg=COLORS["bg_card"],
                    fg=COLORS["text"], font=FONTS["small"], anchor="w")
                self.progress_title.pack(side="left", fill="x", expand=True)
                self.progress_percent = tk.Label(
                    heading, text="", bg=COLORS["bg_card"],
                    fg=COLORS["primary"], font=FONTS["small"])
                self.progress_percent.pack(side="right", padx=(8, 0))

                self.progress_label = tk.Label(
                    self.progress_bar_wrap,
                    text="Preparando...",
                    bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                    font=FONTS["small"], anchor="w",
                )
                self.progress_label.pack(fill="x", padx=12, pady=(2, 4))

                ttk.Button(self.progress_bar_wrap, text="Cancelar tarea",
                           command=self.tasks.cancel).pack(side="right", padx=12)

                self.progress = ttk.Progressbar(
                    self.progress_bar_wrap,
                    style="Thin.Horizontal.TProgressbar",
                    mode="indeterminate",
                )
                self.progress.pack(fill="x", padx=12, pady=(0, 8))

            self.progress_title.config(text=message)
            self.progress_label.config(text="Preparando...")
            self.progress_percent.config(text="")
            self.progress.configure(mode="indeterminate", value=0)
            self.progress.start(12)
            self.update_idletasks()
        except Exception:
            pass

    def _progress_message(self, message):
        """Acepta texto o (porcentaje, paso); siempre se llama desde after()."""
        try:
            if hasattr(self, "progress_label"):
                if isinstance(message, tuple) and len(message) == 2:
                    percent, detail = message
                    percent = max(0, min(100, int(percent)))
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=100,
                                            value=percent)
                    self.progress_percent.config(text=f"{percent}%")
                    self.progress_label.config(text=str(detail))
                else:
                    self.progress_label.config(text=str(message))
                self.update_idletasks()
        except Exception:
            pass

    def _progress_stop(self):
        try:
            self.progress.stop()
            if hasattr(self, "progress_bar_wrap"):
                self.progress_bar_wrap.pack_forget()
                self.progress_bar_wrap.destroy()
                del self.progress_bar_wrap
            dialog = getattr(self, "_analysis_progress_dialog", None)
            if dialog is not None:
                dialog.destroy()
                del self._analysis_progress_dialog
        except Exception:
            pass

    # ============================================
    # HEADER / KPI
    # ==============================================================
    def _refresh_header(self):
        if self.active_dataset_name:
            self.header_status.config(
                text=f"● {self.active_dataset_name}",
                fg=COLORS["success"])
        else:
            self.header_status.config(
                text="● Sin archivo cargado",
                fg=COLORS["text_dim"])

    def _refresh_kpis(self):
        """
        KPIs con caché por identidad de DataFrame.

        - No recalcula si es el mismo df.
        - Para df grandes, usa los valores ya cacheados.
        """
        df = self.df_view if self.df_view is not None else self.df_raw
        if df is None:
            for entry in self.kpi_cards.values():
                entry["value"].config(text="—")
            self._kpi_cache = None
            return

        def fmt(n):
            return f"{n:,}".replace(",", ".")

        # --- Cache por id(df) + shape ---
        cache_key = (id(df), df.shape[0], df.shape[1])
        if getattr(self, "_kpi_cache_key", None) == cache_key:
            # Refrescar solo desde caché (rápido)
            c = self._kpi_cache
            self.kpi_cards["filas"]["value"].config(text=c["filas"])
            self.kpi_cards["columnas"]["value"].config(text=c["columnas"])
            self.kpi_cards["num_cols"]["value"].config(text=c["num_cols"])
            self.kpi_cards["cat_cols"]["value"].config(text=c["cat_cols"])
            self.kpi_cards["analisis"]["value"].config(
                text=str(len(self.analyses)))
            self.kpi_cards["graficos"]["value"].config(
                text=str(len(self.figures)))
            return

        # --- Cálculo (primera vez o df cambió) ---
        active = self.session.active_dataset
        lazy_stats = (self.session.active_stats if active is not None
                      and active.backend != "pandas" else None)
        n_rows = lazy_stats["rows"] if lazy_stats else len(df)
        n_cols = lazy_stats["columns"] if lazy_stats else df.shape[1]

        # Detección de numéricas: solo mira dtypes (instantáneo)
        try:
            num_cols = sum(
                1 for c in df.columns
                if pd.api.types.is_numeric_dtype(df[c])
            )
        except Exception:
            num_cols = df.select_dtypes("number").shape[1]

        # Detección de categóricas: usa column_types si está, si no
        # cuenta las no-numéricas
        if self.column_types:
            cat_cols = sum(1 for c in df.columns
                            if self.column_types.get(c) == "categorica")
        else:
            try:
                cat_cols = n_cols - num_cols
            except Exception:
                cat_cols = 0

        n_txt = fmt(n_rows)
        if n_rows >= db_engine.UMBRAL_PANDAS:
            n_txt = f"{n_txt}  ⚡"

        self.kpi_cards["filas"]["value"].config(text=n_txt)
        self.kpi_cards["columnas"]["value"].config(text=fmt(n_cols))
        self.kpi_cards["num_cols"]["value"].config(text=fmt(num_cols))
        self.kpi_cards["cat_cols"]["value"].config(text=fmt(cat_cols))
        self.kpi_cards["analisis"]["value"].config(
            text=str(len(self.analyses)))
        self.kpi_cards["graficos"]["value"].config(
            text=str(len(self.figures)))

        # Guardar en caché
        self._kpi_cache = {
            "filas": n_txt,
            "columnas": fmt(n_cols),
            "num_cols": fmt(num_cols),
            "cat_cols": fmt(cat_cols),
        }
        self._kpi_cache_key = cache_key

        # Aviso visual si es grande
        n_txt = fmt(n_rows)
        if n_rows >= db_engine.UMBRAL_PANDAS:
            n_txt = f"{n_txt}  ⚡"

        self.kpi_cards["filas"]["value"].config(text=n_txt)
        self.kpi_cards["columnas"]["value"].config(text=fmt(n_cols))
        self.kpi_cards["num_cols"]["value"].config(text=fmt(num_cols))
        self.kpi_cards["cat_cols"]["value"].config(text=fmt(cat_cols))
        self.kpi_cards["analisis"]["value"].config(text=str(len(self.analyses)))
        self.kpi_cards["graficos"]["value"].config(text=str(len(self.figures)))
    # ==============================================================
    # POPUP DE DETALLE
    # ==============================================================
    def _show_kpi_detail(self, kind: str):
        df = self.df_view if self.df_view is not None else self.df_raw
        if df is None:
            self._toast("Carga un archivo primero.", "warning")
            return

        title_map = {
            "filas": "Detalle · Filas",
            "columnas": "Detalle · Columnas",
            "numericas": "Detalle · Variables numéricas",
            "categoricas": "Detalle · Variables categóricas",
            "analisis": "Análisis disponibles",
            "graficos": "Gráficos generados",
        }
        title = title_map.get(kind, "Detalle")

        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=COLORS["bg"])
        win.minsize(540, 460)
        win.transient(self)
        win.grab_set()
        center_popup(win, self, 620, 580)

        header = tk.Frame(win, bg=COLORS["bg_card"])
        header.pack(fill="x")
        tk.Frame(header, bg=COLORS["primary"], height=2).pack(fill="x")
        tk.Label(header, text=title,
                bg=COLORS["bg_card"], fg=COLORS["text"],
                font=FONTS["h2"]).pack(anchor="w", padx=16, pady=12)

        # Cuerpo scrollable
        body_wrap = tk.Frame(win, bg=COLORS["bg"])
        body_wrap.pack(fill="both", expand=True, padx=16, pady=12)

        canvas = tk.Canvas(body_wrap, bg=COLORS["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(body_wrap, orient="vertical",
                            command=canvas.yview)
        inner = tk.Frame(canvas, bg=COLORS["bg"])
        inner.bind("<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Contenido
        active = self.session.active_dataset
        if (active is not None
                and (active.backend != "pandas" or len(df) > 20_000)
                and kind in {"filas", "numericas", "categoricas"}):
            self._kv(inner, "Estado", "Calculando sobre el dataset activo completo...")
            date_col = detect_date_column(df) if kind == "filas" else None

            def work(cancel, progress):
                if kind == "filas":
                    return active.profile_rows(date_col, cancel)
                return active.profile_columns(
                    "numeric" if kind == "numericas" else "categorical", cancel)

            def done(details):
                if not win.winfo_exists() or self.session.active_dataset is not active:
                    return
                for child in inner.winfo_children():
                    child.destroy()
                if kind == "filas":
                    self._kv(inner, "Filas activas", f"{details['rows']:,}")
                    self._kv(inner, "Filas con algún nulo",
                             f"{details['rows_with_null']:,}")
                    if date_col and details.get("date_range"):
                        first, last = details["date_range"]
                        self._kv(inner, "Periodo completo", f"{first} → {last}")
                else:
                    for item in details:
                        self._section_title(inner, item["column"])
                        self._kv(inner, "Nulos", f"{item['nulls']:,}")
                        if kind == "numericas":
                            for key, label in (("min", "Mínimo"),
                                               ("max", "Máximo"),
                                               ("avg", "Media")):
                                self._kv(inner, label, str(item[key]))
                        else:
                            self._kv(inner, "Valores distintos (aprox.)",
                                     f"{item['distinct_approx']:,}")

            self._start_task(work, done, "Calculando estadísticas completas...")
        elif kind == "filas":
            self._detail_filas(inner, df)
        elif kind == "columnas":
            self._detail_columnas(inner, df, win)
        elif kind == "numericas":
            sample = df.head(20_000)
            if len(sample) < len(df):
                self._kv(inner, "Resumen", "Muestra de las primeras 20.000 filas")
            self._detail_numericas(inner, sample)
        elif kind == "categoricas":
            sample = df.head(20_000)
            if len(sample) < len(df):
                self._kv(inner, "Resumen", "Muestra de las primeras 20.000 filas")
            self._detail_categoricas(inner, sample)
        elif kind == "analisis":
            self._detail_analisis(inner)
        elif kind == "graficos":
            self._detail_graficos(inner)

        # Footer
        footer = tk.Frame(win, bg=COLORS["bg_card"])
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=COLORS["border"], height=1).pack(fill="x")
        ttk.Button(footer, text="Cerrar",
                command=win.destroy).pack(anchor="e", padx=16, pady=10)

    # ---------- Filas ----------
    def _detail_filas(self, parent, df):
        self._kv(parent, "Total de filas", str(df.shape[0]))
        self._kv(parent, "Filas activas (con filtros)",
                str(self.df_view.shape[0]) if self.df_view is not None
                else "—")
        if len(df) > 20_000:
            df = df.head(20_000)
            self._kv(parent, "Estadísticas", "Muestra de las primeras 20.000 filas")
        self._kv(parent, "Filas duplicadas",
                str(int(df.duplicated().sum())))
        self._kv(parent, "Filas con algún nulo",
                str(int(df.isnull().any(axis=1).sum())))
        date_col = detect_date_column(df)
        if date_col:
            dmin, dmax = df[date_col].min(), df[date_col].max()
            self._kv(parent, "Rango temporal",
                    f"{dmin.date()} → {dmax.date()}")
            self._kv(parent, "Días cubiertos",
                    str((dmax - dmin).days + 1))

    # ---------- Columnas ----------
    def _detail_columnas(self, parent, df, win):
        active_stats = self.session.active_stats
        is_lazy = (self.session.active_dataset is not None
                   and self.session.active_dataset.backend != "pandas")
        total_rows = active_stats["rows"] if is_lazy else df.shape[0]
        total_cols = active_stats["columns"] if is_lazy else df.shape[1]
        self._kv(parent, "Total de columnas", str(total_cols))
        self._kv(parent, "Dimensiones",
                f"{total_rows} filas × {total_cols} columnas")
        self._spacer(parent)

        # ---- Visibilidad ----
        self._section_title(parent, "Visibilidad de columnas")
        tk.Label(parent,
                text="Desmarca las que no quieras ver en la vista previa.",
                bg=COLORS["bg"], fg=COLORS["text_dim"],
                font=FONTS["tiny"]).pack(anchor="w", pady=(0, 6))

        vis_frame = tk.Frame(parent, bg=COLORS["bg"])
        vis_frame.pack(fill="x")

        self._vis_vars = {}
        for c in df.columns:
            col_s = str(c)
            is_visible = (not self.visible_columns) or (col_s in self.visible_columns)
            var = tk.BooleanVar(value=is_visible)
            tipo = self.column_types.get(c, self.column_types.get(col_s, "auto"))
            ttk.Checkbutton(
                vis_frame,
                text=f"{col_s}    ·    {tipo}",
                variable=var,
            ).pack(anchor="w", pady=1, padx=2)
            self._vis_vars[col_s] = var

        # ---- Filtro de valores ----
        self._spacer(parent, 14)
        self._section_title(parent, "Filtro de valores")

        def _es_filtrable(c):
            t = self.column_types.get(c)
            if t in ("categorica", "texto"):
                return True
            if df[c].dtype == object:
                return True
            try:
                n = df[c].head(20_000).nunique(dropna=True)
                return n <= 50 and t != "numero"
            except Exception:
                return False

        filtrables = [str(c) for c in df.columns if _es_filtrable(c)]

        if not filtrables:
            tk.Label(parent, text="No hay columnas categóricas para filtrar.",
                    bg=COLORS["bg"], fg=COLORS["text_dim"],
                    font=FONTS["small"]).pack(anchor="w")
        else:
            # Selector de columna
            sel_row = tk.Frame(parent, bg=COLORS["bg"])
            sel_row.pack(fill="x", pady=(4, 6))
            tk.Label(sel_row, text="Columna:",
                    bg=COLORS["bg"], fg=COLORS["text_muted"],
                    font=FONTS["small"]).pack(side="left")

            self._filter_col_var = tk.StringVar(value=filtrables[0])
            combo_filter = ttk.Combobox(
                sel_row, textvariable=self._filter_col_var,
                values=filtrables, state="readonly", width=22,
            )
            combo_filter.pack(side="left", padx=(6, 0))

            # Buscador
            search_var = tk.StringVar()
            search_row = tk.Frame(parent, bg=COLORS["bg"])
            search_row.pack(fill="x", pady=(0, 4))
            tk.Label(search_row, text="Buscar:",
                    bg=COLORS["bg"], fg=COLORS["text_muted"],
                    font=FONTS["small"]).pack(side="left")
            search_entry = ttk.Entry(search_row, textvariable=search_var)
            search_entry.pack(side="left", fill="x", expand=True,
                            padx=(6, 0))

            # Contenedor de checkboxes
            checks_wrap = tk.Frame(parent, bg=COLORS["bg"],
                                highlightbackground=COLORS["border"],
                                highlightthickness=1)
            checks_wrap.pack(fill="x")

            checks_inner = tk.Frame(checks_wrap, bg=COLORS["bg"])
            checks_inner.pack(fill="x", padx=6, pady=6)

            self._filter_values_vars = {}
            _local_state: dict[str, dict] = {}
            _unique_cache: dict[str, list[str] | None] = {}
            _last_col = {"name": None}
            _rebuild_after = {"id": None}

            def _snapshot_state():
                col_prev = _last_col["name"]
                if col_prev is None:
                    return
                _local_state[col_prev] = {
                    v: var.get()
                    for v, var in self._filter_values_vars.items()
                }

            def _render_checks(col, uniques):
                if not checks_inner.winfo_exists():
                    return
                for w in checks_inner.winfo_children():
                    w.destroy()
                self._filter_values_vars.clear()
                if uniques is None:
                    tk.Label(checks_inner,
                             text="Más de 400 valores. Reduce la cardinalidad.",
                             bg=COLORS["bg"], fg=COLORS["warning"]).pack(anchor="w")
                    return

                q = search_var.get().strip().lower()
                if q:
                    uniques = [v for v in uniques if q in v.lower()]

                if len(uniques) > 400:
                    tk.Label(checks_inner,
                            text=f"{len(uniques)} valores. Refina con el buscador.",
                            bg=COLORS["bg"], fg=COLORS["warning"],
                            font=FONTS["tiny"]).pack(anchor="w")

                prev = _local_state.get(col, None)
                allowed = self.value_filters.get(col, None)

                for v in uniques[:400]:
                    if prev is not None:
                        is_checked = prev.get(v, True)
                    elif allowed is None:
                        is_checked = True
                    else:
                        is_checked = v in allowed
                    var = tk.BooleanVar(value=is_checked)
                    ttk.Checkbutton(checks_inner, text=v,
                                    variable=var).pack(anchor="w")
                    self._filter_values_vars[v] = var

            def _rebuild_checks():
                col = self._filter_col_var.get()
                if not col or col not in df.columns:
                    return
                if col != _last_col["name"]:
                    _snapshot_state()
                    _last_col["name"] = col
                if col in _unique_cache:
                    _render_checks(col, _unique_cache[col])
                    return
                if self.tasks.busy:
                    checks_inner.after(200, _rebuild_checks)
                    return

                def work(cancel, progress):
                    return limited_unique_strings(df[col], limit=400,
                                                  cancel=cancel)

                def done(values):
                    _unique_cache[col] = values
                    if (checks_inner.winfo_exists()
                            and self._filter_col_var.get() == col):
                        _render_checks(col, values)

                if self._start_task(work, done, f"Leyendo valores de {col}..."):
                    tk.Label(checks_inner, text="Leyendo valores...",
                             bg=COLORS["bg"], fg=COLORS["text_dim"]).pack(anchor="w")

            def _rebuild_throttled(*_a):
                if _rebuild_after["id"]:
                    try:
                        checks_inner.after_cancel(_rebuild_after["id"])
                    except Exception:
                        pass
                _rebuild_after["id"] = checks_inner.after(120, _rebuild_checks)

            combo_filter.bind("<<ComboboxSelected>>", _rebuild_throttled)
            search_var.trace_add("write", lambda *a: _rebuild_throttled())
            _rebuild_checks()

            # Botones rápidos
            quick_row = tk.Frame(parent, bg=COLORS["bg"])
            quick_row.pack(fill="x", pady=(4, 0))

            def _sel_all():
                for var in self._filter_values_vars.values():
                    var.set(True)

            def _sel_none():
                for var in self._filter_values_vars.values():
                    var.set(False)

            ttk.Button(quick_row, text="Marcar todo",
                    command=_sel_all).pack(side="left")
            ttk.Button(quick_row, text="Desmarcar",
                    command=_sel_none).pack(side="left", padx=(6, 0))

        # ---- Botones aplicar / reset ----
        self._spacer(parent, 12)
        btn_row = tk.Frame(parent, bg=COLORS["bg"])
        btn_row.pack(fill="x")

        def _apply():
            # Visibilidad
            vis = [c for c, v in self._vis_vars.items() if v.get()]
            self.visible_columns = vis if len(vis) < len(df.columns) else []

            # Filtros de valores
            if filtrables:
                col = self._filter_col_var.get()
                if col and col in _unique_cache and _unique_cache[col] is not None:
                    selected = {v for v, var in self._filter_values_vars.items()
                                if var.get()}
                    all_vals = set(_unique_cache[col])
                    if selected >= all_vals:
                        self.value_filters.pop(col, None)
                    else:
                        self.value_filters[col] = selected

            self._refresh_preview()
            self._toast("Filtros aplicados", "success", 1500)
            try:
                win.destroy()
            except Exception:
                pass

        def _reset():
            self.visible_columns = []
            self.value_filters = {}
            for var in self._vis_vars.values():
                var.set(True)
            if filtrables:
                for var in self._filter_values_vars.values():
                    var.set(True)
            self._refresh_preview()
            self._toast("Filtros restablecidos", "info", 1500)

        ttk.Button(btn_row, text="✓  Aplicar",
                style="Primary.TButton",
                command=_apply).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(btn_row, text="↺  Restablecer",
                command=_reset).pack(
            side="left", fill="x", expand=True, padx=(4, 0))

    # ---------- Numéricas ----------
    def _detail_numericas(self, parent, df):
        num = df.select_dtypes("number")
        if num.empty:
            self._kv(parent, "Resultado", "No hay columnas numéricas")
            return
        self._kv(parent, "Columnas numéricas", str(num.shape[1]))
        self._spacer(parent)
        self._section_title(parent, "Resumen por columna")

        for c in num.columns:
            card = tk.Frame(parent, bg=COLORS["bg_card"],
                            highlightbackground=COLORS["border"],
                            highlightthickness=1)
            card.pack(fill="x", pady=3)
            tk.Label(card, text=str(c),
                    bg=COLORS["bg_card"], fg=COLORS["text"],
                    font=FONTS["h3"]).pack(anchor="w", padx=10, pady=(8, 2))
            info = tk.Frame(card, bg=COLORS["bg_card"])
            info.pack(fill="x", padx=10, pady=(0, 8))
            stats = {
                "nulos": f"{int(num[c].isnull().sum())} "
                        f"({num[c].isnull().mean()*100:.1f}%)",
                "min": f"{num[c].min():.4g}",
                "max": f"{num[c].max():.4g}",
                "media": f"{num[c].mean():.4g}",
                "std": f"{num[c].std():.4g}",
            }
            for i, (k, v) in enumerate(stats.items()):
                tk.Label(info, text=f"{k}:",
                        bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                        font=FONTS["tiny"]).grid(row=0, column=i*2,
                                                sticky="w", padx=(0, 2))
                tk.Label(info, text=v,
                        bg=COLORS["bg_card"], fg=COLORS["text"],
                        font=FONTS["tiny"]).grid(row=0, column=i*2+1,
                                                sticky="w", padx=(0, 12))

    # ---------- Categóricas ----------
    def _detail_categoricas(self, parent, df):
        cats = []
        for c in df.columns:
            if self.column_types.get(c) == "categorica":
                cats.append(c)
            elif df[c].dtype == object and df[c].nunique(dropna=True) <= 50:
                cats.append(c)
        if not cats:
            self._kv(parent, "Resultado", "No hay columnas categóricas")
            return
        self._kv(parent, "Columnas categóricas", str(len(cats)))
        self._spacer(parent)
        self._section_title(parent, "Cardinalidad y valores top")
        for c in cats:
            card = tk.Frame(parent, bg=COLORS["bg_card"],
                            highlightbackground=COLORS["border"],
                            highlightthickness=1)
            card.pack(fill="x", pady=3)
            tk.Label(card, text=str(c),
                    bg=COLORS["bg_card"], fg=COLORS["text"],
                    font=FONTS["h3"]).pack(anchor="w", padx=10, pady=(8, 2))
            nunique = df[c].nunique(dropna=True)
            tk.Label(card, text=f"{nunique} valores únicos",
                    bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                    font=FONTS["tiny"]).pack(anchor="w", padx=10)
            for val, cnt in df[c].value_counts().head(5).items():
                row = tk.Frame(card, bg=COLORS["bg_card"])
                row.pack(fill="x", padx=10)
                tk.Label(row, text=f"· {val}",
                        bg=COLORS["bg_card"], fg=COLORS["text"],
                        font=FONTS["tiny"], anchor="w").pack(side="left")
                tk.Label(row, text=f"({cnt})",
                        bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                        font=FONTS["tiny"]).pack(side="right")
            tk.Frame(card, bg=COLORS["bg_card"], height=6).pack()

    # ---------- Análisis ----------
    def _detail_analisis(self, parent):
        if not self.analyses:
            self._kv(parent, "Resultado", "No hay análisis en ./analyses/")
            return
        self._kv(parent, "Total detectados", str(len(self.analyses)))
        self._spacer(parent)
        by_cat = {}
        for a in self.analyses:
            by_cat.setdefault(a["category"], []).append(a)
        for cat, items in by_cat.items():
            self._section_title(parent, f"{cat} ({len(items)})")
            for a in items:
                card = tk.Frame(parent, bg=COLORS["bg_card"],
                                highlightbackground=COLORS["border"],
                                highlightthickness=1)
                card.pack(fill="x", pady=2)
                tk.Label(card, text=a["name"],
                        bg=COLORS["bg_card"], fg=COLORS["text"],
                        font=FONTS["h3"]).pack(anchor="w", padx=10,
                                                pady=(6, 0))
                tk.Label(card, text=a["description"],
                        bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                        font=FONTS["tiny"], wraplength=480,
                        justify="left").pack(anchor="w", padx=10,
                                            pady=(0, 6))

    # ---------- Gráficos ----------
    def _detail_graficos(self, parent):
        self._kv(parent, "Gráficos generados", str(len(self.figures)))
        self._kv(parent, "Análisis actual",
                self.chosen_analysis["name"] if self.chosen_analysis else "—")
        self._spacer(parent)
        if not self.figures:
            self._kv(parent, "Info",
                    "Ejecuta un análisis de la categoría 'Gráficos'.")
        else:
            self._section_title(parent, "Índice de gráficos")
            for i in range(len(self.figures)):
                self._kv(parent, f"Gráfico {i+1}",
                        "✓ actual" if i == self.plot_idx else "—")

    # ---------- Helpers ----------
    def _kv(self, parent, key, value):
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(fill="x", pady=2)
        tk.Label(row, text=key, bg=COLORS["bg"], fg=COLORS["text_muted"],
                font=FONTS["small"], width=26,
                anchor="w").pack(side="left")
        tk.Label(row, text=value, bg=COLORS["bg"], fg=COLORS["text"],
                font=FONTS["body"], anchor="w",
                justify="left", wraplength=380).pack(side="left")

    def _section_title(self, parent, text):
        tk.Label(parent, text=text.upper(),
                bg=COLORS["bg"], fg=COLORS["primary"],
                font=FONTS["kpi_lbl"]).pack(anchor="w", pady=(10, 4))

    def _spacer(self, parent, h=8):
        tk.Frame(parent, bg=COLORS["bg"], height=h).pack()

    # ==============================================================
    # ARCHIVO
    # ==============================================================
    # ==============================================================
    # POOL DE DATAFRAMES
    # ==============================================================
    def _dataset_name_for(self, path: Path) -> str:
        base = path.stem
        name = base
        i = 2
        while name in self.loaded_datasets:
            name = f"{base} ({i})"
            i += 1
        return name

    def _read_dataset(self, path: Path, retained: int, log) -> pd.DataFrame:
        """Wrapper de compatibilidad para la carga sin interfaz."""
        return read_dataset(
            path, retained, log,
            ensure_load_fits=memory_budget.ensure_load_fits,
            loader=load_file,
        )

    def _start_file_load(self, path: Path):
        name = self._dataset_name_for(path)
        resident_frames = tuple(frame for frame in self.loaded_datasets.values()
                                if isinstance(frame, pd.DataFrame))
        started = time.perf_counter()

        def work(cancel, progress):
            retained = sum(int(frame.memory_usage(deep=True).sum())
                           for frame in resident_frames)
            df = self._read_dataset(path, retained, progress)
            if cancel.is_set():
                raise TaskCancelled()
            if isinstance(df, ActiveDataset):
                preview = df.preview(cancel=cancel)
                # La interfaz solo necesita el total; el perfil de nulos de
                # todas las columnas obliga a recorrerlas sin aportar nada
                # al primer dibujo del dataset.
                stats = {"rows": df.row_count(cancel),
                         "columns": len(df.columns), "nulls": {}}
                return LazyLoaded(df, stats, preview)
            return self._ensure_date_sorted(df, name)

        def done(df):
            self.loaded_datasets[name] = df
            self._refresh_dataset_combo()
            self._activate_dataset(name, already_sorted=True)
            elapsed = time.perf_counter() - started
            rows = df.stats["rows"] if isinstance(df, LazyLoaded) else len(df)
            columns = len(df.dataset.columns) if isinstance(df, LazyLoaded) else df.shape[1]
            self._log(f"[OK] Cargado '{name}': {rows:,} × "
                      f"{columns} en {elapsed:.1f}s")
            if isinstance(df, LazyLoaded):
                resource_message = df.dataset.resource_status()["message"]
                self._log(f"[RECURSOS] {resource_message}")
                self._toast(resource_message, "info", 8000)
            else:
                self._toast(f"'{name}' cargado en {elapsed:.1f}s", "success")

        return self._start_task(work, done, f"Cargando {path.name}...")

    def _refresh_dataset_combo(self):
        if not hasattr(self, "combo_dataset"):
            return
        names = list(self.loaded_datasets.keys())
        self.combo_dataset["values"] = names
        if self.active_dataset_name and self.active_dataset_name in names:
            self.combo_dataset.set(self.active_dataset_name)
        elif names:
            self.combo_dataset.set(names[0])

    def _on_dataset_change(self, _event=None):
        if self.tasks.busy:
            self._refresh_dataset_combo()
            self._toast("Espera a que termine la tarea activa.", "warning")
            return
        name = self.combo_dataset.get()
        if name and name != self.active_dataset_name:
            self._activate_dataset(name, already_sorted=True)
            self._toast(f"Dataset activo: {name}", "info", 1500)


    def on_add_file_to_pool(self):
        """Añade un archivo al pool."""
        path = filedialog.askopenfilename(
            parent=self, title="Añadir archivo al pool",
            filetypes=[
                ("Todos los soportados", "*.xlsx *.xlsm *.xls *.csv *.txt"),
                ("Excel", "*.xlsx *.xlsm *.xls"),
                ("CSV", "*.csv"),
                ("Texto", "*.txt"),
                ("Todos", "*.*"),
            ],
        )
        if not path:
            return
        self._start_file_load(Path(path))

    def on_remove_from_pool(self):
        if self.tasks.busy:
            self._toast("Espera a que termine la tarea activa.", "warning")
            return
        name = self.active_dataset_name
        if not name:
            self._toast("No hay dataset activo.", "warning")
            return
        if not messagebox.askyesno(
                "Quitar del pool",
                f"¿Quitar '{name}' del pool de DataFrames?", parent=self):
            return
        self.loaded_datasets.pop(name, None)
        self._log(f"[OK] '{name}' eliminado del pool")
        if self.loaded_datasets:
            next_name = next(iter(self.loaded_datasets))
            self._activate_dataset(next_name, already_sorted=True)
        else:
            self._clear_active_dataset()

    def _clear_active_dataset(self):
        """Vacía la UI cuando no queda ningún DF en el pool."""
        self.active_dataset_name = None
        self.df_raw = None
        self.df_view = None
        self.df_table = None
        self._df_original = None
        self.session.active_dataset = None
        self.session.active_stats = None
        self.session.base_view = None
        self._tb_builder_dataset = None
        self._lazy_preview_key = None
        self._pandas_filter_key = None
        self.file_path = None
        self.tb_rows.clear()
        self.tb_cols.clear()
        self.tb_vals.clear()
        self.tb_pivot_enabled = False
        self.tb_filters.clear()
        self.tb_col_types.clear()
        self.visible_columns = []
        self.value_filters = {}

        self.lbl_file.config(text="(ninguno)", fg=COLORS["text_muted"])
        self.combo_date["values"] = []
        self.combo_date.set("")
        self.lbl_range.config(text="(sin datos)", fg=COLORS["text_muted"])
        self.entry_start.delete(0, "end")
        self.entry_end.delete(0, "end")

        self._clear_plots()
        self._refresh_preview()
        self._refresh_kpis()
        self._refresh_header()
        self._refresh_dataset_combo()

    def _activate_dataset(self, name: str, already_sorted: bool = False):
        if name not in self.loaded_datasets:
            return

        payload = self.loaded_datasets[name]

        if isinstance(payload, LazyLoaded):
            dataset = payload.dataset.reset()
            from core.diagnostics import log_debug
            log_debug("DATA", "active dataset", name=name,
                      backend=dataset.backend, rows=payload.stats["rows"],
                      columns=len(dataset.columns))
            preview = payload.preview
            self.session.active_dataset = dataset
            self.session.active_stats = payload.stats
            self.session.base_view = None
            self._pandas_filter_key = None
            self._preview_page = 0
            self._lazy_preview_key = (dataset.version_token(), None, 0)
            self._tb_builder_dataset = None
            self.active_dataset_name = name
            self.df_raw = preview
            self.df_view = preview
            self._df_original = preview
            self.df_table = preview
            self._duck_view = None
            self.file_path = Path(dataset.origin or dataset.source)
            self._view_is_built = False
            self.tb_rows.clear()
            self.tb_cols.clear()
            self.tb_vals.clear()
            self.tb_pivot_enabled = False
            self.tb_filters.clear()
            self.tb_col_types.clear()
            self.visible_columns = []
            self.value_filters = {}
            self._kpi_cache_key = None
            self.lbl_file.config(
                text=f"{name} · {payload.stats['rows']:,} × {len(dataset.columns)} ⚡lazy",
                fg=COLORS["warning"])
            self._refresh_dataset_combo()
            self._refresh_header()
            # La página ya se obtuvo en el worker. Pintarla antes de anunciar
            # la carga evita esperar al perfil temporal del CSV completo.
            self._refresh_preview(filtered_df=preview)
            self.after(50, lambda: self._post_activate(name))
            return

        # --- Modo NORMAL: comportamiento actual ---
        df = payload
        self.session.active_stats = None
        self._pandas_filter_key = None
        self._lazy_preview_key = None
        if not already_sorted:
            try:
                df = self._ensure_date_sorted(df, name)
                self.loaded_datasets[name] = df
            except Exception as e:
                print(f"[activate] Error al ordenar por fecha: {e}")
        self.session.active_dataset = ActiveDataset.from_frame(df)
        self.session.base_view = df
        from core.diagnostics import log_debug
        log_debug("DATA", "active dataset", name=name,
                  backend="pandas", rows=len(df), columns=len(df.columns))
        
    # (el resto igual que ahora)

        # ============================================
        # FASE 1 · Síncrona (instantánea)
        # ============================================
        n = len(df)
        txt_n = f"{n:,}".replace(",", ".")
        es_grande = n >= db_engine.UMBRAL_PANDAS

        self.active_dataset_name = name
        self.df_raw = df
        self.df_view = df
        self._df_original = df
        self.df_table = df
        self.file_path = None
        self._view_is_built = False
        self._tb_builder_dataset = None

        # Reset estado pestaña Tabla
        self.tb_rows.clear()
        self.tb_cols.clear()
        self.tb_vals.clear()
        self.tb_pivot_enabled = False
        self.tb_filters.clear()
        self.tb_col_types.clear()
        self.visible_columns = []
        self.value_filters = {}

        # Label sidebar
        if es_grande:
            self.lbl_file.config(
                text=f"{name}  ·  {txt_n} × {df.shape[1]}  ⚡",
                fg=COLORS["warning"])
        else:
            self.lbl_file.config(
                text=f"{name}  ·  {txt_n} × {df.shape[1]}",
                fg=COLORS["text"])

        self._refresh_dataset_combo()
        self._refresh_header()
        self._refresh_kpis()
        self._log(f"[OK] Dataset activo: {name} ({txt_n} filas)")

        # Reset visual rápido del preview y de las tarjetas
        try:
            for item in self.tree.get_children():
                self.tree.delete(item)
            self.tree["columns"] = []
            self.lbl_shape.config(text="Preparando vista...",
                                fg=COLORS["text_dim"])
        except Exception:
            pass

        # ============================================
        # FASE 2 · Asíncrona (50 ms después)
        # ============================================
        self.after(50, lambda: self._post_activate(name))

    def _ensure_date_sorted(self, df, name: str):
        return ensure_date_sorted(df, name)
     
    def _post_activate(self, name: str):
        """
        Trabajo pesado de activación, ejecutado después de que la UI
        haya pintado el cambio. Si algo peta, no congela la app.
        """
        if name != self.active_dataset_name:
            return  # el usuario cambió de dataset mientras trabajábamos

        import time as _t
        _t0 = _t.time()

        df = self.df_raw
        if df is None:
            return

        # --- Detección de tipos (con muestra) ---
        try:
            self.column_types.clear()
            self._auto_detect_types(apply=False)
            self._log(f"[activar] tipos: {_t.time()-_t0:.2f}s")
        except Exception as e:
            self._log(f"[WARN] detect types: {e}")

        # --- Combo de fecha ---
        try:
            date_cols = get_date_columns(df)
            if date_cols:
                self.combo_date["values"] = date_cols
                self.combo_date.current(0)
                self._populate_date_range_lazy(date_cols[0])
                self._auto_set_granularity(date_cols[0])
                self._log(f"[activar] fechas: {_t.time()-_t0:.2f}s")
            else:
                self.combo_date["values"] = ["(sin fechas)"]
                self.combo_date.current(0)
                self.lbl_range.config(text="Sin fechas detectadas",
                                    fg=COLORS["warning"])
        except Exception as e:
            self._log(f"[WARN] date range: {e}")

        # --- Refrescos ---
        try:
            self._refresh_preview()
            self._log(f"[activar] preview: {_t.time()-_t0:.2f}s")
        except Exception as e:
            self._log(f"[WARN] preview: {e}")

        try:
            self._refresh_type_editor()
        except Exception:
            pass

        try:
            self._refresh_pivot_controls()
        except Exception:
            pass

        try:
            self._tb_refresh_source()
            self._tb_refresh_lists()
            self._log(f"[activar] tabla: {_t.time()-_t0:.2f}s")
        except Exception as e:
            self._log(f"[WARN] tabla: {e}")

        # Placeholder del constructor
        try:
            self.tb_preview_tree["columns"] = []
            self.tb_lbl_preview_info.config(
                text="Constructor listo · configura filas/columnas/valores",
                fg=COLORS["text_dim"])
        except Exception:
            pass

        self._log(f"[OK] Activación completada en "
                f"{_t.time()-_t0:.2f}s")

    # ==============================================================
    # ABRIR ARCHIVO (entrada al pool)
    # ==============================================================
    def on_open_file(self):
        path = filedialog.askopenfilename(
            parent=self, title="Selecciona un archivo",
            filetypes=[
                ("Todos los soportados",
                 "*.xlsx *.xlsm *.xls *.csv *.txt *.parquet"),
                ("Excel",   "*.xlsx *.xlsm *.xls"),
                ("CSV",     "*.csv"),
                ("Parquet", "*.parquet"),
                ("Texto",   "*.txt"),
                ("Todos",   "*.*"),
            ],
        )
        if not path:
            return

        p = Path(path)
        self._start_file_load(p)
    

    # ==============================================================
    # DIÁLOGO DE UNIÓN
    # ==============================================================
    def on_open_merge_dialog(self):
        if self.tasks.busy:
            self._toast("Espera a que termine la tarea activa.", "warning")
            return
        if len(self.loaded_datasets) < 2:
            self._toast("Necesitas al menos 2 DataFrames en el pool.",
                        "warning")
            return
        dlg = MergeDialog(self, self.loaded_datasets,
                        default_a=self.active_dataset_name)
        self.wait_window(dlg)
        if not self.winfo_exists():
            return
        if dlg.result is None:
            return
        name = dlg.result["name"]
        df = dlg.result["df"]

        # Guardia final: no activar DFs gigantes que cuelgan la UI
        self.loaded_datasets[name] = df

        n = len(df)
        txt_n = f"{n:,}".replace(",", ".")
        self._log(f"[OK] '{name}' creado: {txt_n} × {df.shape[1]}")

        # Activarlo directamente: la activación ya es ligera
        self._activate_dataset(name, already_sorted=True)
        self._toast(f"'{name}' añadido al pool ({txt_n} filas)",
                    "success")
    # FECHAS · HELPERS
    # --------------------------------------------------------------
    def _populate_date_range(self, date_col: str):
        """Rellena Desde/Hasta con el rango real de la columna elegida.

        Aplica coerción automática (Mes, Año, Semana, texto...) a datetime
        para que toda la app trabaje con una columna temporal consistente.
        """
        if self.df_raw is None or date_col not in self.df_raw.columns:
            return

        # --- Coerción automática a datetime si no lo es ya ---
        if not pd.api.types.is_datetime64_any_dtype(self.df_raw[date_col]):
            try:
                converted = coerce_date_column(self.df_raw[date_col],
                                                date_col)
                if pd.api.types.is_datetime64_any_dtype(converted):
                    # Aplicar a df_raw y demás copias
                    self.df_raw[date_col] = converted
                    if (self.df_view is not None
                            and date_col in self.df_view.columns):
                        self.df_view[date_col] = coerce_date_column(
                            self.df_view[date_col], date_col)
                    if (self.df_table is not None
                            and date_col in self.df_table.columns):
                        self.df_table[date_col] = coerce_date_column(
                            self.df_table[date_col], date_col)

                    # Marcar el tipo como "fecha"
                    self.column_types[date_col] = "fecha"
                    if hasattr(self, "tb_col_types"):
                        self.tb_col_types[str(date_col)] = "fecha"

                    self._log(f"[OK] Columna '{date_col}' convertida a "
                            f"formato fecha automáticamente.")
                else:
                    self.lbl_range.config(
                        text=f"⚠ No se pudo interpretar '{date_col}' "
                            f"como fecha",
                        fg=COLORS["warning"])
                    return
            except Exception as e:
                self._log(f"[WARN] Coerción de '{date_col}': {e}")

        # --- Extraer rango ---
        s = pd.to_datetime(self.df_raw[date_col], errors="coerce").dropna()
        try:
            if getattr(s.dt, "tz", None) is not None:
                s = s.dt.tz_convert(None)
        except Exception:
            pass

        if s.empty:
            self.lbl_range.config(text="(columna sin valores)",
                                fg=COLORS["warning"])
            return

        dmin, dmax = s.min(), s.max()
        self.entry_start.delete(0, "end")
        self.entry_start.insert(0, str(dmin.date()))
        self.entry_end.delete(0, "end")
        self.entry_end.insert(0, str(dmax.date()))

        gran = detect_granularity(self.df_raw, date_col)
        self.lbl_range.config(
            text=f"{dmin.date()} → {dmax.date()}  ({gran})",
            fg=COLORS["text"])

        # Refrescar tarjetas del constructor para que el cambio se vea
        try:
            self._tb_refresh_source()
            self._tb_refresh_lists()
        except Exception:
            pass
        try:
            self._refresh_type_editor()
        except Exception:
            pass

    def _populate_date_range_lazy(self, date_col: str):
        """
        Rellena el rango de fechas SIN convertir toda la columna si el
        DF es grande. Usa min/max directos si ya es datetime.
        """
        if self.df_raw is None or date_col not in self.df_raw.columns:
            return

        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            def work(cancel, progress):
                # El perfil completo calcula nulos de todas las columnas.
                # Para este control solo necesitamos MIN/MAX de la fecha.
                return active.date_range(date_col, cancel)

            def done(result):
                if self.session.active_dataset is not active:
                    return
                dmin, dmax = result
                if dmin is None or dmax is None:
                    self.lbl_range.config(text="(columna sin fechas válidas)",
                                          fg=COLORS["warning"])
                    return
                self.entry_start.delete(0, "end")
                self.entry_start.insert(0, str(dmin))
                self.entry_end.delete(0, "end")
                self.entry_end.insert(0, str(dmax))
                self.lbl_range.config(text=f"{dmin} → {dmax} (dataset completo)",
                                      fg=COLORS["text"])
                if self.session.active_stats is not None:
                    self.session.active_stats["date_range"] = (dmin, dmax)
                    self.session.active_stats["date_range_column"] = date_col

            self._start_task(work, done, "Calculando periodo completo...")
            return

        s_full = self.df_raw[date_col]

        # --- Caso 1: ya es datetime ---
        if pd.api.types.is_datetime64_any_dtype(s_full):
            try:
                dmin = s_full.min()
                dmax = s_full.max()
            except Exception:
                dmin = dmax = None
        # --- Caso 2: no es datetime → usar muestra ---
        else:
            n_total = len(s_full)
            if n_total > 100_000:
                sample = s_full.head(100_000)
            else:
                sample = s_full
            try:
                s = coerce_date_column(sample, date_col).dropna()
                if s.empty:
                    self.lbl_range.config(
                        text="(columna sin valores válidos)",
                        fg=COLORS["warning"])
                    return
                dmin, dmax = s.min(), s.max()
            except Exception as e:
                self._log(f"[WARN] _populate_date_range_lazy: {e}")
                self.lbl_range.config(text="(error al parsear fechas)",
                                    fg=COLORS["warning"])
                return

        if dmin is None or pd.isna(dmin):
            self.lbl_range.config(text="(sin rango)",
                                fg=COLORS["warning"])
            return

        self.entry_start.delete(0, "end")
        self.entry_start.insert(0, str(pd.Timestamp(dmin).date()))
        self.entry_end.delete(0, "end")
        self.entry_end.insert(0, str(pd.Timestamp(dmax).date()))

        gran = detect_granularity(self.df_raw, date_col)
        self.lbl_range.config(
            text=f"{pd.Timestamp(dmin).date()} → "
                f"{pd.Timestamp(dmax).date()}  ({gran})",
            fg=COLORS["text"])

    def _auto_set_granularity(self, date_col: str):
        """Pone el combo de granularidad según los datos reales."""
        if self.df_raw is None:
            return
        g = detect_granularity(self.df_raw, date_col)
        # Mapeo inverso
        inv = {"D": "Diario", "W": "Semanal", "M": "Mensual",
            "Q": "Trimestral", "Y": "Anual"}
        label = inv.get(g, "Original")
        try:
            idx = list(FREQ_MAP.keys()).index(label)
            self.combo_freq.current(idx)
        except ValueError:
            self.combo_freq.current(0)

    def _on_date_col_change(self, _event=None):
        """Al cambiar de columna de fecha, refresca rango y granularidad."""
        date_col = self.combo_date.get().strip()
        if not date_col or date_col == "(sin fechas)":
            return
        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            self._populate_date_range_lazy(date_col)
            return
        frames = tuple({id(frame): frame for frame in
                        (self.df_raw, self.df_view, self.df_table)
                        if frame is not None and date_col in frame.columns}.values())
        if not frames:
            return

        def work(cancel, progress):
            converted = []
            for frame in frames:
                memory_budget.ensure_dataframe_operation_fits(
                    frame, 2, "Conversión de fecha")
                if cancel.is_set():
                    raise TaskCancelled()
                series = frame[date_col]
                if not pd.api.types.is_datetime64_any_dtype(series):
                    series = coerce_date_column(series, date_col)
                if not pd.api.types.is_datetime64_any_dtype(series):
                    raise ValueError(f"No se pudo interpretar '{date_col}' como fecha")
                converted.append((frame, series))
            dates = pd.to_datetime(converted[0][1], errors="coerce").dropna()
            if dates.empty:
                raise ValueError("Columna sin fechas válidas")
            if getattr(dates.dt, "tz", None) is not None:
                dates = dates.dt.tz_convert(None)
            dmin, dmax = dates.min(), dates.max()
            gran = detect_granularity(
                pd.DataFrame({date_col: converted[0][1]}), date_col)
            return converted, dmin, dmax, gran

        def done(value):
            converted, dmin, dmax, gran = value
            for frame, series in converted:
                frame[date_col] = series
            self.column_types[date_col] = "fecha"
            self.tb_col_types[str(date_col)] = "fecha"
            self.entry_start.delete(0, "end")
            self.entry_start.insert(0, str(dmin.date()))
            self.entry_end.delete(0, "end")
            self.entry_end.insert(0, str(dmax.date()))
            self.lbl_range.config(text=f"{dmin.date()} → {dmax.date()}  ({gran})",
                                  fg=COLORS["text"])
            self._auto_set_granularity(date_col)
            self._toast(f"Columna temporal activa: {date_col}",
                        "info", 1800)
        self._start_task(work, done, "Calculando rango de fechas...")
    # ==============================================================
    # VISTA PREVIA
    # ==============================================================
    def _get_filtered_view(self) -> pd.DataFrame | None:
        df = self.df_view if self.df_view is not None else self.df_raw
        if df is None:
            return None
        return self._filter_values(df, self.value_filters)

    @staticmethod
    def _filter_values(df, filters):
        return filter_values(df, filters)

    def _prepare_df_for_analysis(self, df: pd.DataFrame,
                                 column_types=None) -> pd.DataFrame:
        column_types = (self.column_types if column_types is None
                        else column_types)
        return prepare_df_for_analysis(df, column_types)

    def _change_preview_page(self, step):
        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            if self.tasks.busy:
                return
            total = (self.session.active_stats or {}).get("rows", 0)
            pages = max(1, (total + 199) // 200)
            self._preview_page = min(max(0, self._preview_page + step), pages - 1)
        else:
            self._preview_page += step
        self._refresh_preview()

    def _combined_active_filters(self):
        filters = {c: set(values) for c, values in self.value_filters.items()
                   if values is not None}
        for column, allowed in self.tb_filters.items():
            filters[column] = (filters[column] & set(allowed)
                               if column in filters else set(allowed))
        return filters

    def _schedule_pandas_filters(self):
        active = self.session.active_dataset
        base = self.session.base_view
        if active is None or active.backend != "pandas" or base is None:
            return False
        filters = self._combined_active_filters()
        filter_key = tuple((c, tuple(sorted(v, key=str)))
                           for c, v in sorted(filters.items()))
        key = (id(base), filter_key)
        if key == self._pandas_filter_key:
            return False
        if not filters:
            self.df_view = base
            self.session.active_dataset = ActiveDataset.from_frame(base)
            self._pandas_filter_key = key
            self._kpi_cache_key = None
            return False
        if self.tasks.busy:
            if not self.tasks.closing:
                self.after(300, self._refresh_preview)
            return True

        def work(cancel, progress):
            memory_budget.ensure_dataframe_operation_fits(
                base, 1, "Filtro del dataset activo")
            if cancel.is_set():
                raise TaskCancelled()
            result = filter_values(base, filters)
            if cancel.is_set():
                raise TaskCancelled()
            return result

        def done(result):
            if self.session.base_view is not base:
                self._refresh_preview()
                return
            if self._combined_active_filters() != filters:
                self._refresh_preview()
                return
            self.df_view = result
            self.session.active_dataset = ActiveDataset.from_frame(result)
            self._pandas_filter_key = key
            self._kpi_cache_key = None
            self._preview_page = 0
            self._refresh_preview()

        self._start_task(work, done, "Filtrando dataset activo...")
        return True

    def _schedule_lazy_preview(self):
        current = self.session.active_dataset
        if current is None or current.backend == "pandas":
            return False
        payload = self.loaded_datasets.get(self.active_dataset_name)
        if not isinstance(payload, LazyLoaded):
            return False
        table_result = (self._view_is_built
                        and self._tb_builder_dataset is not None)
        filters = (self._combined_active_filters()
                   if current.backend != "query" and not table_result else
                   {col: set(values) for col, values in self.value_filters.items()
                    if col in current.columns})
        dataset = current.with_filters(filters)
        date_col = self.combo_date.get().strip()
        if date_col not in dataset.columns:
            date_col = None
        key = (dataset.version_token(), date_col, self._preview_page)
        if key == self._lazy_preview_key:
            return False
        if self.tasks.busy:
            if not self.tasks.closing:
                self.after(300, self._refresh_preview)
            return True
        cached_stats = (self.session.active_stats
                        if self._lazy_preview_key is not None
                        and key[0] == self._lazy_preview_key[0]
                        else None)

        def work(cancel, progress):
            stats = (dict(cached_stats) if cached_stats is not None
                     else {"rows": dataset.row_count(cancel),
                           "columns": len(dataset.projection or dataset.columns),
                           "nulls": {}})
            if date_col and stats.get("date_range_column") != date_col:
                # date_range is a single bounded aggregate and avoids the
                # all-column null-count scan performed by stats(..., date).
                stats["date_range"] = dataset.date_range(date_col, cancel)
                stats["date_range_column"] = date_col
            page_count = max(1, (stats["rows"] + 199) // 200)
            page = min(self._preview_page, page_count - 1)
            preview = dataset.preview(offset=page * 200, cancel=cancel)
            return stats, page, preview

        def done(result):
            if self.loaded_datasets.get(self.active_dataset_name) is not payload:
                return
            if self.session.active_dataset is not current:
                return
            latest_filters = (self._combined_active_filters()
                              if current.backend != "query" and not table_result else
                              {col: set(values) for col, values in
                               self.value_filters.items()
                               if col in current.columns})
            if latest_filters != filters:
                self._refresh_preview()
                return
            stats, page, preview = result
            self.session.active_dataset = dataset
            self.session.active_stats = stats
            self._preview_page = page
            self.df_view = preview
            self._lazy_preview_key = (dataset.version_token(), date_col, page)
            self._kpi_cache_key = None
            self._refresh_preview()
            if date_col and stats.get("date_range"):
                dmin, dmax = stats["date_range"]
                if dmin is not None and dmax is not None:
                    self.lbl_range.config(
                        text=f"{dmin} → {dmax} (dataset completo)",
                        fg=COLORS["text"])

        self._start_task(work, done, "Consultando dataset activo...")
        return True

    def _refresh_preview(self, filtered_df=None):
        if filtered_df is None and self._schedule_lazy_preview():
            return
        if filtered_df is None and self._schedule_pandas_filters():
            return
        for item in self.tree.get_children():
            self.tree.delete(item)

        df_full = self.df_view if self.df_view is not None else self.df_raw
        active = self.session.active_dataset
        is_lazy = active is not None and active.backend != "pandas"
        if not is_lazy and id(df_full) != self._preview_source_id:
            self._preview_page = 0
            self._preview_source_id = id(df_full)
        if df_full is None:
            self.lbl_shape.config(text="(sin datos)",
                                fg=COLORS["text_muted"])
            self.tree["columns"] = []
            self._preview_page_controls(0, 1)
            self._refresh_kpis()
            return

        # ⚠ Evitar reaplicar value_filters sobre df_view ya construido
        # (df_view ya es el resultado del pivot; solo aplicar value_filters)
        if is_lazy or (active is not None and active.backend == "pandas"):
            df = df_full
        elif self.value_filters:
            if filtered_df is None and len(df_full) > 100_000:
                source = df_full
                filters = {key: set(value) if value is not None else None
                           for key, value in self.value_filters.items()}

                def work(cancel, progress):
                    memory_budget.ensure_dataframe_operation_fits(
                        source, 1, "Filtro de valores")
                    if cancel.is_set():
                        raise TaskCancelled()
                    return MMMApp._filter_values(source, filters)

                def done(result):
                    current = (self.df_view if self.df_view is not None
                               else self.df_raw)
                    if current is source and self.value_filters == filters:
                        self._refresh_preview(filtered_df=result)
                self._start_task(work, done, "Filtrando vista previa...")
                return
            df = (filtered_df if filtered_df is not None
                  else self._get_filtered_view())
        else:
            df = df_full

        # Columnas visibles
        if self.visible_columns:
            cols = [c for c in df.columns if str(c) in self.visible_columns]
        else:
            cols = list(df.columns)

        if not cols:
            self.lbl_shape.config(text="(sin columnas visibles)",
                                fg=COLORS["warning"])
            self.tree["columns"] = []
            self._preview_page_controls(0, 1)
            self._refresh_kpis()
            return

        # --- Página acotada ---
        n_total = self.session.active_stats["rows"] if is_lazy else len(df)
        if is_lazy:
            df_show = df
            total_pages = max(1, (n_total + 199) // 200)
        else:
            df_show, self._preview_page, total_pages = preview_page(
                df, self._preview_page)
        self._preview_page_controls(self._preview_page, total_pages)

        # Texto informativo
        def fmt(n):
            return f"{n:,}".replace(",", ".")

        total_txt = (f"{fmt(n_total)} × {self.session.active_stats['columns']}"
                     if is_lazy else f"{fmt(df_full.shape[0])} × {df_full.shape[1]}")
        shown_txt = f"{fmt(len(df_show))} × {len(cols)}"
        if total_pages > 1:
            txt = f"{shown_txt}  ·  (de {fmt(n_total)} filas; total {total_txt})"
        elif len(df) != df_full.shape[0] or len(cols) != df_full.shape[1]:
            txt = f"{shown_txt}  ·  (total {total_txt})"
        else:
            txt = total_txt
        self.lbl_shape.config(text=txt, fg=COLORS["text"])

        col_strs = [str(c) for c in cols]
        self.tree["columns"] = col_strs
        for c in col_strs:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=120, anchor="w", stretch=False)

        # Iterar solo sobre la muestra
        df_show_reset = df_show.reset_index(drop=True)
        for i in range(len(df_show_reset)):
            row = df_show_reset.iloc[i]
            values = [self._format_cell(row[c]) for c in cols]
            tag = "even" if i % 2 == 0 else "odd"
            self.tree.insert("", "end", values=values, tags=(tag,))

        self._refresh_kpis()

    def _preview_page_controls(self, page, total_pages):
        self.lbl_preview_page.config(text=f"Página {page + 1}/{total_pages}")
        self.btn_preview_previous.config(
            state="normal" if page > 0 else "disabled")
        self.btn_preview_next.config(
            state="normal" if page + 1 < total_pages else "disabled")
    @staticmethod
    def _format_cell(v) -> str:
        if pd.isna(v):
            return ""
        # Fechas
        if isinstance(v, (pd.Timestamp,)):
            return v.strftime("%Y-%m-%d")
        # Enteros grandes sin notación científica
        if isinstance(v, (int, np.integer)):
            return f"{v:,}".replace(",", ".")
        # Floats: 2 decimales con separador de miles si > 1000
        if isinstance(v, (float, np.floating)):
            if abs(v) >= 1000:
                return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            return f"{v:.4g}"
        return str(v)
    # ==============================================================
    # TIPOS
    # ==============================================================
    def _refresh_type_editor(self):
        for w in self.type_frame.winfo_children():
            w.destroy()
        self.type_vars.clear()

        df = self.df_raw
        if df is None:
            tk.Label(self.type_frame, text="(sin datos)",
                    bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                    font=FONTS["small"]).pack(anchor="w", padx=4)
            if hasattr(self, "lbl_types_count"):
                self.lbl_types_count.config(text="")
            return

        for i, col in enumerate(df.columns):
            bg = COLORS["bg_card"] if i % 2 == 0 else COLORS["bg_card_alt"]
            row = tk.Frame(self.type_frame, bg=bg)
            row.pack(fill="x", pady=0)

            tk.Label(row, text=str(col), width=16, anchor="w",
                    bg=bg, fg=COLORS["text"],
                    font=FONTS["small"]).pack(
                side="left", padx=(6, 0), pady=3)

            var = tk.StringVar(value=self.column_types.get(col, "auto"))
            ttk.Combobox(row, textvariable=var, values=TYPE_OPTIONS,
                        state="readonly", width=12).pack(
                side="right", padx=(0, 6), pady=3)
            self.type_vars[col] = var

        if hasattr(self, "lbl_types_count"):
            self.lbl_types_count.config(text=f"{len(df.columns)} columnas")

    def on_auto_detect_types(self):
        if self.tasks.busy:
            self._toast("Espera a que termine la tarea activa.", "warning")
            return
        if self.df_raw is None:
            self._toast("Carga un archivo primero.", "warning")
            return
        self.column_types.clear()
        self._auto_detect_types(apply=False)
        self._refresh_type_editor()
        self._refresh_kpis()
        self._log("[OK] Tipos detectados automáticamente.")
        self._toast("Tipos detectados", "success", 1800)

    def _auto_detect_types(self, apply: bool):
        """Detecta tipos en una muestra y opcionalmente los aplica."""
        df = self.df_raw
        if df is None:
            return
        self.column_types.update(self._detect_types(df))
        if apply:
            self._apply_types_to_df()

    @staticmethod
    def _detect_types(df):
        return detect_types(df)

    def on_apply_types(self):
        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            self._toast("Los tipos del archivo grande se gestionan en DuckDB.",
                        "info", 4000)
            return
        if self.df_raw is None:
            self._toast("Carga un archivo primero.", "warning")
            return

        n = len(self.df_raw)
        if n >= db_engine.UMBRAL_PANDAS:
            if not messagebox.askyesno(
                    "Aplicar tipos",
                    f"El dataset tiene {n:,} filas.\n\n"
                    f"Aplicar tipos puede tardar 15-30 s y consumir "
                    f"bastante RAM.\n\n¿Continuar?".replace(",", "."), parent=self):
                return

        for col, var in self.type_vars.items():
            self.column_types[col] = var.get()
        source = self.df_raw
        types = dict(self.column_types)

        def work(cancel, progress):
            memory_budget.ensure_dataframe_operation_fits(
                source, 2, "Conversión de tipos")
            return self._convert_types(source, types, cancel)

        def done(converted):
            self.df_raw = converted
            if not self._view_is_built:
                self.df_view = converted
            self.session.active_dataset = ActiveDataset.from_frame(self.df_view)
            self.session.base_view = self.df_view
            self._pandas_filter_key = None
            self._refresh_preview()
            self._refresh_pivot_controls()
            self._log("[OK] Tipos aplicados.")
            self._toast("Tipos aplicados", "success", 1800)
        self._start_task(work, done, "Aplicando tipos...")

    def _apply_types_to_df(self):
        df = MMMApp._convert_types(self.df_raw, self.column_types)
        self.df_raw = df
        if not self._view_is_built:
            self.df_view = df
        self.session.active_dataset = ActiveDataset.from_frame(self.df_view)
        self.session.base_view = self.df_view
        self._pandas_filter_key = None

    @staticmethod
    def _convert_types(source, types, cancel=None):
        return convert_types(source, types, cancel)
    # ==============================================================
    # PIVOT
    # ==============================================================
    def _refresh_pivot_controls(self):
        df = self.df_raw
        if df is None:
            self.pivot_index["values"] = []
            self.pivot_columns["values"] = []
            self.pivot_values.delete(0, "end")
            return

        cols = [str(c) for c in df.columns]
        self.pivot_index["values"] = cols
        self.pivot_columns["values"] = cols

        if cols and not self.pivot_index.get():
            date_col = detect_date_column(df)
            self.pivot_index.set(str(date_col) if date_col else cols[0])

        if cols and not self.pivot_columns.get():
            for c in df.columns:
                if self.column_types.get(c) == "categorica":
                    self.pivot_columns.set(str(c))
                    break

        self.pivot_values.delete(0, "end")
        for i, c in enumerate(df.columns):
            self.pivot_values.insert("end", str(c))
            if self.column_types.get(c) == "numero":
                self.pivot_values.selection_set(i)

    def on_pivot(self):
        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            self._toast("Pivot lazy en preparación; evita usar solo la muestra.",
                        "warning", 5000)
            return
        df = self.df_raw
        if df is None:
            self._toast("Carga un archivo primero.", "warning")
            return

        idx_col = self.pivot_index.get().strip()
        col_col = self.pivot_columns.get().strip()
        agg = self.pivot_agg.get().strip() or "sum"

        selected = self.pivot_values.curselection()
        val_cols = [self.pivot_values.get(i) for i in selected]

        if not idx_col or not col_col or not val_cols:
            self._toast("Selecciona índice, columnas y al menos un valor.",
                        "warning")
            return
        if idx_col == col_col:
            self._toast("Índice y columnas deben ser distintos.", "warning")
            return
        if idx_col in val_cols or col_col in val_cols:
            self._toast("Un valor no puede ser índice o columna.",
                        "warning")
            return

        naming = self.pivot_naming.get()

        def work(cancel, progress):
            memory_budget.ensure_dataframe_operation_fits(
                df, 3, "Pivot")
            if cancel.is_set():
                raise TaskCancelled()
            # Convertir columnas 'category' a str para que no rompan
            # las operaciones de agregación/pivot.
            df_pivot = df.copy()
            for c in df_pivot.columns:
                if isinstance(df_pivot[c].dtype, pd.CategoricalDtype):
                    df_pivot[c] = df_pivot[c].astype(str)

            pivoted = df_pivot.pivot_table(
                index=idx_col,
                columns=col_col,
                values=val_cols,
                aggfunc=agg,
                fill_value=0,
            )

            if isinstance(pivoted.columns, pd.MultiIndex):
                flat = []
                for metrica, soporte in pivoted.columns:
                    if naming == "metrica_soporte":
                        flat.append(f"{metrica} - {soporte}")
                    else:
                        flat.append(f"{soporte} - {metrica}")
                pivoted.columns = flat
            else:
                pivoted.columns = [str(c) for c in pivoted.columns]

            pivoted = pivoted.reset_index()
            pivoted.columns.name = None
            types = MMMApp._detect_types(pivoted)
            pivoted = MMMApp._convert_types(pivoted, types, cancel)
            return pivoted, types

        def done(value):
            pivoted, types = value
            self.df_raw = pivoted
            self.df_view = pivoted
            self.session.active_dataset = ActiveDataset.from_frame(pivoted)
            self.session.base_view = pivoted
            self._pandas_filter_key = None

            # Reset de visibilidad y filtros: las columnas han cambiado
            self.visible_columns = []
            self.value_filters = {}

            self._log(f"[OK] Pivot aplicado ({agg}): "
                    f"{pivoted.shape[0]} × {pivoted.shape[1]}")

            self.column_types.clear()
            self.column_types.update(types)

            self._refresh_preview()
            self._refresh_type_editor()
            self._refresh_pivot_controls()
            self._refresh_kpis()
            self._toast(f"Pivot aplicado · {pivoted.shape[1]} columnas",
                        "success")
        self._start_task(work, done, "Aplicando pivot...")

    def on_undo_pivot(self):
        """Restaura SIEMPRE el dataset original cargado al principio."""
        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            self.on_reset_filters()
            return
        if self.tasks.busy:
            self._toast("Espera a que termine la tarea activa.", "warning")
            return
        if self._df_original is None:
            self._toast("No hay ningún dataset cargado.", "warning")
            return
        source = self._df_original

        def work(cancel, progress):
            memory_budget.ensure_dataframe_operation_fits(
                source, 2, "Deshacer pivot")
            types = MMMApp._detect_types(source)
            converted = MMMApp._convert_types(source, types, cancel)
            return converted, types

        def done(value):
            converted, types = value
            self.df_raw = converted
            self.df_view = converted
            self.session.active_dataset = ActiveDataset.from_frame(converted)
            self.session.base_view = converted
            self._pandas_filter_key = None
            self.visible_columns = []
            self.value_filters = {}
            self.column_types.clear()
            self.column_types.update(types)
            self._refresh_preview()
            self._refresh_type_editor()
            self._refresh_pivot_controls()
            self._refresh_kpis()
            self._log("[OK] Pivot deshecho. Vuelto al dataset original.")
            self._toast("Vuelto al dataset original", "info")
        self._start_task(work, done, "Deshaciendo pivot...")

    # ==============================================================
    # FILTROS Y ANÁLISIS
    # ==============================================================
    def on_apply_filters(self):
        if self.df_raw is None:
            self._toast("Carga un archivo primero.", "warning")
            return

        date_col = self.combo_date.get().strip()
        if not date_col or date_col == "(sin fechas)":
            self._toast("Selecciona una columna de fecha.", "warning")
            return
        if date_col not in self.df_raw.columns:
            self._toast(f"Columna '{date_col}' no existe.", "error")
            return

        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            if FREQ_MAP.get(self.combo_freq.get(), "Original") != "Original":
                self._toast("La agregación temporal de este archivo grande aún no está disponible.",
                            "warning", 5000)
                return
            try:
                self.session.active_dataset = active.with_date_range(
                    date_col, self.entry_start.get().strip() or None,
                    self.entry_end.get().strip() or None)
            except (ValueError, KeyError) as exc:
                self._toast(str(exc), "error")
                return
            self._preview_page = 0
            self._refresh_preview()
            return

        source = self.df_raw
        s = self.entry_start.get().strip()
        e = self.entry_end.get().strip()
        freq_label = self.combo_freq.get()
        freq_code = FREQ_MAP.get(freq_label, "Original")

        def work(cancel, progress):
            memory_budget.ensure_dataframe_operation_fits(
                source, 2, "Filtro de fechas")
            if cancel.is_set():
                raise TaskCancelled()
            # La conversión sustituye solo la columna de fecha. Compartir las
            # demás columnas evita duplicar el dataset completo antes del
            # filtro; la selección booleana crea el resultado independiente.
            df = source.copy(deep=False)
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            try:
                if getattr(df[date_col].dt, "tz", None) is not None:
                    df[date_col] = df[date_col].dt.tz_convert(None)
            except Exception:
                pass
            df = df.dropna(subset=[date_col])
            if s:
                df = df[df[date_col] >= pd.Timestamp(s)]
            if e:
                df = df[df[date_col] <= pd.Timestamp(e)]
            if df.empty:
                raise ValueError("El rango no contiene datos.")
            n_before = len(df)
            df = aggregate_period(df, date_col, freq_code)
            return df, n_before

        def done(value):
            df, n_before = value
            self.df_view = df
            self.session.active_dataset = ActiveDataset.from_frame(df)
            self.session.base_view = df
            self._pandas_filter_key = None
            self._log(f"[OK] Filtrado por '{date_col}': "
                      f"{n_before} → {len(df)} filas ({freq_label})")
            self._refresh_preview()
            self._toast(f"Filtros aplicados · {len(df)} filas", "success", 1800)
            if self.value_filters:
                cols_now = set(df.columns)
                self.value_filters = {c: v for c, v in self.value_filters.items()
                                      if c in cols_now}
        self._start_task(work, done, "Aplicando filtros...")

    def on_reset_filters(self):
        """Restaura el rango completo y la granularidad original."""
        if self.tasks.busy:
            self._toast("Espera a que termine la tarea activa.", "warning")
            return
        if self.df_raw is None:
            return
        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            self.session.active_dataset = active.reset()
            self.value_filters = {}
            self.tb_filters.clear()
            self._preview_page = 0
            self._refresh_preview()
            self._toast("Filtros restablecidos", "info", 1500)
            return
        date_col = self.combo_date.get().strip()
        if date_col and date_col != "(sin fechas)":
            self._populate_date_range_lazy(date_col)
            self._auto_set_granularity(date_col)
        self.df_view = self.df_raw
        self.session.active_dataset = ActiveDataset.from_frame(self.df_raw)
        self.session.base_view = self.df_raw
        self.value_filters = {}
        self.tb_filters.clear()
        self._pandas_filter_key = None
        self._refresh_preview()
        self._toast("Filtros restablecidos", "info", 1500)

    def on_run_analysis(self):
        active = self.session.active_dataset
        if active is not None and active.backend != "pandas":
            if self.chosen_analysis is None:
                self._toast("Selecciona un análisis.", "warning")
                return
            if self.tasks.busy:
                self._toast("Espera a que termine la tarea activa.", "warning")
                return

            def work(cancel, progress):
                return active.analysis_frame(cancel)

            def done(frame):
                if self.session.active_dataset is active:
                    self._run_analysis_with_frame(frame)

            self._start_task(work, done, "Preparando dataset activo para análisis...")
            return
        self._run_analysis_with_frame(
            self.df_view if self.df_view is not None else self.df_raw)

    def _run_analysis_with_frame(self, df_for_analysis, module=None):
        if self.df_raw is None:
            self._toast("Carga un archivo primero.", "warning")
            return
        if self.chosen_analysis is None:
            self._toast("Selecciona un análisis.", "warning")
            return

        if df_for_analysis is None or df_for_analysis.empty:
            self._toast("No hay datos para analizar.", "warning")
            return
        # Aplicar tipos solo si aún no se han aplicado y hay algo que
        # convertir. Para DFs grandes esto ahorra decenas de segundos.
        # --------------------------------------------------------------
        # Conversión de tipos SIN tocar df_raw ni df_view
        # (solo se modifica una copia local para el análisis)
        # --------------------------------------------------------------
        type_snapshot = dict(self.column_types)

        analysis = self.chosen_analysis
        name = analysis["name"]
        if module is None and is_analysis_loaded(analysis):
            module = analysis.get("module")
        if module is None:
            def load_work(cancel, progress):
                if cancel.is_set():
                    raise TaskCancelled()
                progress(f"Cargando análisis: {name}...")
                loaded = load_analysis(analysis)
                if cancel.is_set():
                    raise TaskCancelled()
                return loaded

            def load_done(loaded):
                # La selección puede cambiar mientras trabaja el importador.
                if self.chosen_analysis is analysis:
                    self._run_analysis_with_frame(df_for_analysis, loaded)

            self._start_task(
                load_work, load_done, f"Cargando análisis: {name}...")
            return

        # ==========================================================
        # SELECCIÓN DE DIÁLOGO
        # ==========================================================
        custom = getattr(module, "CUSTOM_DIALOG", None)
        kwargs = {}

        # ---------- 1 · Regresión ----------
        if custom == "RegressionDialog":
            try:
                ds_name = (self.active_dataset_name
                        if self.active_dataset_name
                        else "dataset")
                dlg = RegressionDialog(self, df_for_analysis, ds_name)
                self.wait_window(dlg)
                if not self.winfo_exists():
                    return
                if dlg.result is None:
                    self._toast("Regresión cancelada.", "info", 1500)
                    return

                r = dlg.result
                df_for_analysis = r["df"]
                kwargs = {
                    "regression_type": r["regression_type"],
                    "date_col": r["date_col"],
                    "target_col": r["target_col"],
                    "input_cols": r["input_cols"],
                    "anomaly_cols": r["anomaly_cols"],
                }
            except Exception as e:
                self._toast(f"Error en diálogo: {e}", "error", 5000)
                self._log(traceback.format_exc())
                return

        # ---------- 2 · Causal Impact ---------- ← ANTES del fallback
        elif custom == "CausalImpactDialog":
            try:
                ds_name = (self.active_dataset_name
                        if self.active_dataset_name
                        else "dataset")
                dlg = CausalImpactDialog(self, df_for_analysis, ds_name)
                self.wait_window(dlg)
                if not self.winfo_exists():
                    return
                if dlg.result is None:
                    self._toast("Causal Impact cancelado.", "info", 1500)
                    return

                r = dlg.result
                df_for_analysis = r["df"]
                kwargs = {
                    "date_col": r["date_col"],
                    "kpi_col": r["kpi_col"],
                    "dim_cols": r["dim_cols"],
                    "target_tasks": r["target_tasks"],
                    "sesgos_cols": r["sesgos_cols"],
                    "controles_excluidos": r.get("controles_excluidos", []),
                    "fecha_campana": r["fecha_campana"],
                    "fecha_fin_datos": r["fecha_fin_datos"],
                    "granularidad": r["granularidad"],
                    "umbral_correlacion": r["umbral_correlacion"],
                    "min_controles": r["min_controles"],
                    "max_controles": r["max_controles"],
                    "max_combinaciones": r["max_combinaciones"],
                    "top_n_controles": r["top_n_controles"],
                    "alpha": r["alpha"],
                    "prior_level_sd": r["prior_level_sd"],
                    "dynamic_regression": r["dynamic_regression"],
                    "standardize_data": r["standardize_data"],
                }
            except Exception as e:
                self._toast(f"Error en diálogo: {e}", "error", 5000)
                self._log(traceback.format_exc())
                return

        # ---------- 3 · GeoX: configuración compacta de geos y diseño
        elif custom == "GeoXDialog":
            try:
                ds_name = self.active_dataset_name or "dataset"
                dlg = GeoXDialog(self, df_for_analysis, ds_name)
                self.wait_window(dlg)
                if not self.winfo_exists():
                    return
                if dlg.result is None:
                    self._toast("GeoX cancelado.", "info", 1500)
                    return
                kwargs = dict(dlg.result)
            except Exception as e:
                self._toast(f"Error en diálogo GeoX: {e}", "error", 5000)
                self._log(traceback.format_exc())
                return

        # ---------- 4 · Fallback genérico ---------- ← AL FINAL
        elif callable(getattr(module, "get_config_schema", None)):
            try:
                config_sample = RegressionDialog._sample_for_preview(
                    df_for_analysis)
                schema = module.get_config_schema(config_sample)
                dlg = AnalysisConfigDialog(self, schema, df=config_sample)
                self.wait_window(dlg)
                if not self.winfo_exists():
                    return
                if dlg.result is None:
                    self._toast("Análisis cancelado.", "info", 1500)
                    return
                kwargs = dlg.result
            except Exception as e:
                self._toast(f"Error al configurar: {e}", "error", 5000)
                self._log(f"[ERROR] get_config_schema: {e}")
                self._log(traceback.format_exc())
                return

        # ==========================================================
        # EJECUCIÓN
        # ==========================================================
        self._log(f"\n>>> Ejecutando: {name}")
        if kwargs:
            self._log(f"    Opciones: {kwargs}")

        def work(cancel, progress):
            return run_analysis(
                module, df_for_analysis, kwargs, type_snapshot, cancel, progress)

        def done(payload):
            result, out = payload
            result = _attach_analysis_context(result)
            self._last_analysis_name = name
            self.result = result
            self._figures_all = extract_figures(result)
            self.figures = list(self._figures_all)
            # ✅ Mapa figura → nombre predefinido
            self._figure_names = {}
            for f in self._figures_all:
                nm = getattr(f, "_mmm_name", None)
                if nm:
                    self._figure_names[id(f)] = nm
            self.plot_idx = 0

            # Guardar en historial si el plugin nos pasó la entrada
            try:
                hist_entry = None
                if isinstance(df_for_analysis, pd.DataFrame):
                    hist_entry = kwargs.pop("_history_entry", None) \
                        if isinstance(kwargs, dict) else None

                if isinstance(result, dict) and "_history_entry" in result:
                    hist_entry = result.pop("_history_entry")

                if hist_entry is None and isinstance(result, dict):
                    metrics = _regression_history_metrics(result)
                    r2, mape = 0.0, 0.0
                    if isinstance(metrics, pd.DataFrame):
                        try:
                            r2 = float(metrics.loc[
                                metrics["Métrica"] == "R²",
                                "Valor"].iloc[0])
                            mape = float(metrics.loc[
                                metrics["Métrica"] == "MAPE (%)",
                                "Valor"].iloc[0])
                        except Exception:
                            pass
                    hist_entry = {
                        "timestamp": pd.Timestamp.now().strftime(
                            "%Y-%m-%d %H:%M:%S"),
                        "regression_type": kwargs.get("regression_type", ""),
                        "target_col": kwargs.get("target_col", ""),
                        "date_col": kwargs.get("date_col", ""),
                        "input_cols": kwargs.get("input_cols", []),
                        "anomaly_cols": kwargs.get("anomaly_cols", []),
                        "r2": r2,
                        "mape": mape,
                    }
                else:
                    metrics = _regression_history_metrics(result)
                    if isinstance(metrics, pd.DataFrame) and hist_entry:
                        try:
                            hist_entry["r2"] = float(metrics.loc[
                                metrics["Métrica"] == "R²",
                                "Valor"].iloc[0])
                            hist_entry["mape"] = float(metrics.loc[
                                metrics["Métrica"] == "MAPE (%)",
                                "Valor"].iloc[0])
                        except Exception:
                            pass

                ds_name = (self.active_dataset_name
                        if self.active_dataset_name
                        else "dataset")
                append_regression_history(ds_name, hist_entry)
            except Exception as e:
                print(f"[WARN] No se pudo guardar historial: {e}")

            # Detectar canales en las figuras
            self._canales = []
            for f in self._figures_all:
                c = getattr(f, "_mmm_canal", None)
                if c and c not in self._canales:
                    self._canales.append(c)
            self._canales = sorted(self._canales)

            if self._canales:
                self.combo_canal["values"] = ["Todos"] + self._canales
                self.combo_canal.set("Todos")
                self._current_canal = "Todos"
                self.canal_bar.pack(fill="x", pady=(0, 6),
                                    before=self.canvas_frame)
                self.lbl_canal_info.config(
                    text=f"{len(self._canales)} canal(es) · "
                        f"{len(self._figures_all)} figura(s) en total")
            else:
                self.canal_bar.pack_forget()

            self._render_plots()
            self._refresh_results()

            if out:
                self._log(out)
            self._log(f"[OK] {len(self.figures)} gráfico(s).")

            try:
                if self.result and any(isinstance(v, pd.DataFrame)
                                        for v in self.result.values()):
                    self.notebook.select(self.tab_results)
                else:
                    self.notebook.select(self.tab_plots)
            except Exception:
                self.notebook.select(self.tab_plots)

            self._refresh_kpis()

            status = None
            if isinstance(result, dict):
                status = result.get("Estado") or result.get("Error")
            if status and str(status).upper().startswith("ERROR"):
                self._toast(f"{name} · {status}", "error", 6000)
            else:
                self._toast(f"{name} · {len(self.figures)} gráfico(s)",
                            "success")

        self._start_task(work, done, f"Análisis: {name}")

    # ==============================================================
    # CARRUSEL Y ZOOM
    # ==============================================================
    def on_prev_plot(self):
        if not self.figures:
            return
        self.plot_idx = (self.plot_idx - 1) % len(self.figures)
        self._render_plots()

    def on_next_plot(self):
        if not self.figures:
            return
        self.plot_idx = (self.plot_idx + 1) % len(self.figures)
        self._render_plots()

    def _render_plots(self):
        # Guardia: si canvas_frame no existe, no hacemos nada
        if not hasattr(self, "canvas_frame"):
            return

        for widget in self.canvas_frame.winfo_children():
            widget.destroy()

        n = len(self.figures)
        if n == 0:
            self.lbl_plot_counter.config(text="Sin gráficos")
            tk.Label(self.canvas_frame,
                    text="Ejecuta un análisis de la categoría 'Gráficos'.",
                    bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                    font=FONTS["body"]).pack(expand=True)
            self.canvas = None
            self.toolbar = None
            return

        if self.plot_idx >= n:
            self.plot_idx = 0

        self.lbl_plot_counter.config(
            text=f"Gráfico {self.plot_idx + 1} de {n}")

        fig = self.figures[self.plot_idx]
        style_matplotlib(fig)

        canvas = FigureCanvasTkAgg(fig, master=self.canvas_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        toolbar = NavigationToolbar2Tk(canvas, self.canvas_frame)
        toolbar.update()

        self._bind_zoom(canvas)
        self.canvas = canvas
        self.toolbar = toolbar
    def _bind_zoom(self, canvas):
        canvas.mpl_connect("scroll_event", self._on_scroll)
        canvas.get_tk_widget().bind("<Button-4>", self._on_scroll_linux)
        canvas.get_tk_widget().bind("<Button-5>", self._on_scroll_linux)
        canvas.mpl_connect("button_press_event", self._on_double_click_reset)

    def _on_scroll(self, event):
        if event.inaxes is None:
            return
        step = 1 if event.step > 0 else -1
        self._apply_zoom(event.inaxes, event.xdata, event.ydata, step)
        event.canvas.draw_idle()

    def _on_scroll_linux(self, event):
        step = 1 if event.num == 4 else -1
        x_pix, y_pix = event.x, event.y
        for ax in self.canvas.figure.get_axes():
            bbox = ax.get_window_extent()
            if (bbox.x0 <= x_pix <= bbox.x1
                    and bbox.y0 <= y_pix <= bbox.y1):
                x_data, y_data = ax.transData.inverted().transform((x_pix, y_pix))
                self._apply_zoom(ax, x_data, y_data, step)
                self.canvas.draw_idle()
                break

    @staticmethod
    def _apply_zoom(ax, x, y, step, factor=1.2):
        scale = 1.0 / factor if step > 0 else factor
        xlim = ax.get_xlim()
        ax.set_xlim(x - (x - xlim[0]) * scale, x + (xlim[1] - x) * scale)
        ylim = ax.get_ylim()
        ax.set_ylim(y - (y - ylim[0]) * scale, y + (ylim[1] - y) * scale)

    def _on_double_click_reset(self, event):
        if event.dblclick and event.inaxes is not None:
            event.inaxes.autoscale()
            event.canvas.draw_idle()

    def _clear_plots(self):
        self.figures = []
        self._figures_all = []
        self._canales = []
        self._current_canal = "Todos"
        self.plot_idx = 0
        try:
            self.canal_bar.pack_forget()
        except Exception:
            pass
        if hasattr(self, "canvas_frame"):
            self._render_plots()
        self._refresh_kpis()
    # ==============================================================
    # CONSOLA Y SESIÓN
    # ==============================================================
    def on_clear_console(self):
        self.console_buffer = io.StringIO()
        self._refresh_console()
        self._toast("Consola limpia", "info", 1500)

    def on_optimize_csv(self):
        payload = self.loaded_datasets.get(self.active_dataset_name)
        if not isinstance(payload, LazyLoaded):
            self._toast("Selecciona un CSV grande cargado en modo lazy.",
                        "info", 3500)
            return
        source = payload.dataset
        if source.backend != "duckdb_csv":
            self._toast("El dataset ya usa Parquet.", "info", 2500)
            return

        def work(cancel, progress):
            return source.cache_as_parquet(cancel, progress)

        def done(cache):
            self._log(f"[OK] CSV optimizado como Parquet: {cache}")
            self._toast("CSV optimizado; se reutilizará al cargarlo de nuevo.",
                        "success", 5000)

        self._start_task(work, done, "Convirtiendo CSV a Parquet...")

    def on_clear_cache(self):
        size_before = 0.0

        def sized(size):
            nonlocal size_before
            size_before = size
            if size_before < 0.1:
                self._toast("La caché ya está vacía.", "info", 1500)
                return
            if not messagebox.askyesno(
                    "Limpiar caché",
                    f"Se borrarán {size_before:.1f} MB de Parquet "
                    f"cacheados.\n\nLos archivos originales no se tocan.", parent=self):
                return

            protected = [item.dataset.source
                         for item in self.loaded_datasets.values()
                         if isinstance(item, LazyLoaded)
                         and item.dataset.origin is not None]
            active = self.session.active_dataset
            if active is not None and isinstance(active.source, Path):
                protected.append(active.source)

            def clear(cancel, progress):
                if cancel.is_set():
                    raise TaskCancelled()
                return db_engine.clear_cache(protected)

            self._start_task(clear, cleared, "Limpiando caché...")

        def cleared(n):
            self._toast(f"Caché limpiada · {n} archivos borrados",
                        "success", 2500)
            self._log(f"[OK] Caché Parquet vaciada ({size_before:.1f} MB)")

        def work(cancel, progress):
            return db_engine.cache_size_mb()

        self._start_task(work, sized, "Comprobando caché...")

    def on_save_console(self):
        out_dir = writable_root() / "output"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / "console_log.txt"
        with atomic_output(path) as temporary:
            temporary.write_text(self.console_buffer.getvalue(), encoding="utf-8")
        self._log(f"[OK] Consola guardada en {path}")
        self._toast(f"Consola guardada en {path.name}", "success")

    def on_save_plot(self):
        """Guarda el gráfico actual en una carpeta de análisis fechada."""
        if not self.figures:
            self._toast("No hay gráfico que guardar", "warning")
            return
        self._save_analysis_outputs(
            include_tables=False, figures=(self.figures[self.plot_idx],))
    
    def on_save_all_plots(self):
        """Guarda todas las figuras del análisis en una carpeta fechada."""
        if not self.figures:
            self._toast("No hay gráficos que guardar", "warning")
            return
        self._save_analysis_outputs(
            include_tables=False, figures=tuple(self._figures_all or self.figures))

    def _log(self, text: str):
        if not text:
            return
        if not text.endswith("\n"):
            text += "\n"
        current = self.console_buffer.getvalue()
        # The console is a recent-history view; cap it to avoid unbounded RAM.
        self.console_buffer = io.StringIO()
        self.console_buffer.write((current + text)[-100_000:])
        self._refresh_console()

    def _refresh_console(self):
        if not hasattr(self, "console_text"):
            return
        self.console_text.config(state="normal")
        self.console_text.delete("1.0", "end")
        self.console_text.insert("1.0", self.console_buffer.getvalue())
        self.console_text.see("end")
        self.console_text.config(state="disabled")

    # ==============================================================
    # ANÁLISIS (chips)
    # ==============================================================
    def _refresh_analyses(self):
        """Rellena el combobox agrupado por categoría."""
        self._analysis_labels = []       # texto mostrado
        self._analysis_map = {}          # texto → análisis

        if not self.analyses:
            self.analysis_combo["values"] = ["(sin análisis)"]
            self.analysis_combo.current(0)
            self.chosen_analysis = None
            self.lbl_analysis_cat.config(text="—")
            self.lbl_analysis_desc.config(text="")
            return

        # Agrupa por categoría respetando orden
        by_cat = {}
        for a in self.analyses:
            by_cat.setdefault(a["category"], []).append(a)

        labels = []
        for cat, items in by_cat.items():
            for a in items:
                label = f"[{cat}]  {a['name']}"
                labels.append(label)
                self._analysis_map[label] = a

        self._analysis_labels = labels
        self.analysis_combo["values"] = labels

        # Selecciona el primero por defecto
        if labels:
            self.analysis_combo.current(0)
            self._on_analysis_combo_change()

    def _on_analysis_combo_change(self, _event=None):
        """Actualiza el análisis seleccionado al cambiar el combobox."""
        idx = self.analysis_combo.current()
        if idx < 0 or idx >= len(self._analysis_labels):
            return

        label = self._analysis_labels[idx]
        chosen = self._analysis_map.get(label)
        if not chosen:
            return

        self.chosen_analysis = chosen
        self.lbl_analysis_cat.config(text=chosen["category"].upper())
        desc = chosen.get("description", "") or "(sin descripción)"
        self.lbl_analysis_desc.config(text=desc)

    def free_all_memory(self, hard=False):
        """Liberación completa. hard=True → resetea DuckDB."""
        import gc
        self.free_app_caches()

        if hard:
            try:
                db_engine.reset_conn()
            except Exception as e:
                self._log(f"[free] DuckDB reset: {e}")
            for _ in range(2):
                gc.collect()

        self._log(f"[OK] Liberación "
                f"{'completa' if hard else 'ligera'} completada")
# ==================================================================
# DIÁLOGO · EXPORTAR VISTA PREVIA
# ==================================================================
# ==================================================================
# DIÁLOGO DE REGRESIÓN (v2 · con pestañas, pre-flight y persistencia)
# ==================================================================

# ==================================================================
# MAIN
# ==================================================================
def main():
    try:
        app = MMMApp()
        app.mainloop()
    except Exception:
        traceback.print_exc()
        try:
            input("Enter para cerrar...")
        except Exception:
            pass


if __name__ == "__main__":
    main()
