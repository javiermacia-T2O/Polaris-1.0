"""Análisis Inversión ↔ KPI por Canal · Fiel al Rmd Retargeters Display IBS.

Jerarquía: Fecha | Canal | Soporte | Inversión | KPI
Genera 6 gráficos por cada CANAL:
  - Canal · Acumulado / Saturación / Marginal
  - Soportes · Acumulado / Saturación / Marginal
"""

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from core.loader import get_date_columns, detect_date_column

NAME = "Inversión ↔ KPI por Canal (acum + sat + marginal)"
DESCRIPTION = "6 gráficos por canal: acumulado, saturación y marginal"
CATEGORY = "Gráficos"

_INV_KEYS = ("inversión", "inversion", "spend", "coste", "costo",
             "gasto", "cost", "investment")
_CANAL_KEYS = ("canal", "channel")
_SOPORTE_KEYS = ("soporte", "partner", "vendor", "plataforma",
                 "platform", "fuente", "source", "proveedor")

_BLUE = "#58A6FF"
_RED = "#F85149"
_GREEN = "#3FB950"
_AMBER = "#D29922"
_PURPLE = "#BC8CFF"
_PALETTE = [_BLUE, _RED, _GREEN, _AMBER, _PURPLE,
            "#79B8FF", "#FF7B72", "#7EE787", "#FFA657", "#D2A8FF"]


# ------------------------------------------------------------------
# SCHEMA
# ------------------------------------------------------------------
def get_config_schema(df: pd.DataFrame) -> dict:
    date_cols = get_date_columns(df)
    if not date_cols:
        dc = detect_date_column(df)
        date_cols = [dc] if dc else []

    canal_cols = [c for c in df.columns
                  if any(k in str(c).lower() for k in _CANAL_KEYS)]
    soporte_cols = [c for c in df.columns
                    if any(k in str(c).lower() for k in _SOPORTE_KEYS)]
    inv_cols = [c for c in df.columns
                if any(k in str(c).lower() for k in _INV_KEYS)]
    numeric_cols = df.select_dtypes("number").columns.tolist()
    kpi_cols = [c for c in numeric_cols if c not in inv_cols]

    # --------------------------------------------------------------
    # FILTRABLES: cualquier columna menos las de fecha (esas van por rango)
    # --------------------------------------------------------------
    def _filtrable(c):
        if c in date_cols:
            return False
        try:
            s = df[c]
        except Exception:
            return False
        # Texto / categórica: sí
        if s.dtype == object or str(s.dtype) == "category":
            return True
        # Numéricas con cardinalidad razonable: sí
        if pd.api.types.is_numeric_dtype(s):
            return s.nunique(dropna=True) <= 500
        # Booleanas: sí
        if pd.api.types.is_bool_dtype(s):
            return True
        return False

    filterable = []
    for c in df.columns:
        if _filtrable(c):
            filterable.append(str(c))

    # --------------------------------------------------------------
    # Debug para la consola de la app
    # --------------------------------------------------------------
    print("=== Diagnóstico de columnas ===")
    print(f"Fechas:     {date_cols}")
    print(f"Canal:      {canal_cols}")
    print(f"Soporte:    {soporte_cols}")
    print(f"Inversión:  {inv_cols}")
    print(f"KPIs:       {kpi_cols}")
    print("Filtrables y nº de valores únicos:")
    for c in filterable:
        try:
            n = df[c].nunique(dropna=True)
            print(f"  · {c}: {n} valores")
        except Exception:
            print(f"  · {c}: (error)")

    fields = [
        {"key": "date_col", "label": "Columna de fecha",
         "type": "select", "options": [str(c) for c in date_cols],
         "default": str(date_cols[0]) if date_cols else None},
        {"key": "canal_col", "label": "Columna de canal (Display, Search, ...)",
         "type": "select",
         "options": [str(c) for c in canal_cols] or [str(c) for c in df.columns],
         "default": str(canal_cols[0]) if canal_cols else None},
        {"key": "soporte_col", "label": "Columna de soporte (Criteo, DV360, ...)",
         "type": "select",
         "options": [str(c) for c in soporte_cols] or [str(c) for c in df.columns],
         "default": str(soporte_cols[0]) if soporte_cols else None},
        {"key": "inv_col", "label": "Columna de inversión",
         "type": "select",
         "options": [str(c) for c in inv_cols] or [str(c) for c in numeric_cols],
         "default": str(inv_cols[0]) if inv_cols else None},
        {"key": "kpi_cols", "label": "KPIs a analizar",
         "type": "multi", "options": [str(c) for c in kpi_cols],
         "default": [str(kpi_cols[0])] if kpi_cols else []},
        {"key": "filters", "label": "Filtros adicionales",
         "type": "filters", "options": filterable},
    ]

    return {"title": "Configuración · Inversión ↔ KPI por Canal",
            "fields": fields}
