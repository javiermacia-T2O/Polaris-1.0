"""
Causal Impact — versión clásica (3 paneles) con BSTS.

Replica la salida estándar de:
  - R: CausalImpact::CausalImpact()
  - Python: pycausalimpact (Google)

Salida:
  1. Gráfico clásico de 3 paneles (original / puntual / acumulado)
  2. Serie completa (real vs contrafactual) exportable
  3. Tabla resumen ejecutiva por target
  4. Distribución de modelos (grid search)

Parámetros configurables:
  alpha, prior_level_sd, nseasons, season_duration,
  dynamic_regression, standardize_data
"""

from itertools import combinations
import warnings

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from scipy import stats as scipy_stats

# ==================================================================
# MONKEY-PATCH: pandas >= 2.1 eliminó DataFrame.applymap
# pycausalimpact lo usa internamente → parcheamos para que funcione
# ==================================================================
try:
    if not hasattr(pd.DataFrame, "applymap"):
        pd.DataFrame.applymap = pd.DataFrame.map
except Exception:
    pass

# Silenciar warnings de statsmodels
warnings.filterwarnings(
    "ignore",
    message="Value of `irregular` may be overridden",
    category=UserWarning,
)

# Silenciar el warning específico de SpecificationWarning
try:
    from statsmodels.tools.sm_exceptions import SpecificationWarning
    warnings.filterwarnings("ignore", category=SpecificationWarning)
except Exception:
    pass

NAME = "Causal Impact (BSTS)"
DESCRIPTION = "Inferencia causal con BSTS · gráficos clásicos 3 paneles"
CATEGORY = "MMM"

CUSTOM_DIALOG = "CausalImpactDialog"

# ==================================================================
# SEPARADOR ÚNICO para combos (no puede aparecer en nombres de series)
# ==================================================================
CONTROLS_SEP = "  ;;  "

# Intenta cargar pycausalimpact; si no está, usamos nuestro motor interno
try:
    from causalimpact import CausalImpact as _PCI
    _HAS_PCI = True
except Exception:
    _HAS_PCI = False

try:
    import statsmodels.api as sm
    from statsmodels.tsa.statespace.structural import (
        UnobservedComponents,
    )
    _HAS_SM = True
except Exception:
    _HAS_SM = False


# ==================================================================
# SCHEMA (fallback si el diálogo custom no se usa)
# ==================================================================
def get_config_schema(df: pd.DataFrame) -> dict:
    return {
        "title": "Causal Impact · configuración rápida",
        "fields": [
            {"key": "date_col", "label": "Columna de fecha",
             "type": "select",
             "options": [str(c) for c in df.columns
                          if pd.api.types.is_datetime64_any_dtype(df[c])],
             "default": None},
            {"key": "kpi_col", "label": "KPI a analizar",
             "type": "select",
             "options": [str(c) for c in df.select_dtypes("number").columns],
             "default": None},
        ],
    }


# ==================================================================
# RUN
# ==================================================================
def run(df: pd.DataFrame,
        date_col=None, kpi_col=None, kpi_cols=None,
        dim_cols=None, target_tasks=None,
        controles_excluidos=None,
        sesgos_cols=None,
        fecha_campana=None, fecha_fin_datos=None,
        granularidad="semanal",
        umbral_correlacion=0.5,
        min_controles=1, max_controles=3,
        max_combinaciones=50, top_n_controles=10,
        alpha=0.05,
        prior_level_sd=0.01,
        nseasons=None, season_duration=1,
        dynamic_regression=False,
        standardize_data=False,
        progress_callback=None,
        **kwargs) -> dict:

    # ---------------------------------------------
    # Normalizar la lista de KPIs
    # ---------------------------------------------
    if kpi_cols is None:
        # El diálogo personalizado viaja por una capa de compatibilidad que
        # solo conserva ``kpi_col``. Aceptar una secuencia aquí permite que
        # una selección múltiple llegue intacta sin romper llamadas antiguas.
        if isinstance(kpi_col, (list, tuple, set)):
            kpi_cols = list(kpi_col)
        else:
            kpi_cols = [kpi_col] if kpi_col else []
    kpi_cols = [k for k in kpi_cols if k and k in df.columns]
    if not kpi_cols:
        return {"Estado": "ERROR · ningún KPI válido seleccionado"}

    _emit_progress(progress_callback, 2, "Preparando targets...")

    print("=" * 60)
    print(f"[CausalImpact] Inicio · {len(kpi_cols)} KPI(s): "
          f"{kpi_cols}")
    print(f"[CausalImpact] date={date_col}")
    print(f"[CausalImpact] BSTS: alpha={alpha} "
          f"prior_sd={prior_level_sd} nseasons={nseasons} "
          f"season_dur={season_duration} dyn_reg={dynamic_regression} "
          f"std={standardize_data}")
    print(f"[CausalImpact] pycausalimpact={'sí' if _HAS_PCI else 'no'}, "
          f"statsmodels={'sí' if _HAS_SM else 'no'}")
    print(f"[CausalImpact] df.shape={df.shape}")
    print("=" * 60)

    globals()["_WARNED_PCI"] = False
    globals()["_WARNED_SM"] = False

    # ---------------------------------------------
    # Validaciones generales
    # ---------------------------------------------
    if not fecha_campana or not fecha_fin_datos:
        return {"Estado": "ERROR · faltan fechas de campaña / fin"}
    try:
        fecha_campana_ts = pd.Timestamp(fecha_campana)
        fecha_fin_ts = pd.Timestamp(fecha_fin_datos)
    except Exception as e:
        return {"Estado": f"ERROR · fechas inválidas: {e}"}

    if not date_col or date_col not in df.columns:
        date_col = _auto_date(df)
    if not date_col:
        return {"Estado": "ERROR · sin columna de fecha"}

    dim_cols = [c for c in (dim_cols or []) if c in df.columns]
    sesgos_cols = [c for c in (sesgos_cols or []) if c in df.columns]
    controles_excluidos = list(controles_excluidos or [])

    # ---------------------------------------------
    # Tareas / targets
    # ---------------------------------------------
    tasks = _parse_tasks(target_tasks)
    if not tasks:
        if not dim_cols:
            return {"Estado": "ERROR · sin dimensiones y sin tareas"}
        first_dim = dim_cols[0]
        vals = df[first_dim].dropna().astype(str).unique().tolist()
        tasks = [{first_dim: [v]} for v in sorted(vals)]

    targets_nombres = set(_task_to_name(t) for t in tasks)
    controles_excluidos = list(set(controles_excluidos) | targets_nombres)

    # ✅ NUEVO: construir la lista de "task_str" de TODOS los targets
    # para que _analyze_target excluya sus columnas como controles.
    # Cada task se convierte en "col=v1|v2;col2=w1|w2" para que
    # _analyze_target sepa qué columnas del wide excluir.
    targets_task_strs = []
    for t in tasks:
        parts = []
        for col, vals in t.items():
            parts.append(f"{col}={'|'.join(str(v) for v in vals)}")
        targets_task_strs.append(";".join(parts))

    print(f"[CausalImpact] Targets: {len(tasks)}")
    print(f"[CausalImpact] Excluidos: {len(controles_excluidos)}")
    print(f"[CausalImpact] Task_strs target: {targets_task_strs}")

    # ---------------------------------------------
    # Normalización base (una sola vez)
    # ---------------------------------------------
    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work = work.dropna(subset=[date_col])
    for c in sesgos_cols:
        work[c] = pd.to_numeric(work[c], errors="coerce")

    if work.empty:
        return {"Estado": "ERROR · df vacío tras normalizar"}

    # ---------------------------------------------
    # Params motor
    # ---------------------------------------------
    params = {
        "umbral_correlacion": float(umbral_correlacion),
        "min_controles": int(min_controles),
        "max_controles": int(max_controles),
        "max_combinaciones": int(max_combinaciones),
        "top_n_controles": int(top_n_controles),
        "controles_excluidos": controles_excluidos,
        "all_targets_task_strs": targets_task_strs,   # ✅ NUEVO
        "alpha": float(alpha),
        "prior_level_sd": float(prior_level_sd),
        "nseasons": (int(nseasons) if nseasons and int(nseasons) >= 2
                     else None),
        "season_duration": int(season_duration) if season_duration else 1,
        "granularidad": granularidad,
        "dynamic_regression": bool(dynamic_regression),
        "standardize_data": bool(standardize_data),
    }

    # ---------------------------------------------
    # BUCLES: para cada KPI → para cada target
    # ---------------------------------------------
    results_all: list = []
    figs_clasicos: dict = {}
    wide_export: dict = {}

    t_global_start = __import__("time").time()

    total_pairs = max(1, len(kpi_cols) * len(tasks))
    pair_index = 0

    for kpi_idx, kpi_col in enumerate(kpi_cols, start=1):
        kpi_start = 5 + int(90 * pair_index / total_pairs)
        _emit_progress(
            progress_callback, kpi_start,
            f"Preparando KPI: {kpi_col}")
        print(f"\n{'#'*60}")
        print(f"# KPI {kpi_idx}/{len(kpi_cols)}: {kpi_col}")
        print(f"{'#'*60}")

        # Normalizar el KPI
        work_kpi = work.copy()
        original_kpi = work_kpi[kpi_col]
        numeric_kpi = pd.to_numeric(original_kpi, errors="coerce")
        if numeric_kpi.isna().any():
            count_missing = int(numeric_kpi.isna().sum())
            print(f"[CausalImpact] ⛔ {kpi_col}: {count_missing} KPI "
                  "ausentes; no se imputan como cero")
            pair_index += len(tasks)
            continue
        work_kpi[kpi_col] = numeric_kpi

        # --- Matriz wide por KPI ---
        try:
            wide, dim_map = _build_wide(
                work_kpi, date_col, kpi_col, dim_cols, granularidad)
        except Exception as e:
            print(f"[CausalImpact] ❌ _build_wide falló para "
                  f"'{kpi_col}': {e}")
            pair_index += len(tasks)
            continue

        print(f"[CausalImpact] Matriz wide: {wide.shape}")
        if len(wide) < 5:
            print(f"[CausalImpact] ⏭ Muy pocos periodos para "
                  f"'{kpi_col}'")
            pair_index += len(tasks)
            continue

        fechas = wide["Fecha_Analisis"].values
        fecha_campana_g = _snap_period(fecha_campana_ts, fechas, "ceil")
        fecha_fin_g = _snap_period(fecha_fin_ts, fechas, "floor")

        if fecha_campana_g > fecha_fin_g:
            print(f"[CausalImpact] ⏭ Campaña inválida para '{kpi_col}'")
            pair_index += len(tasks)
            continue

        # Sesgos (una sola vez por KPI)
        sesgos_df = None
        if sesgos_cols:
            freq_map = {"semanal": "W-SUN", "mensual": "M", "diaria": "D"}
            freq = freq_map.get(granularidad, "D")
            try:
                sesgos_df = (work.groupby(
                                work[date_col].dt.to_period(freq)
                                              .dt.start_time
                             )[sesgos_cols].mean().reset_index())
                sesgos_df.columns = (["Fecha_Analisis"]
                                       + list(sesgos_cols))
            except Exception as e:
                print(f"[CausalImpact] Error sesgos: {e}")
                sesgos_df = None

        # ---- Loop por target ----
        n_tasks = len(tasks)
        for i, task in enumerate(tasks, start=1):
            target_name = _task_to_name(task)
            full_name = f"{kpi_col} · {target_name}"
            pair_start = 5 + int(90 * pair_index / total_pairs)
            pair_end = 5 + int(90 * (pair_index + 1) / total_pairs)
            _emit_progress(
                progress_callback, pair_start,
                f"KPI: {kpi_col} · Target: {target_name}")

            print(f"\n{'─'*60}")
            print(f"[CausalImpact] ▶ [{kpi_col}] TARGET "
                  f"{i}/{n_tasks}: {target_name}")
            print(f"{'─'*60}")

            t_target_start = __import__("time").time()

            try:
                res = _analyze_target(
                    wide, dim_map, task, target_name,
                    fecha_campana_g, fecha_fin_g,
                    sesgos_df, params,
                    progress_callback=progress_callback,
                    progress_start=pair_start,
                    progress_end=pair_end)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[CausalImpact] ❌ {full_name}: {e}")
                pair_index += 1
                continue

            t_target = __import__("time").time() - t_target_start

            if res is None:
                print(f"[CausalImpact] ⏭ OMITIDO {full_name} "
                      f"({t_target:.1f}s)")
                pair_index += 1
                continue

            w = res["winner"]
            w["KPI"] = kpi_col  # ← guardar qué KPI corresponde

            print(f"[CausalImpact] ✅ {full_name}")
            print(f"    Efecto rel: {w['Efecto_Relativo']:+.2f}%  ·  "
                  f"p={w['P_Valor']:.4f}")

            results_all.append(w)

            # --- Gráficos con prefijo KPI ---
            try:
                fig = plot_causal_impact_classic(
                    w, target_name,
                    fecha_campana_g, fecha_fin_g,
                    params["alpha"])
                fig._mmm_kpi = kpi_col
                fig._mmm_target = target_name
                fig._mmm_kind = "CI"
                fig._mmm_name = f"CI - {full_name}"
                figs_clasicos[f"CI · {full_name}"] = {"plot": fig}
            except Exception as e:
                print(f"[CausalImpact] ⚠ Gráfico combinado: {e}")
                import traceback
                traceback.print_exc()

            try:
                fig1 = plot_ci_panel_series(
                    w, target_name,
                    fecha_campana_g, fecha_fin_g,
                    params["alpha"])
                fig1._mmm_kpi = kpi_col
                fig1._mmm_target = target_name
                fig1._mmm_kind = "Serie"
                fig1._mmm_name = f"Serie - {full_name}"
                figs_clasicos[f"CI Serie · {full_name}"] = {"plot": fig1}
            except Exception as e:
                print(f"[CausalImpact] ⚠ Panel serie: {e}")

            try:
                fig2 = plot_ci_panel_effect(
                    w, target_name,
                    fecha_campana_g, fecha_fin_g,
                    params["alpha"])
                fig2._mmm_kpi = kpi_col
                fig2._mmm_target = target_name
                fig2._mmm_kind = "Efecto_puntual"
                fig2._mmm_name = f"Efecto puntual - {full_name}"
                figs_clasicos[f"CI Efecto puntual · {full_name}"] = {
                    "plot": fig2}
            except Exception as e:
                print(f"[CausalImpact] ⚠ Panel efecto: {e}")

            try:
                fig3 = plot_ci_panel_cumulative(
                    w, target_name,
                    fecha_campana_g, fecha_fin_g,
                    params["alpha"])
                fig3._mmm_kpi = kpi_col
                fig3._mmm_target = target_name
                fig3._mmm_kind = "Efecto_acumulado"
                fig3._mmm_name = f"Efecto acumulado - {full_name}"
                figs_clasicos[f"CI Efecto acumulado · {full_name}"] = {
                    "plot": fig3}
            except Exception as e:
                print(f"[CausalImpact] ⚠ Panel acumulado: {e}")

            try:
                fig_box = plot_distribution_boxplot(
                    res["distribution"], target_name)
                fig_box._mmm_kpi = kpi_col
                fig_box._mmm_target = target_name
                fig_box._mmm_kind = "Boxplot"
                fig_box._mmm_name = f"Boxplot - {full_name}"
                figs_clasicos[f"Boxplot · {full_name}"] = {
                    "plot": fig_box}
            except Exception as e:
                print(f"[CausalImpact] ⚠ Boxplot: {e}")
            wide_export[full_name] = res["wide_target"]
            pair_index += 1

    t_global = __import__("time").time() - t_global_start

    print(f"\n{'='*60}")
    print(f"[CausalImpact] FIN · {len(results_all)} análisis OK "
          f"en {t_global:.1f}s")
    print(f"{'='*60}\n")

    if not results_all:
        return {
            "Estado": "ERROR · ningún análisis produjo resultados",
            "Diagnóstico": (
                "No existe un contrafactual suficientemente fiable con los "
                "datos disponibles. Revisa cobertura, estabilidad y errores "
                "del backtesting PRE antes de interpretar un efecto causal."),
            "quality_status": "No válida",
            "Diagnóstico del análisis": pd.DataFrame([{
                "Estado": "No se estimó ningún efecto",
                "Causa": ("No hubo targets/KPI con controles elegibles y "
                         "backtesting PRE superior al baseline."),
            }]),
        }

    # ---------------------------------------------
    # Ensamblar salida
    # ---------------------------------------------
    df_exec = _build_executive_table(results_all)

    out = {
        "Estado": "OK",
        "Resumen ejecutivo": df_exec,
    }
    out.update(figs_clasicos)

    for tname, wdf in wide_export.items():
        out[f"Matriz wide · {tname}"] = wdf

    print(f"[CausalImpact] OK · {len(results_all)} análisis")
    print("=" * 60)
    _emit_progress(progress_callback, 100, "Causal Impact completado")
    return out

