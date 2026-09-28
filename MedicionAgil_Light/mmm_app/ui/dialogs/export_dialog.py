"""Export dialog extracted from the desktop application."""

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd

from core import engine as db_engine
from core.atomic import atomic_output
from core.exporter import export_dataframe
from theme import COLORS, FONTS
from ui.window_position import center_popup

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class ExportDialog(tk.Toplevel):
    """
    Diálogo para exportar el dataset activo o la vista filtrada.

    Formatos soportados:
      - CSV     → sin límite (pero >50M filas puede tardar mucho)
      - TSV     → igual que CSV pero con tabulador
      - XLSX    → límite Excel: 1.048.576 filas × 16.384 columnas
      - Parquet → sin límite, formato columnar comprimido
    """

    # Límites conocidos
    XLSX_MAX_ROWS = 1_048_576
    XLSX_MAX_COLS = 16_384
    SHEETS_MAX_CELLS = 10_000_000

    def __init__(self, parent, df_full: pd.DataFrame,
                 df_view: pd.DataFrame | None = None):
        super().__init__(parent)
        self.title("Exportar vista previa")
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()
        # Interceptar el cierre con la X para evitar cortar una exportación
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)      

        self.df_full = df_full
        self.df_view = df_view if df_view is not None else df_full
        self.result = None

        # Variables de compresión
        self.var_csv_comp = tk.StringVar(value="none")
        self.var_parquet_comp = tk.StringVar(value="zstd")

        # Estado de exportación en background
        self._export_thread = None
        self._export_result = None
        self._export_error = None
        self._export_start_time = None
        self._export_poll_id = None

        w, h = 720, 640
        center_popup(self, parent, w, h)
        self.minsize(560, 500)

        self._build_ui()

    def _build_ui(self):
        # --- Header ---
        header = tk.Frame(self, bg=COLORS["bg_card"])
        header.pack(fill="x")
        tk.Frame(header, bg=COLORS["primary"], height=2).pack(fill="x")
        tk.Label(header, text="💾  Exportar datos",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(anchor="w", padx=20, pady=12)

        # --- Cuerpo ---
        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        # --- Qué exportar ---
        tk.Label(body, text="Qué exportar",
                 bg=COLORS["bg"], fg=COLORS["primary"],
                 font=FONTS["kpi_lbl"]).pack(anchor="w", pady=(6, 4))

        scope_card = tk.Frame(body, bg=COLORS["bg_card"],
                               highlightbackground=COLORS["border"],
                               highlightthickness=1)
        scope_card.pack(fill="x", pady=(0, 10))
        scope_inner = tk.Frame(scope_card, bg=COLORS["bg_card"])
        scope_inner.pack(fill="x", padx=12, pady=10)

        self.var_scope = tk.StringVar(value="view")

        def _fmt(n):
            return f"{n:,}".replace(",", ".")

        n_view = len(self.df_view)
        n_full = len(self.df_full)

        ttk.Radiobutton(
            scope_inner,
            text=f"Vista filtrada actual ({_fmt(n_view)} filas)",
            variable=self.var_scope,
            value="view",
            command=self._on_scope_change,
        ).pack(anchor="w")
        ttk.Radiobutton(
            scope_inner,
            text=f"Dataset completo ({_fmt(n_full)} filas)",
            variable=self.var_scope,
            value="full",
            command=self._on_scope_change,
        ).pack(anchor="w")

        # --- Formato ---
        tk.Label(body, text="Formato",
                 bg=COLORS["bg"], fg=COLORS["primary"],
                 font=FONTS["kpi_lbl"]).pack(anchor="w", pady=(6, 4))

        fmt_card = tk.Frame(body, bg=COLORS["bg_card"],
                             highlightbackground=COLORS["border"],
                             highlightthickness=1)
        fmt_card.pack(fill="x", pady=(0, 10))
        fmt_inner = tk.Frame(fmt_card, bg=COLORS["bg_card"])
        fmt_inner.pack(fill="x", padx=12, pady=10)

        self.var_fmt = tk.StringVar(value="csv")

        formatos = [
            ("CSV (.csv)", "csv",
             "Universal · sin límite de filas · archivo plano"),
            ("TSV (.tsv)", "tsv",
             "Como CSV pero con tabulador · ideal para pegados"),
            ("Excel (.xlsx)", "xlsx",
             f"Límite: {_fmt(self.XLSX_MAX_ROWS)} filas × "
             f"{_fmt(self.XLSX_MAX_COLS)} columnas"),
            ("Parquet (.parquet)", "parquet",
             "Columnar · comprimido · ideal para datasets grandes"),
        ]
        for label, val, hint in formatos:
            row = tk.Frame(fmt_inner, bg=COLORS["bg_card"])
            row.pack(fill="x", pady=1)
            ttk.Radiobutton(
                row, text=label, variable=self.var_fmt,
                value=val, command=self._on_fmt_change,
            ).pack(side="left")
            tk.Label(row, text=hint, bg=COLORS["bg_card"],
                     fg=COLORS["text_dim"], font=FONTS["tiny"]).pack(
                side="left", padx=(8, 0))

        # --- Panel de compresión dinámico ---
        self.comp_frame = tk.Frame(body, bg=COLORS["bg_card"],
                                    highlightbackground=COLORS["border"],
                                    highlightthickness=1)
        self.comp_frame.pack(fill="x", pady=(6, 10))
        self._render_compression_options()

        # --- Aviso dinámico ---
        self.lbl_warn = tk.Label(
            body, text="", bg=COLORS["bg"], fg=COLORS["warning"],
            font=FONTS["small"], wraplength=560, justify="left")
        self.lbl_warn.pack(anchor="w", pady=(0, 8))

        # --- Nombre archivo ---
        name_row = tk.Frame(body, bg=COLORS["bg"])
        name_row.pack(fill="x", pady=(6, 0))
        tk.Label(name_row, text="Nombre del archivo:",
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="left")
        self.var_name = tk.StringVar(value="vista_previa")
        ttk.Entry(name_row, textvariable=self.var_name,
                  width=40).pack(side="left", padx=(6, 0), fill="x",
                                  expand=True)

        # --- Footer ---
        footer = tk.Frame(self, bg=COLORS["bg_card"])
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=COLORS["border"], height=1).pack(fill="x")
        fr = tk.Frame(footer, bg=COLORS["bg_card"])
        fr.pack(fill="x", padx=20, pady=12)

        self.btn_cancel = ttk.Button(fr, text="Cancelar",
                                       command=self._on_cancel)
        self.btn_cancel.pack(side="right", padx=(6, 0))

        self.btn_export = ttk.Button(
            fr, text="✓  Exportar",
            style="Primary.TButton",
            command=self._on_export)
        self.btn_export.pack(side="right")

        # --- Panel de progreso (oculto por defecto) ---
        self.progress_panel = tk.Frame(self, bg=COLORS["bg_card"],
                                        highlightbackground=COLORS["primary"],
                                        highlightthickness=1)
        self.progress_panel.pack(fill="x", side="bottom",
                                   padx=20, pady=(0, 10))
        pi = tk.Frame(self.progress_panel, bg=COLORS["bg_card"])
        pi.pack(fill="x", padx=14, pady=10)

        # Fila superior: título + porcentaje gigante
        row_top = tk.Frame(pi, bg=COLORS["bg_card"])
        row_top.pack(fill="x")

        self.lbl_progress = tk.Label(
            row_top, text="", bg=COLORS["bg_card"], fg=COLORS["text"],
            font=FONTS["h3"], anchor="w")
        self.lbl_progress.pack(side="left", fill="x", expand=True)

        self.lbl_progress_pct = tk.Label(
            row_top, text="0%", bg=COLORS["bg_card"],
            fg=COLORS["primary"], font=("Segoe UI", 22, "bold"),
            anchor="e")
        self.lbl_progress_pct.pack(side="right")

        self.lbl_progress_detail = tk.Label(
            pi, text="", bg=COLORS["bg_card"], fg=COLORS["text_muted"],
            font=FONTS["small"], anchor="w")
        self.lbl_progress_detail.pack(fill="x", pady=(2, 8))

        self.progress_bar = ttk.Progressbar(
            pi, style="Export.Horizontal.TProgressbar",
            mode="determinate", maximum=100.0, value=0.0)
        self.progress_bar.pack(fill="x", pady=(4, 0))

        # Ocultar el panel hasta que se exporte
        self.progress_panel.pack_forget()

        self._check_limits()

    

    def _render_compression_options(self):
        """Redibuja las opciones de compresión en vertical (sin cortes)."""
        for w in self.comp_frame.winfo_children():
            w.destroy()

        fmt = self.var_fmt.get()
        inner = tk.Frame(self.comp_frame, bg=COLORS["bg_card"])
        inner.pack(fill="x", padx=12, pady=10)

        tk.Label(inner, text="Compresión:",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h3"]).pack(anchor="w", pady=(0, 6))

        if fmt == "csv":
            opts = [
                ("Sin comprimir · máximo velocidad", "none"),
                ("GZIP · reduce ~60% disco · más lento", "gzip"),
                ("ZSTD · reduce ~50% disco · equilibrado", "zstd"),
            ]
            var = self.var_csv_comp

        elif fmt == "parquet":
            opts = [
                ("ZSTD · rápido, reduce ~50%", "zstd"),
                ("Snappy · más rápido, reduce ~30%", "snappy"),
                ("GZIP · más lento, reduce ~60%", "gzip"),
                ("Sin comprimir", "none"),
            ]
            var = self.var_parquet_comp

        elif fmt == "xlsx":
            tk.Label(inner,
                     text="Excel gestiona su propia compresión interna.",
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=FONTS["small"]).pack(anchor="w")
            return

        else:  # tsv
            tk.Label(inner,
                     text="TSV no tiene opciones de compresión.",
                     bg=COLORS["bg_card"], fg=COLORS["text_dim"],
                     font=FONTS["small"]).pack(anchor="w")
            return

        # Renderizar en vertical (nada se corta)
        for label, val in opts:
            ttk.Radiobutton(inner, text=label,
                             variable=var, value=val,
                             command=self._check_limits).pack(
                anchor="w", pady=1)
    def _selected_df(self) -> pd.DataFrame:
        if self.var_scope.get() == "full":
            return self.df_full
        return self.df_view

    def _on_scope_change(self):
        self._check_limits()

    def _on_fmt_change(self):
        self._render_compression_options()
        self._check_limits()

    def _check_limits(self):
        """
        Valida límites según formato y tamaño. Si es imposible,
        bloquea el botón Exportar y muestra un aviso rojo.
        """
        df = self._selected_df()
        n_rows = len(df)
        n_cols = df.shape[1]
        fmt = self.var_fmt.get()

        def _fmt_num(n):
            return f"{n:,}".replace(",", ".")

        errors = []
        warns = []

        # --- XLSX ---
        if fmt == "xlsx":
            if n_rows > self.XLSX_MAX_ROWS:
                errors.append(
                    f"Excel admite máximo "
                    f"{_fmt_num(self.XLSX_MAX_ROWS)} filas y "
                    f"estás intentando exportar "
                    f"{_fmt_num(n_rows)}.\n"
                    f"Usa CSV o Parquet en su lugar."
                )
            if n_cols > self.XLSX_MAX_COLS:
                errors.append(
                    f"Excel admite máximo "
                    f"{_fmt_num(self.XLSX_MAX_COLS)} columnas y "
                    f"estás intentando exportar "
                    f"{_fmt_num(n_cols)}."
                )

            # Aviso Google Sheets
            cells = n_rows * n_cols
            if cells > self.SHEETS_MAX_CELLS:
                warns.append(
                    f"⚠ Si lo vas a subir a Google Sheets: "
                    f"supera el límite de "
                    f"{_fmt_num(self.SHEETS_MAX_CELLS)} celdas "
                    f"({_fmt_num(cells)} celdas)."
                )

        # --- CSV/TSV grande ---
        elif fmt in ("csv", "tsv"):
            if n_rows > 50_000_000:
                warns.append(
                    f"⚠ {_fmt_num(n_rows)} filas en CSV pueden tardar "
                    f"varios minutos y ocupar mucho disco."
                )

        # --- Parquet informativo ---
        elif fmt == "parquet":
            if n_rows > 100_000_000:
                warns.append(
                    f"⚠ {_fmt_num(n_rows)} filas en Parquet: "
                    f"la escritura puede tardar 30-60 s."
                )

        # --- Estado UI ---
        if errors:
            self.lbl_warn.config(
                text="❌  " + "\n❌  ".join(errors),
                fg=COLORS["danger"])
            self.btn_export.state(["disabled"])
        elif warns:
            self.lbl_warn.config(
                text="\n".join(warns),
                fg=COLORS["warning"])
            self.btn_export.state(["!disabled"])
        else:
            self.lbl_warn.config(
                text=f"✓ Listo · {_fmt_num(n_rows)} filas × "
                     f"{n_cols} columnas",
                fg=COLORS["success"])
            self.btn_export.state(["!disabled"])



    def _on_export(self):
        """
        Exportación por CHUNKS: escribe el archivo por bloques y entre
        bloques llama a update() para que la barra se anime y la app
        no se congele.
        """
        df = self._selected_df()
        fmt = self.var_fmt.get()
        base_name = self.var_name.get().strip() or "vista_previa"

        ext_map = {
            "csv": (".csv", [("CSV", "*.csv")]),
            "tsv": (".tsv", [("TSV", "*.tsv"), ("Texto", "*.txt")]),
            "xlsx": (".xlsx", [("Excel", "*.xlsx")]),
            "parquet": (".parquet", [("Parquet", "*.parquet")]),
        }
        ext, ftypes = ext_map[fmt]

        out_dir = PROJECT_ROOT / "output"
        out_dir.mkdir(exist_ok=True)

        path = filedialog.asksaveasfilename(
            parent=self, initialdir=str(out_dir),
            initialfile=base_name + ext,
            defaultextension=ext,
            filetypes=ftypes,
        )
        if not path:
            return

        # Verificación de límite Excel antes de nada
        if fmt == "xlsx" and len(df) > self.XLSX_MAX_ROWS:
            messagebox.showerror(
                "Límite de Excel",
                f"El DataFrame tiene {len(df):,} filas.\n"
                f"Excel admite máximo {self.XLSX_MAX_ROWS:,}.\n\n"
                f"Exporta a CSV o Parquet.".replace(",", "."), parent=self)
            return

        csv_comp = self.var_csv_comp.get()
        parquet_comp = self.var_parquet_comp.get()

        # Bloquear botones y mostrar panel
        self.btn_export.state(["disabled"])
        self._show_progress_panel(fmt, len(df))
        self._export_active = True

        def work(cancel, progress):
            return export_dataframe(
                df, path, fmt, csv_compression=csv_comp,
                parquet_compression=parquet_comp,
                cancel=cancel, progress=progress)

        def done(result):
            self._export_active = False
            self.result = result
            self._hide_progress_panel()
            self.destroy()

        def failed(exc):
            self._export_active = False
            self._hide_progress_panel()
            self.btn_export.state(["!disabled"])
            self.btn_cancel.state(["!disabled"])
            messagebox.showerror("Error al exportar", str(exc), parent=self)

        def cancelled():
            self._export_active = False
            self._hide_progress_panel()
            self.btn_export.state(["!disabled"])
            self.btn_cancel.state(["!disabled"])

        if not self.master.tasks.start(
                work, on_result=done, on_error=failed,
                on_progress=lambda update: self._update_progress(*update),
                on_cancel=cancelled):
            failed(RuntimeError("Ya hay una tarea en curso"))

    # =============================================================
    # ESCRITORES POR CHUNKS
    # =============================================================
    def _write_csv_chunked(self, df, path, compression="none",
                            chunksize=200_000):
        with atomic_output(path) as temporary:
            self._write_csv_chunked_direct(df, temporary, compression,
                                           chunksize)

    def _write_csv_chunked_direct(self, df, path, compression="none",
                                  chunksize=200_000):
        """
        Escribe CSV por bloques. La barra se actualiza entre bloques
        porque llamamos a update() y update_idletasks().
        """
        import gzip as _gzip

        n = len(df)
        if n == 0:
            Path(path).write_text("")
            self._update_progress(1.0)
            return

        # Elige el writer según compresión
        if compression == "gzip":
            f = _gzip.open(path, "wt", encoding="utf-8-sig", newline="")
            close_fn = f.close
        else:
            f = open(path, "w", encoding="utf-8-sig", newline="")
            close_fn = f.close

        try:
            # Cabecera
            df.head(0).to_csv(f, index=False)

            total_chunks = (n + chunksize - 1) // chunksize
            for i in range(0, n, chunksize):
                chunk = df.iloc[i:i + chunksize]
                chunk.to_csv(f, index=False, header=False)

                done = min(i + chunksize, n)
                prog = done / n
                self._update_progress(
                    prog,
                    f"Escrito {done:,} de {n:,} filas"
                    .replace(",", "."),
                )
        finally:
            close_fn()

        self._update_progress(
            1.0, f"CSV escrito · {n:,} filas".replace(",", "."))

    def _write_tsv_chunked(self, df, path, chunksize=200_000):
        with atomic_output(path) as temporary:
            self._write_tsv_chunked_direct(df, temporary, chunksize)

    def _write_tsv_chunked_direct(self, df, path, chunksize=200_000):
        """Igual que CSV pero con tabulador."""
        n = len(df)
        if n == 0:
            Path(path).write_text("")
            self._update_progress(1.0)
            return

        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            df.head(0).to_csv(f, index=False, sep="\t")

            for i in range(0, n, chunksize):
                chunk = df.iloc[i:i + chunksize]
                chunk.to_csv(f, index=False, header=False, sep="\t")

                done = min(i + chunksize, n)
                prog = done / n
                self._update_progress(
                    prog,
                    f"Escrito {done:,} de {n:,} filas"
                    .replace(",", "."),
                )

        self._update_progress(1.0, f"TSV escrito · {n:,} filas")

    def _write_parquet(self, df, path, compression="zstd"):
        with atomic_output(path) as temporary:
            self._write_parquet_direct(df, temporary, compression)

    def _write_parquet_direct(self, df, path, compression="zstd"):
        """
        Parquet: no troceamos porque DuckDB lo hace rápido y no se
        puede partir un único fichero en escrituras parciales.
        Mostramos progreso simulando avance por etapas.
        """
        self._update_progress(
            0.1, "Preparando tabla en memoria...")

        try:
            db_engine.write_parquet_fast(
                df, path, compression=compression,
                log=lambda m: print(m, flush=True))
        except Exception as e:
            self._update_progress(0.5, f"Fallback a pandas: {e}")
            df.to_parquet(path, index=False, compression=compression,
                           engine="pyarrow")

        self._update_progress(
            1.0, f"Parquet escrito · {len(df):,} filas"
            .replace(",", "."))

    def _write_xlsx(self, df, path):
        with atomic_output(path) as temporary:
            self._write_xlsx_direct(df, temporary)

    def _write_xlsx_direct(self, df, path):
        """Excel: sin chunks (openpyxl no lo permite), pero avisamos."""
        self._update_progress(
            0.1, "Excel está escribiendo el archivo "
                 "(puede tardar varios minutos)...")
        try:
            self.update_idletasks()
            self.update()
        except Exception:
            pass

        df.to_excel(path, index=False, engine="openpyxl")

        self._update_progress(
            1.0, f"Excel escrito · {len(df):,} filas".replace(",", "."))

    # =============================================================
    # GESTIÓN DEL PANEL DE PROGRESO
    # =============================================================
    def _show_progress_panel(self, fmt: str, n_rows: int):
        """Muestra el panel con barra de progreso determinada."""
        fmt_names = {
            "csv": "CSV",
            "tsv": "TSV",
            "xlsx": "Excel",
            "parquet": "Parquet",
        }
        txt_n = f"{n_rows:,}".replace(",", ".")

        self.lbl_progress.config(
            text=f"Exportando {txt_n} filas a {fmt_names.get(fmt, fmt)}...")
        self.lbl_progress_detail.config(text="Preparando...")

        # Reset de la barra a 0% (modo determinado)
        try:
            self.progress_bar.stop()
        except Exception:
            pass

        try:
            self.progress_bar.configure(
                mode="determinate", maximum=100.0, value=0.0)
        except Exception:
            pass

        try:
            self.lbl_progress_pct.config(text="0%")
        except Exception:
            pass

        # Mostrar el panel
        self.progress_panel.pack(fill="x", side="bottom",
                                 padx=20, pady=(0, 10))

        # Forzar repintado inicial
        try:
            self.update_idletasks()
            self.update()
        except Exception:
            pass

    def _update_progress(self, fraction: float, detail: str = ""):
        """
        Actualiza la barra, el porcentaje y el detalle.
        Fuerza a Tkinter a repintar para que se vea el avance.
        """
        try:
            pct = max(0.0, min(1.0, float(fraction))) * 100.0
            self.progress_bar.configure(value=pct)
            self.lbl_progress_pct.config(text=f"{pct:.0f}%")
            if detail:
                self.lbl_progress_detail.config(text=detail)

            self.update_idletasks()
            self.update()
        except Exception:
            pass

    def _hide_progress_panel(self):
        """Oculta el panel y detiene la animación."""
        try:
            self.progress_bar.stop()
        except Exception:
            pass
        try:
            self.progress_panel.pack_forget()
        except Exception:
            pass

    def _on_cancel(self):
        if getattr(self, "_export_active", False):
            self.master.tasks.cancel()
            self.btn_cancel.state(["disabled"])
            return
        self.result = None
        self.destroy()

    def _log_export(self, msg: str):
        """Callback que usa el engine para loguear progreso."""
        try:
            print(msg, flush=True)
        except Exception:
            pass