# ------------------------------------------------------------------
# RUN
# ------------------------------------------------------------------
def run(df: pd.DataFrame,
        date_col: str | None = None,
        canal_col: str | None = None,
        soporte_col: str | None = None,
        inv_col: str | None = None,
        kpi_cols: list[str] | None = None,
        filters: dict | None = None,
        ma_window: int = 2,
        progress_callback=None,
        **kwargs) -> dict:

    _emit_progress(progress_callback, 2, "Preparando análisis multisoporte...")

    # --- Filtros adicionales ---
    if filters:
        for col, allowed in filters.items():
            if col not in df.columns or not allowed:
                continue
            df = df[df[col].astype(str).isin([str(v) for v in allowed])].copy()
        if df.empty:
            return {"Error": "Los filtros han dejado el DataFrame vacío."}

    # --- Autodetección ---
    if not date_col or date_col not in df.columns:
        date_col = detect_date_column(df)
    if not canal_col or canal_col not in df.columns:
        canal_col = _auto(df, _CANAL_KEYS)
    if not soporte_col or soporte_col not in df.columns:
        soporte_col = _auto(df, _SOPORTE_KEYS, exclude={canal_col})
    if not inv_col or inv_col not in df.columns:
        inv_col = _auto(df, _INV_KEYS, numeric_only=True)
    if not kpi_cols:
        nums = df.select_dtypes("number").columns.tolist()
        kpi_cols = [c for c in nums if c != inv_col]

    missing = [k for k, v in [("fecha", date_col), ("canal", canal_col),
                              ("soporte", soporte_col), ("inversión", inv_col)]
               if not v]
    if missing or not kpi_cols:
        return {"Error": f"Faltan columnas: {missing or 'KPIs'}",
                "Detectado": {"fecha": date_col, "canal": canal_col,
                              "soporte": soporte_col, "inversión": inv_col,
                              "kpis": kpi_cols}}

    # --- Normalizar ---
    _emit_progress(progress_callback, 10, "Normalizando canales y soportes...")
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    df[inv_col] = pd.to_numeric(df[inv_col], errors="coerce").fillna(0)

    # Normaliza canal y soporte.
    # Importante: pasar a str ANTES de fillna() porque si el dtype es
    # 'category', pandas no permite rellenar con valores que no estén
    # en las categorías (falla con TypeError).
    def _safe_str_col(s: pd.Series) -> pd.Series:
        # 1) Convertir de category a object/str
        if isinstance(s.dtype, pd.CategoricalDtype):
            s = s.astype(object)
        # 2) fillna con "(vacío)"
        s = s.where(s.notna(), "(vacío)")
        # 3) Asegurar string puro
        s = s.astype(str).str.strip()
        # 4) Si quedó "nan" textual, lo pasamos a "(vacío)"
        s = s.replace({"nan": "(vacío)", "None": "(vacío)",
                        "NaN": "(vacío)", "": "(vacío)"})
        return s

    df[canal_col] = _safe_str_col(df[canal_col])
    df[soporte_col] = _safe_str_col(df[soporte_col])

    # Ahora sí: sorted sobre strings puros
    canales = sorted(df[canal_col].unique().tolist())
    print("=" * 60)
    print(f"CANAL columna: '{canal_col}'  ·  {len(canales)} canales únicos")
    for c in canales:
        sub = df[df[canal_col] == c]
        sops = sorted(sub[soporte_col].unique().tolist())
        print(f"  · {c}: {len(sub)} filas, {len(sops)} soportes → {sops}")
    print("=" * 60)

    figures = {}
    params_all = []
    long_all = []
    total_pairs = max(1, len(canales) * len(kpi_cols))
    pair_index = 0

    for canal in canales:
        df_c = df[df[canal_col] == canal].copy()
        if df_c.empty:
            continue

        for kpi in kpi_cols:
            pair_start = 15 + int(78 * pair_index / total_pairs)
            pair_index += 1
            pair_end = 15 + int(78 * pair_index / total_pairs)
            _emit_progress(
                progress_callback, pair_start,
                f"Canal: {canal} · KPI: {kpi}")
            if kpi not in df_c.columns:
                _emit_progress(
                    progress_callback, pair_end,
                    f"Omitido {canal} · {kpi}: columna no disponible")
                continue

            # --- Preparar ---
            sub = df_c[[date_col, soporte_col, inv_col, kpi]].copy()
            sub.columns = ["Fecha", "Soporte", "Inversion", "KPI"]
            sub = sub.dropna()
            sub = sub[(sub["Inversion"] > 0) & (sub["KPI"] > 0)]
            if sub.empty:
                _emit_progress(
                    progress_callback, pair_end,
                    f"Omitido {canal} · {kpi}: sin datos válidos")
                continue
            sub = sub.sort_values(["Soporte", "Fecha"]).reset_index(drop=True)

            # MA forward por soporte
            for col in ("Inversion", "KPI"):
                sub[f"{col}_MA"] = (sub.groupby("Soporte")[col]
                                       .transform(lambda x:
                                                  _ma_forward(x.values,
                                                              k=ma_window)))
            sub["Inv_Acum"] = sub.groupby("Soporte")["Inversion"].cumsum()
            sub["KPI_Acum"] = sub.groupby("Soporte")["KPI"].cumsum()

            # Fits por soporte
            fits_sop = {}
            n_supports = max(1, sub["Soporte"].nunique())
            for support_index, (sop, g) in enumerate(
                    sub.groupby("Soporte"), start=1):
                f = _fit_log(g["Inversion_MA"], g["KPI_MA"])
                if f:
                    fits_sop[sop] = f
                _emit_progress(
                    progress_callback,
                    pair_start + int(
                        (pair_end - pair_start) * 0.45
                        * support_index / n_supports),
                    f"{canal} · {kpi}: soporte "
                    f"{support_index}/{n_supports}")

            # Agregado del canal
            _emit_progress(
                progress_callback,
                pair_start + int((pair_end - pair_start) * 0.58),
                f"{canal} · {kpi}: ajustando total del canal")
            agg = (sub.groupby("Fecha", as_index=False)
                        .agg(Inversion=("Inversion", "sum"),
                             KPI=("KPI", "sum"))
                        .sort_values("Fecha"))
            agg["Inversion_MA"] = _ma_forward(agg["Inversion"].values,
                                              k=ma_window)
            agg["KPI_MA"] = _ma_forward(agg["KPI"].values, k=ma_window)
            agg["Inv_Acum"] = agg["Inversion"].cumsum()
            agg["KPI_Acum"] = agg["KPI"].cumsum()
            fit_agg = _fit_log(agg["Inversion_MA"], agg["KPI_MA"])

            # --- 6 figuras ---
            _emit_progress(
                progress_callback,
                pair_start + int((pair_end - pair_start) * 0.72),
                f"{canal} · {kpi}: generando gráficos")
            tag = f"[{canal}]"
            figs = {
                f"{tag} · Canal · Acumulado":   _plot_canal_acum(agg, kpi, canal),
                f"{tag} · Canal · Saturación":  _plot_canal_sat(agg, fit_agg, kpi, canal),
                f"{tag} · Canal · Marginal":    _plot_canal_marg(agg, fit_agg, kpi, canal),
                f"{tag} · Soportes · Acumulado":  _plot_sop_acum(sub, kpi, canal),
                f"{tag} · Soportes · Saturación": _plot_sop_sat(sub, fits_sop, kpi, canal),
                f"{tag} · Soportes · Marginal":   _plot_sop_marg(sub, fits_sop, kpi, canal),
            }

            # Marca canal y kpi en cada figura (para el filtro de la app)
            for f in figs.values():
                try:
                    f._mmm_canal = canal
                    f._mmm_kpi = kpi
                except Exception:
                    pass

            figures.update(figs)

            # --- Parámetros ---
            if fit_agg:
                med = agg["Inversion_MA"].median()
                params_all.append({
                    "Canal": canal, "KPI": kpi, "Nivel": "Canal",
                    "Soporte": "TODOS",
                    "a": round(fit_agg["a"], 2),
                    "b": round(fit_agg["b"], 4),
                    "R²": round(fit_agg["r2"], 4),
                    "Marginal/100$ @ mediana":
                        round(abs(fit_agg["b"]) / med * 100, 2)
                        if med > 0 else np.nan,
                })
            for sop, f in fits_sop.items():
                g = sub[sub["Soporte"] == sop]
                med = g["Inversion_MA"].median()
                params_all.append({
                    "Canal": canal, "KPI": kpi, "Nivel": "Soporte",
                    "Soporte": sop,
                    "a": round(f["a"], 2),
                    "b": round(f["b"], 4),
                    "R²": round(f["r2"], 4),
                    "Marginal/100$ @ mediana":
                        round(abs(f["b"]) / med * 100, 2)
                        if med > 0 else np.nan,
                })

            sub["Canal"] = canal
            sub["KPI_Nombre"] = kpi
            long_all.append(sub)
            _emit_progress(
                progress_callback, pair_end,
                f"Completado {canal} · {kpi}")

    if not figures:
        return {"Error": "No se generó ningún gráfico. Revisa filtros y columnas."}

    out = {}
    for name, fig in figures.items():
        out[name] = {"plot": fig}

    if params_all:
        out["Parámetros (KPI = a + b·log(Inv))"] = pd.DataFrame(params_all)
    if long_all:
        out["Datos normalizados (MA)"] = pd.concat(long_all, ignore_index=True)

    out["Resumen"] = {
        "Canales analizados": canales,
        "KPIs": kpi_cols,
        "Ventana MA": ma_window,
        "Columnas": {
            "Fecha": date_col, "Canal": canal_col,
            "Soporte": soporte_col, "Inversión": inv_col,
        },
    }
    _emit_progress(progress_callback, 100, "Análisis multisoporte completado")
    return out