# ==================================================================
# MOTOR POR TARGET
# ==================================================================
def _analyze_target(wide, dim_map, task, target_name,
                     fecha_campana, fecha_fin, sesgos_df, params,
                     progress_callback=None, progress_start=0,
                     progress_end=100):
    target_cols = [c for c, d in dim_map.items() if _matches(d, task)]
    if not target_cols:
        return None

    # =============================================================
    # Convertir controles_excluidos (task_str) al nombre de columna
    # del wide ("val1 | val2 | ...")
    # =============================================================
    excluidos_set = set()
    # ✅ NUEVO: excluir columnas de TODOS los targets (no solo del actual)
    for ts in params.get("all_targets_task_strs", []):
        if "=" not in ts:
            continue
        dims = {}
        for part in ts.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                dims[k.strip()] = v.strip()
            for col in wide.columns:
                if col == "Fecha_Analisis":
                    continue
                dims_col = dim_map.get(col, {})
                match = True
                for k, v in dims.items():
                    allowed = str(v).split("|")
                    col_val = str(dims_col.get(k, ""))
                    if col_val not in allowed:
                        match = False
                        break
                if match:
                    excluidos_set.add(col)
    for ex in params.get("controles_excluidos", []):
        if "=" not in ex:
            excluidos_set.add(ex)
            continue

        dims = {}
        for part in ex.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                dims[k.strip()] = v.strip()

        for col in wide.columns:
            if col == "Fecha_Analisis" or col in target_cols:
                continue
            dims_col = dim_map.get(col, {})
            match = True
            for k, v in dims.items():
                allowed = str(v).split("|")
                col_val = str(dims_col.get(k, ""))
                if col_val not in allowed:
                    match = False
                    break
            if match:
                excluidos_set.add(col)

    control_cols_all = [c for c in wide.columns
                        if c != "Fecha_Analisis"
                        and c not in target_cols
                        and c not in excluidos_set]

    if not control_cols_all:
        print("    → Sin controles")
        return None

    # =============================================================
    # Construir wide_t con TODOS los controles
    # =============================================================
    wide_t = wide[["Fecha_Analisis"] + control_cols_all].copy()
    wide_t["_target"] = wide[target_cols].sum(axis=1).values

    sesgo_cols = []
    if sesgos_df is not None and not sesgos_df.empty:
        wide_t = wide_t.merge(sesgos_df, on="Fecha_Analisis", how="left")
        sesgo_cols = [c for c in sesgos_df.columns
                       if c != "Fecha_Analisis"]
        for c in sesgo_cols:
            wide_t[c] = wide_t[c].fillna(wide_t[c].mean())

    fechas = wide_t["Fecha_Analisis"].values
    idx_pre = np.where(fechas < np.datetime64(fecha_campana))[0]
    idx_post = np.where(
        (fechas >= np.datetime64(fecha_campana)) &
        (fechas <= np.datetime64(fecha_fin)))[0]

    if len(idx_pre) < 15 or len(idx_post) < 1:
        print(f"    ⚠ Faltan datos (pre={len(idx_pre)} post={len(idx_post)})")
        return None

    print(f"    → Periodos: PRE={len(idx_pre)}, POST={len(idx_post)}")
    span = max(1, progress_end - progress_start)
    _emit_progress(
        progress_callback, progress_start + int(span * 0.15),
        f"{target_name}: calculando correlaciones preperiodo")

    y_pre = wide_t["_target"].values[idx_pre].astype(float)
    y_post = wide_t["_target"].values[idx_post].astype(float)

    if np.std(y_pre) < 1e-12:
        print(f"    ⚠ Varianza nula en PRE")
        return None

    seasonality = _infer_seasonality_pre(
        y_pre, params.get("granularidad", "semanal"))
    seasonal_period = (params.get("nseasons") or seasonality["period"])
    fit_params = {**params, "nseasons": seasonal_period}
    print("    → Estacionalidad PRE: "
          f"{seasonality['period'] or 'no detectada'} "
          f"({seasonality['reason']})")

    # =============================================================
    # Screening de controles candidatos; registrar cada rechazo.
    # =============================================================
    correlaciones = {}
    candidate_reasons = {}
    for c in wide.columns:
        if c == "Fecha_Analisis":
            continue
        if c in target_cols:
            candidate_reasons[c] = "Target tratado; excluido como control"
        elif c in excluidos_set:
            candidate_reasons[c] = "Excluido por selección manual o por target"
    for c in control_cols_all:
        x_pre = wide_t[c].values[idx_pre].astype(float)
        if not np.isfinite(x_pre).all():
            candidate_reasons[c] = "Valores no finitos en PRE"
            continue
        if np.std(x_pre) < 1e-12:
            candidate_reasons[c] = "Serie constante en PRE"
            continue
        r = np.corrcoef(y_pre, x_pre)[0, 1]
        if np.isfinite(r):
            correlaciones[c] = float(r)

    print(f"    → {len(control_cols_all)} controles candidatos  ·  "
          f"{len(correlaciones)} con correlación válida")

    # Pearson solo crea una shortlist. Estabilidad rolling y error predictivo
    # en pseudo-intervenciones PRE deciden qué controles llegan al modelo.
    ranked_single = []
    for c in control_cols_all:
        x_pre = wide_t[c].values[idx_pre].astype(float)
        if not np.isfinite(x_pre).all() or np.std(x_pre) < 1e-12:
            continue
        metric = _pre_backtest_combo(y_pre, x_pre.reshape(-1, 1))
        if metric is None:
            candidate_reasons[c] = (
                "Backtesting PRE insuficiente o no mejora el baseline ingenuo")
            continue
        rolling = _rolling_correlation_stability(y_pre, x_pre)
        rolling = rolling if np.isfinite(rolling) else -1.0
        spearman = scipy_stats.spearmanr(y_pre, x_pre).statistic
        spearman = float(spearman) if np.isfinite(spearman) else 0.0
        metric["Rolling_Cor"] = rolling
        metric["Spearman_PRE"] = spearman
        # Se conserva la correlación como señal débil; se penalizan series
        # inestables y el ranking principal depende del error PRE OOS.
        metric["Score_PRE"] = (metric["RMSE_OOS"] / max(np.std(y_pre), 1e-9)
                               + (1.0 - max(0.0, rolling)) * 0.10
                               + max(0.0, params["umbral_correlacion"]
                                     - abs(spearman)) * 0.05)
        ranked_single.append((metric["Score_PRE"], c, metric))
    ranked_single.sort(key=lambda item: item[0])
    validos = [c for _, c, _ in ranked_single]

    print(f"    → {len(validos)} controles con screening y backtest PRE válidos")

    if len(validos) < params["min_controles"]:
        print(f"    ⚠ Insuficientes (mín={params['min_controles']})")
        return None

    top = validos[:min(params["top_n_controles"], 12)]
    for c in validos[len(top):]:
        candidate_reasons[c] = "Fuera de la shortlist limitada por eficiencia"
    print(f"    → Top {len(top)} controles: "
          f"{', '.join(top[:5])}{'...' if len(top) > 5 else ''}")

    # =============================================================
    # Generar combos
    # =============================================================
    combos = []
    for k in range(params["min_controles"],
                   min(params["max_controles"], len(top)) + 1):
        combos.extend(list(combinations(top, k)))

    # Evaluar barato los combos solo con cortes PRE y mandar al backend caro
    # un máximo de cinco finalistas. La intervención POST no participa aquí.
    pre_ranked = []
    for combo in combos:
        x_combo = wide_t[list(combo)].values[idx_pre].astype(float)
        metric = _pre_backtest_combo(y_pre, x_combo)
        if metric is not None:
            pre_ranked.append((metric["RMSE_OOS"] / max(np.std(y_pre), 1e-9),
                               combo, metric))
    pre_ranked.sort(key=lambda item: item[0])
    combos = [item[1] for item in pre_ranked[:5]]
    finalists_controls = {control for combo in combos for control in combo}
    for c in top:
        if c not in finalists_controls:
            candidate_reasons.setdefault(
                c, "No incluido en las cinco mejores combinaciones PRE OOS")

    n_combos = len(combos)
    print(f"    → {n_combos} combos a simular")

    # =============================================================
    # Grid search
    # =============================================================
    dist_rows = []
    fits = {}

    t_combos_start = __import__("time").time()
    n_ok = 0
    n_fail = 0

    for j, combo in enumerate(combos, start=1):
        if j == 1 or j % 5 == 0 or j == n_combos:
            combo_pct = progress_start + int(
                span * (0.30 + 0.60 * j / max(1, n_combos)))
            _emit_progress(
                progress_callback, combo_pct,
                f"{target_name}: evaluando modelos {j}/{n_combos}")
        if j == 1 or j % 10 == 0 or j == n_combos:
            elapsed = __import__("time").time() - t_combos_start
            eta = (elapsed / j) * (n_combos - j) if j > 0 else 0
            print(f"    [combos] {j}/{n_combos}  "
                  f"({j/n_combos*100:.0f}%)  "
                  f"· ok={n_ok} fail={n_fail}  "
                  f"· {elapsed:.1f}s (ETA {eta:.1f}s)")

        try:
            fit = _fit_combo(wide_t, combo, sesgo_cols,
                              idx_pre, idx_post, y_pre, y_post, fit_params)
        except Exception as e:
            n_fail += 1
            if n_fail <= 3:
                print(f"    [combo fail] {e}")
            continue
        if fit is None:
            n_fail += 1
            continue

        n_ok += 1
        fit["seasonal_period"] = seasonal_period
        # ⚠ Usar CONTROLS_SEP (no " | " que ya aparece dentro de los nombres)
        key = CONTROLS_SEP.join(combo)
        fits[key] = fit

        pre_metrics = next((m for _, candidate, m in pre_ranked
                            if candidate == combo), {})
        dist_rows.append({
            "Destino": target_name,
            "Controles": key,
            "Num_Controles": len(combo),
            "Efecto_Absoluto": fit["effect_abs"],
            "Efecto_Relativo": fit["effect_rel_pct"],
            "P_Valor": fit["p_val"],
            "Inc_Prob": fit["pip"],
            "RMSE_PRE_OOS": pre_metrics.get("RMSE_OOS", np.nan),
            "RMSE_PRE_baseline": pre_metrics.get("RMSE_baseline_PRE", np.nan),
            "Mejora_PRE_pct": pre_metrics.get(
                "Improvement_vs_baseline_pct", np.nan),
            "MAE_PRE_OOS": pre_metrics.get("MAE_OOS", np.nan),
            "WAPE_PRE_OOS_pct": pre_metrics.get("WAPE_OOS_pct", np.nan),
            "Bias_PRE_OOS": pre_metrics.get("Bias_OOS", np.nan),
            "R2_PRE_OOS": pre_metrics.get("R2_OOS", np.nan),
            "Backtest_folds_PRE": pre_metrics.get("Folds", 0),
            "Estacionalidad_PRE": seasonal_period or "No detectada",
        })

    t_combos = __import__("time").time() - t_combos_start
    print(f"    → Combos terminados: {n_ok} ok, {n_fail} fallidos "
          f"en {t_combos:.1f}s")

    if not dist_rows:
        print(f"    ⚠ Ningún combo convergió para {target_name}")
        return None

    df_dist = pd.DataFrame(dist_rows)
    # POST p-values and effect sizes are reported but never choose the model.
    sig = df_dist.copy()

    # =============================================================
    # R_Pre con CONTROLS_SEP
    # =============================================================
    def _r_pre(cstr):
        try:
            ctrls = [c.strip() for c in cstr.split(CONTROLS_SEP)
                      if c.strip()]
            vals = [correlaciones[c] for c in ctrls
                     if c in correlaciones]
            return float(np.mean(vals)) if vals else np.nan
        except Exception:
            return np.nan

    sig["R_Pre"] = sig["Controles"].apply(_r_pre)

    # =============================================================
    # Selección congelada con el error de backtesting PRE OOS.
    # =============================================================
    best_row = sig.sort_values(["RMSE_PRE_OOS", "MAE_PRE_OOS"]).iloc[0]
    best_key = best_row["Controles"]
    best_fit = fits[best_key]

    print(f"    ✓ Ganador: {best_key}")
    print(f"    Efecto: {best_row['Efecto_Relativo']:.2f}%  "
          f"p={best_row['P_Valor']:.4f}")

    backend = best_fit.get("engine", "desconocido")
    if backend == "ols-fallback":
        quality_status = "Baja"
        quality_reason = (
            "Se usó OLS exploratorio, no BSTS; la incertidumbre no modela "
            "dependencia temporal y no debe leerse como evidencia causal.")
    elif backend == "statsmodels":
        quality_status = "Media"
        quality_reason = (
            "UCM de statsmodels es una aproximación; faltan placebos, "
            "contaminación y análisis de sensibilidad.")
    else:
        quality_status = "Media"
        quality_reason = (
            "Backtesting PRE superado. Aún deben revisarse contaminación, "
            "placebos y sensibilidad antes de una conclusión causal.")
    backend_warning = {
        "pycausalimpact": (
            "Backend pycausalimpact; la validez causal aún depende de "
            "controles no afectados y supuestos PRE/POST."),
        "statsmodels": (
            "Aproximación UCM de statsmodels; no equivale a inferencia BSTS "
            "de pycausalimpact."),
        "ols-fallback": (
            "OLS exploratorio con incertidumbre simplificada; no equivale a "
            "BSTS y no debe interpretarse como evidencia causal."),
    }.get(backend, "Backend no identificado; validar antes de interpretar.")

    winner = {
        "Target": target_name,
        "Controles": best_key,
        "Num_Controles": int(best_row["Num_Controles"]),
        "Efecto_Absoluto": float(best_row["Efecto_Absoluto"]),
        "Efecto_Relativo": float(best_row["Efecto_Relativo"]),
        "P_Valor": float(best_row["P_Valor"]),
        "Inc_Prob": float(best_row["Inc_Prob"]),
        "R_Pre": float(best_row["R_Pre"])
            if np.isfinite(best_row["R_Pre"]) else np.nan,
        "Correlaciones_Controles": {
            c.strip(): float(correlaciones[c.strip()])
            for c in best_key.split(CONTROLS_SEP)
            if c.strip() in correlaciones
        },
        "Controles_Descartados": [
            {"Control": c, "Motivo": reason}
            for c, reason in candidate_reasons.items()
            if c not in best_key.split(CONTROLS_SEP)
        ],
        "Metricas_Backtest_PRE": {
            "RMSE_OOS": float(best_row["RMSE_PRE_OOS"]),
            "MAE_OOS": float(best_row["MAE_PRE_OOS"]),
            "WAPE_OOS_pct": float(best_row["WAPE_PRE_OOS_pct"]),
            "Bias_OOS": float(best_row["Bias_PRE_OOS"]),
            "R2_OOS": float(best_row["R2_PRE_OOS"]),
            "Folds": int(best_row["Backtest_folds_PRE"]),
            "RMSE_baseline_PRE": float(best_row["RMSE_PRE_baseline"]),
            "Improvement_vs_baseline_pct": float(best_row["Mejora_PRE_pct"]),
        },
        "quality_status": quality_status,
        "Diagnostico_Calidad": quality_reason,
        "Backend": backend,
        "Advertencia_Backend": backend_warning,
        "Estacionalidad_PRE": seasonal_period or "No detectada",
        "Diagnostico_Estacionalidad_PRE": seasonality["reason"],
        "Advertencia_Metodologica": (
            "Controles y modelo seleccionados con backtesting PRE. El POST "
            "se usa solo para estimar el efecto; la correlación PRE aislada "
            "no demuestra validez causal."),
        "Volumen": float(y_pre.sum() + y_post.sum()),
        "_fit": best_fit,
        "_wide": wide_t,
        "_idx_pre": idx_pre,
        "_idx_post": idx_post,
    }

    # =============================================================
    # Construir wide_target para export/plot
    # =============================================================
    best_ctrls = [c.strip() for c in best_key.split(CONTROLS_SEP)
                   if c.strip()]

    # Verificar que todos los controles existen en wide_t
    missing = [c for c in best_ctrls if c not in wide_t.columns]
    if missing:
        print(f"    ⚠ Controles no encontrados en wide_t: {missing}")
        best_ctrls = [c for c in best_ctrls if c in wide_t.columns]

    cols_export = ["Fecha_Analisis", "_target"] + best_ctrls + sesgo_cols
    cols_export = [c for c in cols_export if c in wide_t.columns]

    wide_target = wide_t[cols_export].copy()
    wide_target = wide_target.rename(columns={"_target": "Target"})

    return {
        "winner": winner,
        "distribution": df_dist.to_dict("records"),
        "wide_target": wide_target,
    }


