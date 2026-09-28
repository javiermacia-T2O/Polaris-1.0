"""Configuración compacta para diseñar geo-experimentos con Meridian GeoX."""

import math
import tkinter as tk
from tkinter import ttk

import pandas as pd

from theme import COLORS, FONTS
from ui.window_position import center_popup


_DATE_HINTS = ("fecha", "date", "dia", "day", "week", "semana")
_REGION_HINTS = ("region", "región", "location", "geo", "provincia",
                 "state", "ciudad", "city", "municipio", "dma", "zona")
_KPI_HINTS = ("sessions", "conversion", "ventas", "sales", "revenue",
              "gmv", "kpi", "leads", "checkouts")
_MARKET_HINTS = ("country", "pais", "país", "market", "mercado")


def geox_column_options(df: pd.DataFrame) -> dict:
    """Devuelve opciones y valores iniciales sin modificar el dataframe."""
    names = [str(column) for column in df.columns]

    def matching(hints, candidates=None):
        candidates = candidates or names
        return [name for name in candidates
                if any(hint in name.lower() for hint in hints)]

    date_cols = [str(column) for column in df.columns
                 if pd.api.types.is_datetime64_any_dtype(df[column])]
    date_cols = date_cols or matching(_DATE_HINTS)
    region_cols = matching(_REGION_HINTS)
    numeric = [str(column) for column in df.select_dtypes("number").columns]
    kpi_cols = matching(_KPI_HINTS, numeric) or numeric
    market_cols = matching(_MARKET_HINTS)
    return {
        "date": date_cols or names,
        "region": region_cols or names,
        "kpi": kpi_cols or names,
        "market": market_cols,
    }


def clean_geo_values(series: pd.Series, limit: int = 5000) -> list[str]:
    """Valores de región estables para el selector, sin nulos ni duplicados."""
    values = (series.dropna().astype(str).str.strip())
    values = values[(values != "") & (~values.str.lower().isin(
        {"nan", "none", "(not set)", "unknown"}))]
    return sorted(values.drop_duplicates().head(limit).tolist(), key=str.casefold)