def _emit_progress(callback, percent, message):
    """Publica progreso desde el worker sin depender de Tkinter."""
    pct = max(0, min(100, int(percent)))
    text = str(message).strip()
    if callback is not None:
        callback(pct, text)
    else:
        print(f"[Progreso {pct}%] {text}")


# ------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------
def _ma_forward(x, k=2):
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n == 0:
        return x
    out = np.zeros(n)
    for i in range(n):
        end = min(i + k - 1, n - 1)
        out[i] = np.nanmean(x[i:end + 1])
    return out


def _fit_log(inv, kpi):
    x = np.asarray(inv, dtype=float)
    y = np.asarray(kpi, dtype=float)
    mask = (x > 0) & np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 3 or np.std(np.log(x)) < 1e-9 or np.std(y) < 1e-9:
        return None
    lx = np.log(x)
    b, a = np.polyfit(lx, y, 1)
    y_pred = a + b * lx
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {"a": float(a), "b": float(b), "r2": r2}


def _auto(df, keys, numeric_only=False, exclude=None):
    exclude = exclude or set()
    cols = df.select_dtypes("number").columns if numeric_only else df.columns
    for c in cols:
        if c in exclude:
            continue
        if any(k in str(c).lower() for k in keys):
            return c
    return None


def _fmt_num(x, _=None):
    return f"{x:,.0f}".replace(",", ".")