def _emit_progress(callback, percent, message):
    """Publica progreso sin depender de Tkinter ni tocar widgets.

    El callback opcional recibe ``(porcentaje, mensaje)``. La línea impresa
    mantiene el progreso visible a través del puente de stdout existente.
    """
    pct = max(0, min(100, int(percent)))
    text = str(message).strip()
    if callback is not None:
        callback(pct, text)
    else:
        # Compatibilidad con el puente existente que convierte stdout en
        # mensajes de progreso cuando aún no se inyecta un callback.
        print(f"[Progreso {pct}%] {text}")


def _rolling_correlation_stability(target: np.ndarray,
                                  control: np.ndarray) -> float:
    """Media de correlación en mitades solapadas PRE; NaN si no estimable."""
    n = len(target)
    width = max(6, n // 2)
    starts = sorted(set([0, max(0, n - width), max(0, (n - width) // 2)]))
    values = []
    for start in starts:
        stop = min(n, start + width)
        if stop - start < 4:
            continue
        a, b = target[start:stop], control[start:stop]
        if np.std(a) > 1e-12 and np.std(b) > 1e-12:
            corr = float(np.corrcoef(a, b)[0, 1])
            if np.isfinite(corr):
                values.append(corr)
    if not values:
        return float("nan")
    # Penaliza inversión de signo y variación entre subperiodos.
    return float(np.mean(values) - np.std(values))


def _pre_backtest_combo(target: np.ndarray,
                        controls: np.ndarray) -> dict | None:
    """Backtest expanding-window lineal; todos los cortes están dentro PRE."""
    y = np.asarray(target, dtype=float).reshape(-1)
    x = np.asarray(controls, dtype=float)
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    n = len(y)
    if n < 15 or x.shape[0] != n or not np.isfinite(y).all() \
            or not np.isfinite(x).all():
        return None
    min_train = max(8, x.shape[1] + 4, int(n * 0.45))
    test_size = max(2, int(n * 0.10))
    cutoffs = sorted(set(min(n - 2, max(min_train, int(n * share)))
                         for share in (0.55, 0.70, 0.82)))
    actual_parts, predicted_parts, baseline_parts = [], [], []
    for cutoff in cutoffs:
        end = min(n, cutoff + test_size)
        if end <= cutoff or cutoff < min_train:
            continue
        train_x, test_x = x[:cutoff], x[cutoff:end]
        means = train_x.mean(axis=0)
        scales = train_x.std(axis=0)
        scales[scales < 1e-9] = 1.0
        train_z = (train_x - means) / scales
        test_z = (test_x - means) / scales
        design_train = np.column_stack([np.ones(cutoff), train_z])
        design_test = np.column_stack([np.ones(end - cutoff), test_z])
        # Ridge ligero con intercepto sin penalizar; reduce inestabilidad
        # cuando hay controles colineales y evita una grid search costosa.
        penalty = np.eye(design_train.shape[1]) * 1e-3
        penalty[0, 0] = 0.0
        try:
            beta = np.linalg.solve(design_train.T @ design_train + penalty,
                                   design_train.T @ y[:cutoff])
        except np.linalg.LinAlgError:
            beta, *_ = np.linalg.lstsq(design_train, y[:cutoff], rcond=None)
        actual_parts.append(y[cutoff:end])
        predicted_parts.append(design_test @ beta)
        baseline_parts.append(np.full(end - cutoff, np.mean(y[:cutoff])))
    if len(actual_parts) < 2:
        return None
    actual = np.concatenate(actual_parts)
    predicted = np.concatenate(predicted_parts)
    baseline = np.concatenate(baseline_parts)
    residual = actual - predicted
    baseline_residual = actual - baseline
    rmse_oos = float(np.sqrt(np.mean(residual ** 2)))
    baseline_rmse = float(np.sqrt(np.mean(baseline_residual ** 2)))
    if rmse_oos >= baseline_rmse:
        return None
    denominator = float(np.sum(np.abs(actual)))
    variance = float(np.sum((actual - np.mean(actual)) ** 2))
    return {
        "RMSE_OOS": rmse_oos,
        "RMSE_baseline_PRE": baseline_rmse,
        "Improvement_vs_baseline_pct": (
            float((baseline_rmse - rmse_oos) / baseline_rmse * 100)
            if baseline_rmse > 1e-12 else 0.0),
        "MAE_OOS": float(np.mean(np.abs(residual))),
        "WAPE_OOS_pct": (float(np.sum(np.abs(residual)) / denominator * 100)
                         if denominator > 1e-12 else np.nan),
        "Bias_OOS": float(np.mean(residual)),
        "R2_OOS": (1.0 - float(np.sum(residual ** 2)) / variance
                   if variance > 1e-12 else np.nan),
        "Folds": len(actual_parts),
    }

# ==================================================================
# FIT BSTS (pycausalimpact o UnobservedComponents)
# ==================================================================
def _infer_seasonality_pre(values: np.ndarray,
                           granularity: str = "semanal") -> dict:
    """Detecta una periodicidad repetible usando exclusivamente el PRE.

    Requiere tres ciclos completos y que la forma por fase explique al menos
    un 15 % de la varianza residual tras retirar una tendencia lineal. Si no
    alcanza ambos criterios, la estacionalidad queda desactivada.
    """
    y = np.asarray(values, dtype=float).reshape(-1)
    y = y[np.isfinite(y)]
    cadence = str(granularity or "semanal").strip().casefold()
    candidates = ({"diaria": (7, 365), "daily": (7, 365),
                   "semanal": (52,), "weekly": (52,),
                   "mensual": (12,), "monthly": (12,)}.get(cadence, ()))
    if not candidates:
        return {"period": None, "strength": np.nan,
                "reason": "Granularidad sin periodo estacional configurado."}

    if len(y) < 8 or np.std(y) < 1e-12:
        return {"period": None, "strength": np.nan,
                "reason": "PRE demasiado corto o constante para detectar ciclo."}
    time = np.arange(len(y), dtype=float)
    design = np.column_stack([np.ones(len(y)), time - time.mean()])
    trend = design @ np.linalg.lstsq(design, y, rcond=None)[0]
    residual = y - trend
    scored = []
    for period in candidates:
        cycles = len(residual) // period
        if cycles < 3:
            continue
        complete = residual[:cycles * period].reshape(cycles, period)
        variance = float(np.mean(complete ** 2))
        if variance < 1e-12:
            continue
        phase = complete.mean(axis=0, keepdims=True)
        unexplained = float(np.mean((complete - phase) ** 2))
        strength = max(0.0, 1.0 - unexplained / variance)
        scored.append((strength, period))
    if not scored:
        return {"period": None, "strength": np.nan,
                "reason": "No hay tres ciclos completos en el histórico PRE."}
    strength, period = max(scored)
    if strength < 0.15:
        return {"period": None, "strength": strength,
                "reason": (f"La señal cíclica PRE es débil "
                           f"(varianza explicada={strength:.1%}).")}
    return {"period": period, "strength": strength,
            "reason": (f"Ciclo de {period} observaciones; "
                       f"varianza PRE explicada={strength:.1%}.")}


def _fit_combo(wide_t, combo, sesgo_cols,
                idx_pre, idx_post, y_pre, y_post, params):
    """
    Ajusta un modelo BSTS. Intenta pycausalimpact primero; si no,
    usa statsmodels.UnobservedComponents con la misma estructura.
    """
    all_cols = list(combo) + list(sesgo_cols)
    n_pre = len(idx_pre)
    n_post = len(idx_post)

    # --- Construir DataFrame para pycausalimpact ---
    df_ci = pd.DataFrame(
        np.column_stack([y_pre, wide_t[all_cols].values[idx_pre]]),
        columns=["y"] + all_cols,
    )
    df_ci_post = pd.DataFrame(
        np.column_stack([y_post, wide_t[all_cols].values[idx_post]]),
        columns=["y"] + all_cols,
    )
    full = pd.concat([df_ci, df_ci_post], ignore_index=True)
    full.index = pd.RangeIndex(len(full))

    pre_period = [0, n_pre - 1]
    post_period = [n_pre, n_pre + n_post - 1]

    # --- Intento 1: pycausalimpact ---
    if _HAS_PCI:
        try:
            model_args = {}
            if params["prior_level_sd"]:
                model_args["prior_level_sd"] = params["prior_level_sd"]
            if params["nseasons"] and params["nseasons"] >= 2:
                model_args["nseasons"] = params["nseasons"]
                model_args["season_duration"] = \
                    params.get("season_duration", 1)
            if params["dynamic_regression"]:
                model_args["dynamic_regression"] = True

            ci = _PCI(full, pre_period, post_period,
                       alpha=params["alpha"],
                       model_args=model_args)
            return _extract_from_pycausalimpact(
                ci, full, y_pre, y_post, all_cols,
                params, n_pre, n_post)
        except Exception as e:
            # Silenciar solo el primer aviso por ejecución
            global _WARNED_PCI
            if not globals().get("_WARNED_PCI", False):
                print(f"  [pycausalimpact falló: {e}] "
                      f"(se usará fallback statsmodels/OLS)")
                globals()["_WARNED_PCI"] = True

    # --- Intento 2: statsmodels UnobservedComponents ---
    if _HAS_SM:
        try:
            return _fit_statsmodels(full, y_pre, y_post, all_cols,
                                     n_pre, n_post, params)
        except Exception as e:
            global _WARNED_SM
            if not globals().get("_WARNED_SM", False):
                print(f"  [statsmodels falló: {e}] "
                      f"(se usará fallback OLS)")
                globals()["_WARNED_SM"] = True

    # --- Fallback: OLS ---
    return _fit_ols_fallback(wide_t, combo, sesgo_cols,
                              idx_pre, idx_post, y_pre, y_post, params)


def _extract_from_pycausalimpact(ci, full, y_pre, y_post,
                                    all_cols, params, n_pre, n_post):
    """Extrae métricas y series de un objeto pycausalimpact."""
    try:
        s = ci.summary_data
        abs_eff = float(s.loc["abs_effect", "average"]) * n_post
        rel_eff = float(s.loc["rel_effect", "average"]) * 100
        p_val = (float(s.loc["p_value", "average"])
                 if "p_value" in s.index else np.nan)
    except Exception as exc:
        raise RuntimeError(
            f"pycausalimpact no devolvió un resumen interpretable: {exc}") from exc

    # Predicciones e IC
    try:
        pred = ci.inferences
        y_pred = pred["predicted"].values
        y_lower = pred["predicted_lower"].values
        y_upper = pred["predicted_upper"].values
    except Exception as exc:
        raise RuntimeError(
            f"pycausalimpact no devolvió intervalos de predicción: {exc}") from exc

    y_actual = full["y"].values
    effect = y_actual - y_pred

    se = (y_upper - y_lower) / (2 * 1.96)
    se = np.where(se < 1e-9, 1e-9, se)

    # CausalImpact no publica una PIP para este modelo; coincidencia de signos
    # de errores no es probabilidad posterior de inclusión.
    pip = np.nan

    return {
        "effect_abs": abs_eff,
        "effect_rel_pct": rel_eff,
        "p_val": p_val,
        "pip": pip,
        "y_actual": y_actual,
        "y_pred": y_pred,
        "y_lower": y_lower,
        "y_upper": y_upper,
        "effect": effect,
        "n_pre": n_pre,
        "n_post": n_post,
        "combo": all_cols,
        "sesgos": [],
        "engine": "pycausalimpact",
        "alpha": params["alpha"],
    }


def _fit_statsmodels(full, y_pre, y_post, all_cols,
                      n_pre, n_post, params):
    """Fallback con statsmodels UnobservedComponents."""
    y_all = full["y"].values.astype(float)
    X_all = full[all_cols].values.astype(float)

    # Estandarizar si procede
    if params.get("standardize_data"):
        mu, sd = X_all.mean(0), X_all.std(0)
        sd = np.where(sd < 1e-9, 1, sd)
        X_all = (X_all - mu) / sd
    level = "local linear trend"
    seasonality = params.get("nseasons")
    seas = seasonality if seasonality and seasonality >= 2 else None

    # Silenciar el warning de statsmodels sobre 'irregular'
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        model = UnobservedComponents(
            y_all[:n_pre],
            level=level,
            seasonal=seas,
            exog=X_all[:n_pre],
            irregular=True,
        )
        res = model.fit(disp=False, maxiter=200)

    # Predecir post
    fc = res.get_forecast(steps=n_post, exog=X_all[n_pre:])
    y_pred_post = np.asarray(fc.predicted_mean, dtype=float)
    ci = fc.conf_int(alpha=params["alpha"])
    lower_post = np.asarray(ci.iloc[:, 0], dtype=float)
    upper_post = np.asarray(ci.iloc[:, 1], dtype=float)

    # Fit in-sample
    y_pred_pre = np.asarray(res.fittedvalues, dtype=float)

    y_pred = np.concatenate([y_pred_pre, y_pred_post])
    y_lower = np.concatenate([y_pred_pre, lower_post])
    y_upper = np.concatenate([y_pred_pre, upper_post])

    effect = y_all - y_pred
    effect_post = effect[n_pre:]

    abs_eff = float(np.sum(effect_post))
    cum_pred = float(np.sum(y_pred_post))
    rel_eff = abs_eff / max(abs(cum_pred), 1e-9) * 100

    # Varianza acumulada
    var_post = ((upper_post - lower_post) / (2 * 1.96)) ** 2
    se_cum = float(np.sqrt(np.sum(var_post)))

    if se_cum > 1e-12:
        z = abs_eff / se_cum
        p_val = float(2 * (1 - scipy_stats.norm.cdf(abs(z))))
        sgn = 1.0 if abs_eff >= 0 else -1.0
    else:
        p_val = 1.0
    pip = np.nan

    return {
        "effect_abs": abs_eff,
        "effect_rel_pct": rel_eff,
        "p_val": p_val,
        "pip": pip,
        "y_actual": y_all,
        "y_pred": y_pred,
        "y_lower": y_lower,
        "y_upper": y_upper,
        "effect": effect,
        "n_pre": n_pre,
        "n_post": n_post,
        "combo": all_cols,
        "sesgos": [],
        "engine": "statsmodels",
        "alpha": params["alpha"],
    }


def _fit_ols_fallback(wide_t, combo, sesgo_cols,
                       idx_pre, idx_post, y_pre, y_post, params):
    """Fallback último: OLS con IC normal."""
    all_cols = list(combo) + list(sesgo_cols)
    X_pre = wide_t[all_cols].values[idx_pre].astype(float)
    X_post = wide_t[all_cols].values[idx_post].astype(float)

    X_pre_c = sm.add_constant(X_pre, has_constant="add")
    X_post_c = sm.add_constant(X_post, has_constant="add")

    std = np.std(X_pre_c, axis=0)
    keep = std > 1e-12
    keep[0] = True
    X_pre_c = X_pre_c[:, keep]
    X_post_c = X_post_c[:, keep]

    if X_pre_c.shape[1] < 2:
        return None

    model = sm.OLS(y_pre, X_pre_c).fit()
    y_pred_pre = model.predict(X_pre_c)
    y_pred_post = model.predict(X_post_c)

    resid = y_pre - y_pred_pre
    dof = max(len(y_pre) - X_pre_c.shape[1], 1)
    se = float(np.sqrt(np.sum(resid**2) / dof))

    lower_post = y_pred_post - 1.96 * se
    upper_post = y_pred_post + 1.96 * se

    y_actual = np.concatenate([y_pre, y_post])
    y_pred = np.concatenate([y_pred_pre, y_pred_post])
    y_lower = np.concatenate([y_pred_pre, lower_post])
    y_upper = np.concatenate([y_pred_pre, upper_post])
    effect = y_actual - y_pred

    abs_eff = float(np.sum(effect[len(y_pre):]))
    rel_eff = abs_eff / max(abs(np.sum(y_pred_post)), 1e-9) * 100
    se_cum = se * np.sqrt(len(y_post))
    z = abs_eff / se_cum if se_cum > 1e-12 else 0
    p_val = float(2 * (1 - scipy_stats.norm.cdf(abs(z))))
    sgn = 1.0 if abs_eff >= 0 else -1.0
    pip = np.nan

    return {
        "effect_abs": abs_eff,
        "effect_rel_pct": rel_eff,
        "p_val": p_val,
        "pip": pip,
        "y_actual": y_actual,
        "y_pred": y_pred,
        "y_lower": y_lower,
        "y_upper": y_upper,
        "effect": effect,
        "n_pre": len(y_pre),
        "n_post": len(y_post),
        "combo": all_cols,
        "sesgos": [],
        "engine": "ols-fallback",
        "alpha": params["alpha"],
    }


def plot_causal_impact_classic(winner, target_name,
                                 fecha_campana, fecha_fin, alpha=0.05):
    """
    Réplica profesional del plot clásico de CausalImpact.

    Layout limpio:
      - Fondo grisáceo claro
      - Sin subtítulo de métricas
      - Fechas SOLO en el panel inferior
    """
    fit = winner.get("_fit") if isinstance(winner, dict) else None

    BG = "#F5F5F5"
    fig = Figure(figsize=(12, 9.5), dpi=100, facecolor=BG)
    fig._mmm_keep_style = True

    if fit is None:
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "Sin datos del modelo",
                ha="center", va="center", fontsize=12)
        ax.axis("off")
        return fig

    # ==========================================================
    # Extraer series
    # ==========================================================
    y_actual = np.asarray(fit.get("y_actual", []), dtype=float)
    y_pred = np.asarray(fit.get("y_pred", []), dtype=float)
    y_lower = np.asarray(fit.get("y_lower", []), dtype=float)
    y_upper = np.asarray(fit.get("y_upper", []), dtype=float)
    effect = np.asarray(fit.get("effect", []), dtype=float)
    n_pre = int(fit.get("n_pre", 0))
    n_total = len(y_actual)

    if n_total == 0:
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "Sin datos",
                ha="center", va="center", fontsize=12)
        ax.axis("off")
        return fig

    x = np.arange(n_total)
    fechas = _get_fechas(winner)

    # ==========================================================
    # Paleta clara
    # ==========================================================
    C_OBS = "#0D47A1"
    C_PRED = "#C62828"
    C_CI = "#EF5350"
    C_EFFECT = "#2E7D32"
    C_EFFECT_CI = "#66BB6A"
    C_POST = "#FFF3E0"
    C_INTERV = "#D32F2F"
    C_GRID = "#D5D5D5"
    C_TEXT = "#212121"

    # ==========================================================
    # PANEL 1 · Serie observada vs contrafactual
    # ==========================================================
    ax1 = fig.add_subplot(3, 1, 1)
    ax1.set_facecolor(BG)

    ax1.axvspan(n_pre - 0.5, n_total - 0.5,
                 color=C_POST, alpha=0.7, zorder=0)

    ax1.fill_between(x, y_lower, y_upper,
                      color=C_CI, alpha=0.18, zorder=1,
                      label=f"IC {(1-alpha)*100:.0f}%")
    ax1.plot(x, y_pred, color=C_PRED, lw=2.0, ls="--",
              label="Contrafactual", zorder=3)
    ax1.plot(x, y_actual, color=C_OBS, lw=2.2,
              label="Observado", zorder=4)
    ax1.axvline(n_pre - 0.5, color=C_INTERV, lw=1.8,
                 alpha=0.9, zorder=5, label="Intervención")

    ax1.set_ylabel("KPI", fontsize=10, color=C_TEXT)
    ax1.set_title(f"Serie observada vs contrafactual · {target_name}",
                   fontsize=11, fontweight="bold", color=C_TEXT,
                   pad=10)
    ax1.legend(loc="upper left", fontsize=8, ncol=4,
                framealpha=0.95, edgecolor=C_GRID,
                facecolor=BG)
    _style_ax_light(ax1, C_GRID, C_TEXT, BG)

    # Sin fechas en el panel 1
    ax1.set_xticklabels([])
    ax1.tick_params(axis="x", which="both", length=0)

    # ==========================================================
    # PANEL 2 · Efecto puntual
    # ==========================================================
    ax2 = fig.add_subplot(3, 1, 2, sharex=ax1)
    ax2.set_facecolor(BG)

    ax2.axvspan(n_pre - 0.5, n_total - 0.5,
                 color=C_POST, alpha=0.7, zorder=0)

    effect_lower = effect - (y_upper - y_pred)
    effect_upper = effect + (y_pred - y_lower)

    ax2.fill_between(x, effect_lower, effect_upper,
                      color=C_EFFECT_CI, alpha=0.25, zorder=1,
                      label="IC 95%")
    ax2.plot(x, effect, color=C_EFFECT, lw=1.9, zorder=3,
              label="Efecto puntual")

    ax2.axhline(0, color="#9E9E9E", lw=1, ls="--", zorder=2)
    ax2.axvline(n_pre - 0.5, color=C_INTERV, lw=1.8, zorder=5)

    ax2.set_ylabel("Efecto puntual", fontsize=10, color=C_TEXT)
    ax2.set_title("Efecto puntual semana a semana",
                   fontsize=11, fontweight="bold", color=C_TEXT,
                   pad=10)
    ax2.legend(loc="upper left", fontsize=8, ncol=2,
                framealpha=0.95, edgecolor=C_GRID,
                facecolor=BG)
    _style_ax_light(ax2, C_GRID, C_TEXT, BG)

    # Sin fechas en el panel 2
    ax2.set_xticklabels([])
    ax2.tick_params(axis="x", which="both", length=0)

    # ==========================================================
    # PANEL 3 · Efecto acumulado
    # ==========================================================
    ax3 = fig.add_subplot(3, 1, 3, sharex=ax1)
    ax3.set_facecolor(BG)

    cum_effect = np.cumsum(effect)
    resid_var = ((y_upper - y_lower) / (2 * 1.96)) ** 2
    cum_se = np.sqrt(np.cumsum(resid_var))
    cum_lower = cum_effect - 1.96 * cum_se
    cum_upper = cum_effect + 1.96 * cum_se

    cum_e_post = np.full(n_total, np.nan)
    cum_e_post[n_pre:] = cum_effect[n_pre:]
    cum_lo_post = np.full(n_total, np.nan)
    cum_lo_post[n_pre:] = cum_lower[n_pre:]
    cum_hi_post = np.full(n_total, np.nan)
    cum_hi_post[n_pre:] = cum_upper[n_pre:]

    ax3.axvspan(n_pre - 0.5, n_total - 0.5,
                 color=C_POST, alpha=0.7, zorder=0)

    ax3.fill_between(x, cum_lo_post, cum_hi_post,
                      color=C_EFFECT_CI, alpha=0.25, zorder=1,
                      label="IC 95%")
    ax3.plot(x, cum_e_post, color=C_EFFECT, lw=2.2, zorder=3,
              label="Efecto acumulado")

    ax3.axhline(0, color="#9E9E9E", lw=1, ls="--", zorder=2)
    ax3.axvline(n_pre - 0.5, color=C_INTERV, lw=1.8, zorder=5)

    ax3.set_ylabel("Efecto acumulado", fontsize=10, color=C_TEXT)
    ax3.set_title("Efecto acumulado del tratamiento",
                   fontsize=11, fontweight="bold", color=C_TEXT,
                   pad=10)
    ax3.legend(loc="upper left", fontsize=8, ncol=2,
                framealpha=0.95, edgecolor=C_GRID,
                facecolor=BG)
    _style_ax_light(ax3, C_GRID, C_TEXT, BG)

    # Fechas SOLO aquí
    _apply_x_axis(ax3, fechas, n_total, show_label=False,
                   rotation=25, fontsize=8)

    # ==========================================================
    # Layout limpio · SIN subtítulo de métricas
    # ==========================================================
    fig.subplots_adjust(
        top=0.95,
        bottom=0.10,
        left=0.08,
        right=0.97,
        hspace=0.45,
    )

    return fig

# ==================================================================
# HELPERS DE PLOT
# ==================================================================
def _style_ax_white(ax, color_grid, color_text):
    """Aplica estilo blanco + grid suave."""
    ax.grid(True, color=color_grid, alpha=0.7, linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(color_grid)
        spine.set_linewidth(0.8)
    ax.tick_params(colors=color_text, labelsize=9)


def _get_fechas(winner):
    """Intenta extraer las fechas del wide_target."""
    try:
        wide = winner.get("_wide")
        if wide is not None and "Fecha_Analisis" in wide.columns:
            return pd.to_datetime(wide["Fecha_Analisis"]).values
    except Exception:
        pass
    return None


def _apply_x_axis(ax, fechas, n_total, show_label=False,
                   rotation=25, fontsize=7):
    """
    Aplica ticks de fecha al eje X.

    rotation y fontsize son configurables para que las fechas
    ocupen menos espacio vertical cuando hay muchas.
    """
    if fechas is not None and len(fechas) == n_total:
        n_ticks = min(10, n_total)
        step = max(1, n_total // n_ticks)
        ticks = list(range(0, n_total, step))
        labels = [pd.Timestamp(fechas[i]).strftime("%Y-%m-%d")
                   for i in ticks]
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels,
                            rotation=rotation,
                            ha="right",
                            fontsize=fontsize)
        # Reducir padding vertical de los ticks
        ax.tick_params(axis="x", pad=2)
    if not show_label:
        ax.set_xlabel("")
# ==================================================================
# TABLA EJECUTIVA
# ==================================================================
def _build_executive_table(results):
    def rounded(value, digits):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        return round(number, digits) if np.isfinite(number) else "—"

    def percent(value, digits=2):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "—"
        return f"{number:.{digits}f}%" if np.isfinite(number) else "—"

    rows = []
    for r in results:
        p = rounded(r.get("P_Valor"), 4)
        p_numeric = (float(p) if isinstance(p, (int, float, np.number))
                     else np.nan)
        metrics = r.get("Metricas_Backtest_PRE", {})
        inc_prob = rounded(r.get("Inc_Prob"), 6)
        pip = (percent(float(inc_prob) * 100, 1)
               if isinstance(inc_prob, (int, float, np.number)) else "—")
        rows.append({
            "KPI": r.get("KPI", "—"),                # ← nueva
            "Target": r["Target"],
            "Volumen": rounded(r.get("Volumen"), 2),
            "Num. Controles": r["Num_Controles"],
            "Efecto Absoluto (Acum)": rounded(
                r.get("Efecto_Absoluto"), 2),
            "Efecto Relativo": percent(r.get("Efecto_Relativo")),
            "P-Valor": p,
            "Significancia": ("Significativo" if p_numeric <= 0.10 else
                              "No signif." if np.isfinite(p_numeric) else
                              "No disponible"),
            "R Pre (Pearson)": rounded(r.get("R_Pre"), 3),
            "PIP": pip,
            "Controles Activos": _format_control_correlations(r),
            "Controles descartados": "; ".join(
                f"{item['Control']}: {item['Motivo']}"
                for item in r.get("Controles_Descartados", [])) or "—",
            "RMSE PRE OOS": rounded(metrics.get("RMSE_OOS"), 4),
            "MAE PRE OOS": rounded(metrics.get("MAE_OOS"), 4),
            "Mejora vs baseline PRE (%)": rounded(
                metrics.get("Improvement_vs_baseline_pct"), 2),
            "Backend": r.get("Backend", "—"),
            "Limitación del backend": r.get("Advertencia_Backend", "—"),
            "Diagnóstico de calidad": r.get("Diagnostico_Calidad", "—"),
            "Estacionalidad PRE": r.get("Estacionalidad_PRE", "—"),
            "Diagnóstico estacionalidad": r.get(
                "Diagnostico_Estacionalidad_PRE", "—"),
            "Calidad": r.get("quality_status", "—"),
        })
    # Orden: primero por KPI, luego por volumen
    table = pd.DataFrame(rows)
    table["_Volumen_orden"] = pd.to_numeric(
        table["Volumen"], errors="coerce")
    return (table.sort_values(["KPI", "_Volumen_orden"],
                              ascending=[True, False])
                 .drop(columns="_Volumen_orden")
                 .reset_index(drop=True))


def _format_control_correlations(result):
    """Devuelve cada control seguido de su correlación Pearson PRE."""
    controls = [c.strip() for c in str(result.get("Controles", "")).split(
        CONTROLS_SEP) if c.strip()]
    correlations = result.get("Correlaciones_Controles") or {}
    rendered = []
    for control in controls:
        value = correlations.get(control)
        if value is None or not np.isfinite(value):
            rendered.append(control)
        else:
            rendered.append(f"{control} ({float(value):.2f})")
    return CONTROLS_SEP.join(rendered)

# ==================================================================
# HELPERS
# ==================================================================
def _parse_tasks(text):
    if not text:
        return []
    tasks = []
    for line in str(text).split("\n"):
        line = line.strip()
        if not line:
            continue
        task = {}
        for filt in line.split(";"):
            filt = filt.strip()
            if "=" not in filt:
                continue
            col, vals = filt.split("=", 1)
            col = col.strip()
            vals = [v.strip() for v in vals.split("|") if v.strip()]
            if col and vals:
                task[col] = vals
        if task:
            tasks.append(task)
    return tasks


def _task_to_name(task):
    parts = []
    for col, vals in task.items():
        parts.extend(vals)
    return " | ".join(parts) if parts else "Tarea"


def _matches(dims, task):
    for col, allowed in task.items():
        if col not in dims:
            return False
        if str(dims[col]) not in [str(v) for v in allowed]:
            return False
    return True


def _build_wide(work, date_col, kpi_col, dim_cols, granularidad):
    df = work.copy()
    if granularidad == "semanal":
        df["_p"] = df[date_col].dt.to_period("W-SUN").dt.start_time
    elif granularidad == "mensual":
        df["_p"] = df[date_col].dt.to_period("M").dt.start_time
    else:
        df["_p"] = df[date_col].dt.normalize()

    if dim_cols:
        df["_dim"] = (df[dim_cols].fillna("(vacío)").astype(str)
                       .agg(" | ".join, axis=1))
    else:
        df["_dim"] = "total"

    dim_map = {}
    for name, g in df.groupby("_dim"):
        row = g.iloc[0]
        dim_map[name] = {c: str(row[c]) for c in dim_cols}

    wide = (df.groupby(["_p", "_dim"])[kpi_col]
              .sum().unstack(fill_value=0)
              .reset_index()
              .rename(columns={"_p": "Fecha_Analisis"}))
    wide.columns.name = None
    return wide, dim_map


def _auto_date(df):
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c
    return None


def _snap_period(ts, available, mode="nearest"):
    if len(available) == 0:
        return ts
    arr = pd.DatetimeIndex(available).sort_values()
    ts = pd.Timestamp(ts)
    if mode == "floor":
        s = arr[arr <= ts]
        return s[-1] if len(s) else arr[0]
    if mode == "ceil":
        s = arr[arr >= ts]
        return s[0] if len(s) else arr[-1]
    diffs = (arr - ts).to_series().abs()
    return arr[diffs.values.argmin()]

def plot_distribution_boxplot(distribution, target_name):
    """
    Boxplot + jitter de los efectos relativos por combo.

    Color y opacidad reflejan la significancia:
      - p ≤ 0.01        → azul intenso + borde (99% sig.)
      - 0.01 < p ≤ 0.05 → azul fuerte + borde (95% sig.)
      - 0.05 < p ≤ 0.10 → azul medio (90% sig.)
      - 0.10 < p ≤ 0.20 → gris azulado (80% sig.)
      - p > 0.20        → gris muy transparente
    """
    fig = Figure(figsize=(10, 5.5), dpi=100, facecolor="#F5F5F5")
    fig._mmm_keep_style = True
    ax = fig.add_subplot(111)

    if not distribution:
        ax.text(0.5, 0.5, "Sin datos de distribución",
                ha="center", va="center", fontsize=11)
        ax.axis("off")
        return fig

    df = pd.DataFrame(distribution)

    if df.empty or "Efecto_Relativo" not in df.columns:
        ax.text(0.5, 0.5, "Sin datos válidos",
                ha="center", va="center", fontsize=11)
        ax.axis("off")
        return fig

    effects = df["Efecto_Relativo"].values.astype(float)
    p_vals = df["P_Valor"].values.astype(float)

    # ==========================================================
    # Colormap por p-value
    # ==========================================================
    def sig_to_style(p):
        if not np.isfinite(p):
            return "#8B949E", 0.20
        if p <= 0.01:
            return "#1F6FEB", 1.0
        elif p <= 0.05:
            return "#1F6FEB", 0.85
        elif p <= 0.10:
            return "#5A8ABF", 0.65
        elif p <= 0.20:
            return "#8B949E", 0.40
        else:
            return "#8B949E", 0.15

    styles = [sig_to_style(p) for p in p_vals]
    colors = [s[0] for s in styles]
    alphas = [s[1] for s in styles]

    # ==========================================================
    # Boxplot de los significativos (p ≤ 0.05)
    # ==========================================================
    sig_mask = p_vals <= 0.05
    sig_effects = effects[sig_mask]

    if len(sig_effects) >= 3:
        bp = ax.boxplot(
            sig_effects,
            positions=[0],
            widths=0.5,
            patch_artist=True,
            showfliers=False,
        )
        for patch in bp["boxes"]:
            patch.set_facecolor("#A8D5F7")
            patch.set_alpha(0.35)
            patch.set_edgecolor("#1F6FEB")
            patch.set_linewidth(1.5)
        for element in ("whiskers", "caps", "medians"):
            for line in bp[element]:
                line.set_color("#1F6FEB")
                line.set_linewidth(1.2)

    # ==========================================================
    # Jitter
    # ==========================================================
    rng = np.random.default_rng(42)
    x = rng.uniform(-0.18, 0.18, size=len(effects))

    for xi, yi, col, alp, p in zip(x, effects, colors, alphas, p_vals):
        edge = "#1F6FEB" if p <= 0.05 else "white"
        edge_w = 1.0 if p <= 0.05 else 0.6
        ax.scatter(xi, yi, s=55, color=col, alpha=alp,
                    edgecolor=edge, linewidth=edge_w, zorder=5)

    ax.axhline(0, color="#8B949E", lw=1, ls="--", alpha=0.6, zorder=1)

    # ==========================================================
    # Estilo
    # ==========================================================
    ax.set_xticks([0])
    ax.set_xticklabels(["Simulaciones MCMC / combos"], fontsize=9)
    ax.set_ylabel("Efecto Relativo (%)", fontsize=11)
    ax.set_title(f"Distribución de modelos · {target_name}",
                 fontsize=12, fontweight="bold", pad=10)
    ax.grid(True, axis="y", alpha=0.3)

    # ==========================================================
    # Leyenda de significancia
    # ==========================================================
    from matplotlib.patches import Patch
    handles = [
        Patch(facecolor="#1F6FEB", alpha=1.0,
              label="p ≤ 0.01  (99% sig.)"),
        Patch(facecolor="#1F6FEB", alpha=0.85,
              label="0.01 < p ≤ 0.05  (95% sig.)"),
        Patch(facecolor="#5A8ABF", alpha=0.65,
              label="0.05 < p ≤ 0.10  (90% sig.)"),
        Patch(facecolor="#8B949E", alpha=0.40,
              label="0.10 < p ≤ 0.20  (80% sig.)"),
        Patch(facecolor="#8B949E", alpha=0.15,
              label="p > 0.20  (no signif.)"),
    ]
    ax.legend(handles=handles, loc="upper right",
              fontsize=8, title="Significancia", title_fontsize=9,
              framealpha=0.9)

    # Contador
    n_total = len(effects)
    n_sig05 = int(np.sum(p_vals <= 0.05))
    n_sig10 = int(np.sum((p_vals > 0.05) & (p_vals <= 0.10)))
    n_sig20 = int(np.sum((p_vals > 0.10) & (p_vals <= 0.20)))

    info = (f"{n_total} combos  ·  "
            f"{n_sig05} con p≤0.05  ·  "
            f"{n_sig10} con p≤0.10  ·  "
            f"{n_sig20} con p≤0.20")

    fig.text(0.5, 0.01, info, ha="center",
              fontsize=9, style="italic", color="#666")

    fig.tight_layout(rect=[0, 0.03, 1, 1])
    return fig

def plot_ci_panel_series(winner, target_name,
                           fecha_campana, fecha_fin, alpha=0.05):
    """Panel 1 individual: Serie observada vs contrafactual."""
    fit = winner.get("_fit") if isinstance(winner, dict) else None

    BG = "#F5F5F5"
    fig = Figure(figsize=(12, 6.5), dpi=100, facecolor=BG)
    fig._mmm_keep_style = True

    if fit is None:
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "Sin datos del modelo",
                ha="center", va="center", fontsize=12)
        ax.axis("off")
        return fig

    y_actual = np.asarray(fit.get("y_actual", []), dtype=float)
    y_pred = np.asarray(fit.get("y_pred", []), dtype=float)
    y_lower = np.asarray(fit.get("y_lower", []), dtype=float)
    y_upper = np.asarray(fit.get("y_upper", []), dtype=float)
    n_pre = int(fit.get("n_pre", 0))
    n_total = len(y_actual)

    if n_total == 0:
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "Sin datos",
                ha="center", va="center", fontsize=12)
        ax.axis("off")
        return fig

    x = np.arange(n_total)
    fechas = _get_fechas(winner)

    C_OBS = "#0D47A1"
    C_PRED = "#C62828"
    C_CI = "#EF5350"
    C_POST = "#FFF3E0"
    C_INTERV = "#D32F2F"
    C_GRID = "#D5D5D5"
    C_TEXT = "#212121"

    ax = fig.add_subplot(111)
    ax.set_facecolor(BG)

    ax.axvspan(n_pre - 0.5, n_total - 0.5,
                 color=C_POST, alpha=0.7, zorder=0,
                 label="Periodo POST")

    ax.fill_between(x, y_lower, y_upper,
                      color=C_CI, alpha=0.18, zorder=1,
                      label=f"IC {(1-alpha)*100:.0f}%")
    ax.plot(x, y_pred, color=C_PRED, lw=2.5, ls="--",
              label="Contrafactual", zorder=3)
    ax.plot(x, y_actual, color=C_OBS, lw=2.8,
              label="Observado", zorder=4)
    ax.scatter(x, y_actual, s=18, color=C_OBS,
                edgecolor="white", linewidth=0.8, zorder=5)
    ax.axvline(n_pre - 0.5, color=C_INTERV, lw=2.0,
                 alpha=0.9, zorder=6, label="Intervención")

    ax.set_ylabel("KPI", fontsize=12, color=C_TEXT)
    ax.set_title(f"Serie observada vs contrafactual · {target_name}",
                   fontsize=13, fontweight="bold", color=C_TEXT,
                   pad=12)
    ax.legend(loc="upper left", fontsize=10, ncol=4,
                framealpha=0.95, edgecolor=C_GRID,
                facecolor=BG)

    _style_ax_light(ax, C_GRID, C_TEXT, BG)
    _apply_x_axis(ax, fechas, n_total, show_label=False,
                   rotation=25, fontsize=8)

    fig.subplots_adjust(
        top=0.92,
        bottom=0.12,
        left=0.08,
        right=0.97,
    )
    return fig

def plot_ci_panel_effect(winner, target_name,
                           fecha_campana, fecha_fin, alpha=0.05):
    """Panel 2 individual: Efecto puntual."""
    fit = winner.get("_fit") if isinstance(winner, dict) else None

    BG = "#F5F5F5"
    fig = Figure(figsize=(12, 6.5), dpi=100, facecolor=BG)
    fig._mmm_keep_style = True

    if fit is None:
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "Sin datos del modelo",
                ha="center", va="center", fontsize=12)
        ax.axis("off")
        return fig

    y_pred = np.asarray(fit.get("y_pred", []), dtype=float)
    y_lower = np.asarray(fit.get("y_lower", []), dtype=float)
    y_upper = np.asarray(fit.get("y_upper", []), dtype=float)
    effect = np.asarray(fit.get("effect", []), dtype=float)
    n_pre = int(fit.get("n_pre", 0))
    n_total = len(effect)

    if n_total == 0:
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "Sin datos",
                ha="center", va="center", fontsize=12)
        ax.axis("off")
        return fig

    x = np.arange(n_total)
    fechas = _get_fechas(winner)

    C_EFFECT = "#2E7D32"
    C_EFFECT_CI = "#66BB6A"
    C_POST = "#FFF3E0"
    C_INTERV = "#D32F2F"
    C_GRID = "#D5D5D5"
    C_TEXT = "#212121"

    ax = fig.add_subplot(111)
    ax.set_facecolor(BG)

    ax.axvspan(n_pre - 0.5, n_total - 0.5,
                 color=C_POST, alpha=0.7, zorder=0,
                 label="Periodo POST")

    effect_lower = effect - (y_upper - y_pred)
    effect_upper = effect + (y_pred - y_lower)

    ax.fill_between(x, effect_lower, effect_upper,
                      color=C_EFFECT_CI, alpha=0.28, zorder=1,
                      label=f"IC {(1-alpha)*100:.0f}%")
    ax.plot(x, effect, color=C_EFFECT, lw=2.2, zorder=3,
              label="Efecto puntual")
    ax.scatter(x, effect, s=25, color=C_EFFECT,
                edgecolor="white", linewidth=0.8, zorder=4)

    ax.axhline(0, color="#616161", lw=1.2, ls="--", zorder=2)
    ax.axvline(n_pre - 0.5, color=C_INTERV, lw=2.0,
                 alpha=0.9, zorder=6, label="Intervención")

    ax.set_ylabel("Efecto puntual", fontsize=12, color=C_TEXT)
    ax.set_title(f"Efecto puntual semana a semana · {target_name}",
                   fontsize=13, fontweight="bold", color=C_TEXT,
                   pad=12)
    ax.legend(loc="upper left", fontsize=10, ncol=3,
                framealpha=0.95, edgecolor=C_GRID,
                facecolor=BG)

    _style_ax_light(ax, C_GRID, C_TEXT, BG)
    _apply_x_axis(ax, fechas, n_total, show_label=False,
                   rotation=25, fontsize=8)

    fig.subplots_adjust(
        top=0.92,
        bottom=0.12,
        left=0.08,
        right=0.97,
    )
    return fig