class GeoXDialog(tk.Toplevel):
    """Diálogo de GeoX orientado al diseño y al emparejamiento de geos.

    GeoX permite fijar geos de control y excluir geos del diseño. El motor
    selecciona el tratamiento óptimo entre los geos restantes, por lo que la
    interfaz muestra ambos conjuntos sin sugerir una asignación manual que la
    librería no soporta.
    """

    def __init__(self, parent, df: pd.DataFrame, dataset_name: str = "dataset"):
        super().__init__(parent)
        self.title("GeoX · diseño de geo-experimento")
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()
        self.df = df
        self.dataset_name = dataset_name
        self.result = None
        self._controls: list[str] = []
        self._excluded: list[str] = []
        self._geo_options: list[str] = []

        center_popup(self, parent, 1120, 700)

        self._init_variables()
        self._build_ui()
        self._refresh_geo_options()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _event: self._on_cancel())

    def _init_variables(self):
        choices = geox_column_options(self.df)
        self.date_var = tk.StringVar(value=choices["date"][0] if choices["date"] else "")
        self.region_var = tk.StringVar(value=choices["region"][0] if choices["region"] else "")
        self.kpi_var = tk.StringVar(value=choices["kpi"][0] if choices["kpi"] else "")
        self.market_col_var = tk.StringVar(value="(ninguna)")
        self.market_value_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Elige los datos y fija controles solo si ya los conoces.")
        self._column_choices = choices
        self._numbers = {
            "duration_days": tk.StringVar(value="14"),
            "budget": tk.StringVar(value="50000"),
            "cost_per_incremental_conversion": tk.StringVar(value="1"),
            "max_conversions_percent": tk.StringVar(value="0.30"),
        }
        self.experiment_type_var = tk.StringVar(value="HOLDBACK")

    def _build_ui(self):
        header = tk.Frame(self, bg=COLORS["bg_card"])
        header.pack(fill="x")
        tk.Frame(header, bg=COLORS["purple"], height=3).pack(fill="x")
        row = tk.Frame(header, bg=COLORS["bg_card"])
        row.pack(fill="x", padx=20, pady=10)
        tk.Label(row, text="GeoX · diseño y emparejamiento geográfico",
                 bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["h2"]).pack(side="left")
        tk.Label(row, text="El motor propone el tratamiento y compara diseños.",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                 font=FONTS["small"]).pack(side="right")
        tk.Label(self, text=("PRE-test para planificar un experimento geográfico. "
                             "Requiere datos diarios reales y geos que puedan "
                             "aislarse del tratamiento."),
                 bg=COLORS["bg"], fg=COLORS["text_muted"],
                 font=FONTS["small"], anchor="w", justify="left").pack(
                     fill="x", padx=20, pady=(0, 2))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(10, 0))
        config = tk.Frame(self.notebook, bg=COLORS["bg"])
        self.notebook.add(config, text="  1. Datos y diseño  ")
        geos = tk.Frame(self.notebook, bg=COLORS["bg"])
        self.notebook.add(geos, text="  2. Controles geográficos  ")
        self._build_config(config)
        self._build_geos(geos)

        footer = tk.Frame(self, bg=COLORS["bg_card"])
        footer.pack(fill="x", side="bottom")
        tk.Frame(footer, bg=COLORS["border"], height=1).pack(fill="x")
        row = tk.Frame(footer, bg=COLORS["bg_card"])
        row.pack(fill="x", padx=20, pady=10)
        tk.Label(row, textvariable=self.status_var, bg=COLORS["bg_card"],
                 fg=COLORS["text_muted"], font=FONTS["small"],
                 anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Cancelar", command=self._on_cancel).pack(side="right", padx=(8, 0))
        ttk.Button(row, text="▶ Ejecutar análisis", style="Primary.TButton",
                   command=self._on_accept).pack(side="right")

    def _build_geos(self, parent):
        parent.columnconfigure(0, weight=1)
        matching = self._card(parent, "Regiones de control y exclusiones")
        matching.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        tk.Label(
            matching,
            text=("GeoX selecciona el tratamiento entre las regiones disponibles. "
                  "Puedes fijar controles conocidos o excluir regiones del diseño."),
            bg=COLORS["bg_card"], fg=COLORS["text_muted"], font=FONTS["small"],
            anchor="w", justify="left", wraplength=900,
        ).pack(fill="x", padx=12, pady=(8, 8))
        search_row = tk.Frame(matching, bg=COLORS["bg_card"])
        search_row.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(search_row, text="Buscar región", bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["small"]).pack(side="left")
        ttk.Entry(search_row, textvariable=self.search_var).pack(
            side="left", fill="x", expand=True, padx=(8, 0))
        self.search_var.trace_add("write", lambda *_: self._render_available_geos())
        lists = tk.Frame(matching, bg=COLORS["bg_card"])
        lists.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        for index in range(3):
            lists.columnconfigure(index, weight=1)
        self.available_box = self._geo_box(lists, "Regiones disponibles", 0)
        actions = tk.Frame(lists, bg=COLORS["bg_card"])
        actions.grid(row=0, column=1, sticky="nsew", padx=10)
        ttk.Button(actions, text="→ Fijar como control", command=self._add_control).pack(
            fill="x", pady=(38, 6))
        ttk.Button(actions, text="→ Excluir del diseño", command=self._add_excluded).pack(
            fill="x", pady=6)
        ttk.Button(actions, text="Quitar selección", command=self._remove_selected).pack(
            fill="x", pady=6)
        self.assigned_box = self._geo_box(lists, "Controles fijados / excluidas", 2)

    def _build_config(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        source = self._card(parent, "Datos del experimento")
        source.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        tk.Label(source, text=f"Dataset: {self.dataset_name}", bg=COLORS["bg_card"],
                 fg=COLORS["text_muted"], font=FONTS["small"], anchor="w").pack(
                     fill="x", pady=(0, 5))
        self._combo(source, "Fecha", self.date_var, self._column_choices["date"])
        self._combo(source, "Región / geo", self.region_var, self._column_choices["region"],
                    command=self._refresh_geo_options)
        self._combo(source, "KPI", self.kpi_var, self._column_choices["kpi"])
        self._combo(source, "Mercado", self.market_col_var,
                    ["(ninguna)"] + self._column_choices["market"])
        self._entry(source, "Valor de mercado (opcional)", self.market_value_var)

        design = self._card(parent, "Tratamiento y método")
        design.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)
        tk.Label(design, text="GeoX propone el grupo de tratamiento automáticamente.",
                 bg=COLORS["bg_card"], fg=COLORS["text_muted"], font=FONTS["small"],
                 anchor="w", wraplength=430, justify="left").pack(fill="x", pady=(0, 5))
        self._combo(design, "Tipo", self.experiment_type_var,
                    ["HOLDBACK", "GO_DARK", "HEAVY_UP"])
        periods = self._card(parent, "Periodos y presupuesto")
        periods.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        self._number_grid(periods, [
            ("Duración del experimento (días)", "duration_days"),
            ("Presupuesto por celda", "budget"),
            ("Coste por conversión incremental", "cost_per_incremental_conversion"),
            ("Máx. volumen de tratamiento", "max_conversions_percent"),
        ])

    def _card(self, parent, title):
        card = tk.LabelFrame(parent, text=f"  {title}  ", bg=COLORS["bg_card"], fg=COLORS["primary"],
                             font=FONTS["h3"], highlightbackground=COLORS["border"], highlightthickness=1,
                             borderwidth=0, padx=12, pady=8)
        return card

    def _combo(self, parent, label, variable, values, command=None):
        row = tk.Frame(parent, bg=COLORS["bg_card"])
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, width=23, anchor="w", bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["small"]).pack(side="left")
        combo = ttk.Combobox(row, textvariable=variable, values=values, state="readonly")
        combo.pack(side="left", fill="x", expand=True)
        if command:
            combo.bind("<<ComboboxSelected>>", lambda _event: command())

    def _entry(self, parent, label, variable):
        row = tk.Frame(parent, bg=COLORS["bg_card"])
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, width=23, anchor="w", bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["small"]).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)

    def _number_grid(self, parent, items):
        grid = tk.Frame(parent, bg=COLORS["bg_card"])
        grid.pack(fill="x", pady=(7, 2))
        for column in range(2):
            grid.columnconfigure(column, weight=1)
        for index, (label, key) in enumerate(items):
            cell = tk.Frame(grid, bg=COLORS["bg_card"])
            cell.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 10), pady=3)
            tk.Label(cell, text=label, bg=COLORS["bg_card"], fg=COLORS["text_muted"],
                     font=FONTS["tiny"], anchor="w").pack(fill="x")
            ttk.Entry(cell, textvariable=self._numbers[key], justify="right").pack(fill="x")

    def _geo_box(self, parent, title, column):
        frame = tk.Frame(parent, bg=COLORS["bg_card"])
        frame.grid(row=0, column=column, sticky="nsew")
        tk.Label(frame, text=title, bg=COLORS["bg_card"], fg=COLORS["text"],
                 font=FONTS["small"], anchor="w").pack(fill="x")
        box = tk.Listbox(frame, height=8, selectmode="extended", exportselection=False,
                         bg=COLORS["bg_input"], fg=COLORS["text"],
                         selectbackground=COLORS["primary_dark"], borderwidth=0,
                         highlightthickness=1, highlightbackground=COLORS["border"],
                         activestyle="none", font=FONTS["small"])
        box.pack(fill="both", expand=True, pady=(4, 0))
        return box

    def _refresh_geo_options(self):
        column = self.region_var.get()
        if column not in self.df.columns:
            self._geo_options = []
        else:
            self._geo_options = clean_geo_values(self.df[column])
        self._controls = [geo for geo in self._controls if geo in self._geo_options]
        self._excluded = [geo for geo in self._excluded if geo in self._geo_options]
        self._render_available_geos()
        self._render_assigned_geos()

    def _render_available_geos(self):
        if not hasattr(self, "available_box"):
            return
        query = self.search_var.get().strip().casefold()
        assigned = set(self._controls) | set(self._excluded)
        values = [geo for geo in self._geo_options
                  if geo not in assigned and (not query or query in geo.casefold())]
        self.available_box.delete(0, "end")
        for geo in values:
            self.available_box.insert("end", geo)

    def _render_assigned_geos(self):
        if not hasattr(self, "assigned_box"):
            return
        self.assigned_box.delete(0, "end")
        for geo in self._controls:
            self.assigned_box.insert("end", f"Control · {geo}")
        for geo in self._excluded:
            self.assigned_box.insert("end", f"Excluir · {geo}")
        self.status_var.set(
            f"{len(self._geo_options)} regiones disponibles · {len(self._controls)} control(es) fijo(s) · {len(self._excluded)} excluida(s)")

    def _selected_available(self):
        return [self.available_box.get(index) for index in self.available_box.curselection()]

    def _add_control(self):
        selected = self._selected_available()
        self._controls.extend(geo for geo in selected if geo not in self._controls)
        self._render_available_geos()
        self._render_assigned_geos()

    def _add_excluded(self):
        selected = self._selected_available()
        self._excluded.extend(geo for geo in selected if geo not in self._excluded)
        self._render_available_geos()
        self._render_assigned_geos()

    def _remove_selected(self):
        for index in reversed(self.assigned_box.curselection()):
            item = self.assigned_box.get(index)
            if item.startswith("Control · "):
                self._controls.remove(item.removeprefix("Control · "))
            elif item.startswith("Excluir · "):
                self._excluded.remove(item.removeprefix("Excluir · "))
        self._render_available_geos()
        self._render_assigned_geos()

    def _number_value(self, key):
        text = self._numbers[key].get().strip().replace(",", ".")
        if not text:
            return None
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("El valor debe ser finito.")
        return int(value) if value.is_integer() else value

    def _on_accept(self):
        required = {"Fecha": self.date_var.get(), "Región": self.region_var.get(), "KPI": self.kpi_var.get()}
        missing = [label for label, value in required.items() if not value or value not in self.df.columns]
        if missing:
            self.status_var.set("Selecciona: " + ", ".join(missing))
            return
        try:
            numbers = {key: self._number_value(key) for key in self._numbers}
        except ValueError:
            self.status_var.set("Revisa los parámetros numéricos.")
            self.notebook.select(0)
            return
        if numbers["duration_days"] is None or numbers["duration_days"] < 1:
            self.status_var.set("La duración debe ser de al menos un día.")
            return
        if int(numbers["duration_days"]) != numbers["duration_days"]:
            self.status_var.set("La duración debe expresarse en días enteros.")
            return
        if numbers["budget"] is None or numbers["budget"] < 0:
            self.status_var.set("El presupuesto no puede ser negativo.")
            return
        if (numbers["cost_per_incremental_conversion"] is None
                or numbers["cost_per_incremental_conversion"] <= 0):
            self.status_var.set("Indica un coste por conversión mayor que cero.")
            return
        if (numbers["max_conversions_percent"] is None
                or not 0 < numbers["max_conversions_percent"] <= 1):
            self.status_var.set("El máximo de tratamiento debe estar entre 0 y 1.")
            return
        if set(self._controls) & set(self._excluded):
            self.status_var.set("Una región no puede ser control fijo y estar excluida.")
            return
        self.result = {
            "date_col": self.date_var.get(), "region_col": self.region_var.get(),
            "kpi_col": self.kpi_var.get(), "market_col": self.market_col_var.get(),
            "market_value": self.market_value_var.get().strip(),
            "experiment_type": self.experiment_type_var.get(),
            "methodology": "TBR",
            "geo_assignment_rule": "STRATIFIED_SAMPLING",
            "test_type": "TWO_SIDED",
            "design_output_count": 5,
            "cell_count": 1,
            "included_control_geos": list(self._controls),
            "excluded_geos": list(self._excluded),
            **numbers,
        }
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()