def _fmt_dec(x, _=None):
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------------
# FIGURAS
# ------------------------------------------------------------------
def _plot_canal_acum(agg, kpi, canal):
    fig = Figure(figsize=(9, 5.5), dpi=100)
    ax = fig.add_subplot(111)
    ax.fill_between(agg["Inv_Acum"], agg["KPI_Acum"], alpha=0.22, color=_BLUE)
    ax.plot(agg["Inv_Acum"], agg["KPI_Acum"], color=_BLUE, lw=2.4)
    ax.scatter(agg["Inv_Acum"], agg["KPI_Acum"], s=32, color=_BLUE,
               edgecolor="white", lw=1.2, zorder=5)
    ax.set_xlabel("Inversión acumulada")
    ax.set_ylabel(f"{kpi} acumulado")
    ax.set_title(f"Canal {canal} · Evolución acumulada ({kpi})",
                 fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(FuncFormatter(_fmt_num))
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_num))
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def _plot_canal_sat(agg, fit, kpi, canal):
    fig = Figure(figsize=(9, 5.5), dpi=100)
    ax = fig.add_subplot(111)
    ax.scatter(agg["Inversion_MA"], agg["KPI_MA"], s=42, color=_BLUE,
               edgecolor="white", lw=1.2, zorder=5, label="Observado (MA)")
    if fit:
        x0 = max(1e-6, float(agg["Inversion_MA"].min()))
        x1 = float(agg["Inversion_MA"].max()) * 1.1
        xs = np.linspace(x0, x1, 200)
        b = abs(fit["b"])
        ys = fit["a"] + b * np.log(xs)
        ax.plot(xs, ys, color=_RED, lw=2.0,
                label=f"Ajuste log · R²={fit['r2']:.3f}")
    ax.set_xlabel("Inversión (MA)")
    ax.set_ylabel(kpi)
    ax.set_title(f"Canal {canal} · Saturación ({kpi})",
                 fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(FuncFormatter(_fmt_num))
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_num))
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    return fig