def plot_ci_panel_cumulative(winner, target_name,
                               fecha_campana, fecha_fin, alpha=0.05):
    """Panel 3 individual: Efecto acumulado."""
    fit = winner.get("_fit") if isinstance(winner, dict) else None

    BG = "#F5F5F5"
    fig = Figure(figsize=(12, 6.5), dpi=100, facecolor=BG)
    fig._mmm_keep_style = True

    if fit is None:
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "Sin datos del modelo",
                ha="center", va="center", fontsize=12)
        ax.axis("off")
        return fig

    y_pred = np.asarray(fit.get("y_pred", []), dtype=float)
    y_lower = np.asarray(fit.get("y_lower", []), dtype=float)
    y_upper = np.asarray(fit.get("y_upper", []), dtype=float)
    effect = np.asarray(fit.get("effect", []), dtype=float)
    n_pre = int(fit.get("n_pre", 0))
    n_total = len(effect)

    if n_total == 0:
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "Sin datos",
                ha="center", va="center", fontsize=12)
        ax.axis("off")
        return fig

    x = np.arange(n_total)
    fechas = _get_fechas(winner)

    C_EFFECT = "#2E7D32"
    C_EFFECT_CI = "#66BB6A"
    C_POST = "#FFF3E0"
    C_INTERV = "#D32F2F"
    C_GRID = "#D5D5D5"
    C_TEXT = "#212121"

    ax = fig.add_subplot(111)
    ax.set_facecolor(BG)

    cum_effect = np.cumsum(effect)
    resid_var = ((y_upper - y_lower) / (2 * 1.96)) ** 2
    cum_se = np.sqrt(np.cumsum(resid_var))
    cum_lower = cum_effect - 1.96 * cum_se
    cum_upper = cum_effect + 1.96 * cum_se

    cum_e_post = np.full(n_total, np.nan)
    cum_e_post[n_pre:] = cum_effect[n_pre:]
    cum_lo_post = np.full(n_total, np.nan)
    cum_lo_post[n_pre:] = cum_lower[n_pre:]
    cum_hi_post = np.full(n_total, np.nan)
    cum_hi_post[n_pre:] = cum_upper[n_pre:]

    ax.axvspan(n_pre - 0.5, n_total - 0.5,
                 color=C_POST, alpha=0.7, zorder=0,
                 label="Periodo POST")

    ax.fill_between(x, cum_lo_post, cum_hi_post,
                      color=C_EFFECT_CI, alpha=0.28, zorder=1,
                      label=f"IC {(1-alpha)*100:.0f}%")
    ax.plot(x, cum_e_post, color=C_EFFECT, lw=2.5, zorder=3,
              label="Efecto acumulado")
    ax.scatter(x[n_pre:], cum_e_post[n_pre:], s=28,
                color=C_EFFECT, edgecolor="white",
                linewidth=0.8, zorder=4)

    ax.axhline(0, color="#616161", lw=1.2, ls="--", zorder=2)
    ax.axvline(n_pre - 0.5, color=C_INTERV, lw=2.0,
                 alpha=0.9, zorder=6, label="Intervención")

    ax.set_ylabel("Efecto acumulado", fontsize=12, color=C_TEXT)
    ax.set_title(f"Efecto acumulado del tratamiento · {target_name}",
                   fontsize=13, fontweight="bold", color=C_TEXT,
                   pad=12)
    ax.legend(loc="upper left", fontsize=10, ncol=3,
                framealpha=0.95, edgecolor=C_GRID,
                facecolor=BG)

    _style_ax_light(ax, C_GRID, C_TEXT, BG)
    _apply_x_axis(ax, fechas, n_total, show_label=False,
                   rotation=25, fontsize=8)

    fig.subplots_adjust(
        top=0.92,
        bottom=0.12,
        left=0.08,
        right=0.97,
    )
    return fig

def _style_ax_light(ax, color_grid, color_text, bg_color):
    """
    Aplica estilo con fondo grisáceo claro + grid suave.
    Sustituye a _style_ax_white para los gráficos CI.
    """
    ax.set_facecolor(bg_color)
    ax.grid(True, color=color_grid, alpha=0.6, linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(color_grid)
        spine.set_linewidth(0.8)
    ax.tick_params(colors=color_text, labelsize=9)