def _plot_canal_marg(agg, fit, kpi, canal):
    fig = Figure(figsize=(9, 5.5), dpi=100)
    ax = fig.add_subplot(111)
    if fit:
        b = abs(fit["b"])
        x0 = max(1e-6, float(agg["Inversion_MA"].min()))
        x1 = float(agg["Inversion_MA"].max()) * 1.1
        xs = np.linspace(x0, x1, 200)
        ys = (b / xs) * 100
        ax.plot(xs, ys, color=_RED, lw=2.0, ls="--",
                label=f"Marginal · b={b:.2f}")
        ys_pts = (b / agg["Inversion_MA"].values) * 100
        ax.scatter(agg["Inversion_MA"], ys_pts, s=42, color=_BLUE,
                   edgecolor="white", lw=1.2, zorder=5, label="Observado")
    ax.set_xlabel("Inversión (MA)")
    ax.set_ylabel(f"{kpi} incrementales / 100 $")
    ax.set_title(f"Canal {canal} · Rendimiento marginal ({kpi})",
                 fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(FuncFormatter(_fmt_num))
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_dec))
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    return fig


def _plot_sop_acum(long, kpi, canal):
    fig = Figure(figsize=(9, 5.5), dpi=100)
    ax = fig.add_subplot(111)
    for i, (sop, g) in enumerate(long.groupby("Soporte")):
        g = g.sort_values("Fecha")
        c = _PALETTE[i % len(_PALETTE)]
        ax.plot(g["Inv_Acum"], g["KPI_Acum"], color=c, lw=2.0, label=sop)
        ax.scatter(g["Inv_Acum"], g["KPI_Acum"], s=26, color=c,
                   edgecolor="white", lw=0.9, zorder=5)
    ax.set_xlabel("Inversión acumulada")
    ax.set_ylabel(f"{kpi} acumulado")
    ax.set_title(f"Canal {canal} · Acumulado por soporte ({kpi})",
                 fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(FuncFormatter(_fmt_num))
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_num))
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9, title="Soporte")
    fig.tight_layout()
    return fig


def _plot_sop_sat(long, fits, kpi, canal):
    fig = Figure(figsize=(9, 5.5), dpi=100)
    ax = fig.add_subplot(111)
    for i, (sop, g) in enumerate(long.groupby("Soporte")):
        c = _PALETTE[i % len(_PALETTE)]
        ax.scatter(g["Inversion_MA"], g["KPI_MA"], s=30, color=c, alpha=0.8,
                   edgecolor="white", lw=0.8, label=sop)
        fit = fits.get(sop)
        if fit:
            x0 = max(1e-6, float(g["Inversion_MA"].min()))
            x1 = float(g["Inversion_MA"].max()) * 1.1
            xs = np.linspace(x0, x1, 200)
            b = abs(fit["b"])
            ys = fit["a"] + b * np.log(xs)
            ax.plot(xs, ys, color=c, lw=1.8, alpha=0.85)
    ax.set_xlabel("Inversión (MA)")
    ax.set_ylabel(kpi)
    ax.set_title(f"Canal {canal} · Saturación por soporte ({kpi})",
                 fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(FuncFormatter(_fmt_num))
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_num))
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9, title="Soporte")
    fig.tight_layout()
    return fig


def _plot_sop_marg(long, fits, kpi, canal):
    fig = Figure(figsize=(9, 5.5), dpi=100)
    ax = fig.add_subplot(111)
    for i, (sop, g) in enumerate(long.groupby("Soporte")):
        c = _PALETTE[i % len(_PALETTE)]
        fit = fits.get(sop)
        if not fit:
            continue
        b = abs(fit["b"])
        x0 = max(1e-6, float(g["Inversion_MA"].min()))
        x1 = float(g["Inversion_MA"].max()) * 1.1
        xs = np.linspace(x0, x1, 200)
        ys = (b / xs) * 100
        ax.plot(xs, ys, color=c, lw=2.0, ls="--", label=sop)
        ys_pts = (b / g["Inversion_MA"].values) * 100
        ax.scatter(g["Inversion_MA"], ys_pts, s=28, color=c, alpha=0.85,
                   edgecolor="white", lw=0.8, zorder=5)
    ax.set_xlabel("Inversión (MA)")
    ax.set_ylabel(f"{kpi} incrementales / 100 $")
    ax.set_title(f"Canal {canal} · Marginal por soporte ({kpi})",
                 fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(FuncFormatter(_fmt_num))
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_dec))
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9, title="Soporte")
    fig.tight_layout()
    return fig
