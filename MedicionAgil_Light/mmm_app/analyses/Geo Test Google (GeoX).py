"""Diseño de geo-experimento con Meridian GeoX.

Extrae EXHAUSTIVAMENTE todos los atributos disponibles en el objeto
de resultados, sin depender de nombres específicos.
"""

import datetime
import time
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.patches import Patch

NAME = "Diseño de geo-experimento (GeoX)"
DESCRIPTION = "Asigna regiones a control/tratamiento y extrae todos los resultados"
CATEGORY = "MMM"
CUSTOM_DIALOG = "GeoXDialog"


# ------------------------------------------------------------------
# CLAVES
# ------------------------------------------------------------------
_REGION_KEYS = ("region", "región", "location", "geo", "provincia",
                "state", "ciudad", "city", "municipio", "dma", "zona")
_KPI_KEYS = ("engagedsessions", "engaged_sessions", "sessions",
             "conversions", "conversiones", "ventas", "sales",
             "revenue", "totalrevenue", "gmv", "kpi", "leads", "checkouts")
_MARKET_KEYS = ("destination_country", "country", "pais", "país",
                "market", "mercado", "region_country")


# ==================================================================
# SCHEMA
# ==================================================================
def get_config_schema(df: pd.DataFrame) -> dict:
    date_cols = [c for c in df.columns
                 if pd.api.types.is_datetime64_any_dtype(df[c])]
    if not date_cols:
        for c in df.columns:
            if any(k in str(c).lower() for k in ("fecha", "date", "dia", "day")):
                try:
                    pd.to_datetime(df[c], errors="raise")
                    date_cols.append(c)
                except Exception:
                    pass

    region_cols = [c for c in df.columns
                   if any(k in str(c).lower() for k in _REGION_KEYS)]
    market_cols = [c for c in df.columns
                   if any(k in str(c).lower() for k in _MARKET_KEYS)]
    num_cols = df.select_dtypes("number").columns.tolist()
    kpi_cols = [c for c in num_cols
                if any(k in str(c).lower() for k in _KPI_KEYS)] or num_cols

    # Estos valores se corresponden con Meridian GeoX 1.0.x. Mantenerlos
    # aquí evita presentar opciones que la librería no puede ejecutar.
    experiment_types = ["HOLDBACK", "GO_DARK", "HEAVY_UP"]
    # Meridian GeoX 1.0.1 solo implementa TBR en design.run_design.
    methodologies = ["TBR"]
    geo_rules = ["STRATIFIED_SAMPLING", "RANDOM"]

    def _filtrable(c):
        if c in date_cols:
            return False
        try:
            s = df[c]
        except Exception:
            return False
        if s.dtype == object or str(s.dtype) == "category":
            return True
        if pd.api.types.is_numeric_dtype(s):
            return s.nunique(dropna=True) <= 200
        if pd.api.types.is_bool_dtype(s):
            return True
        return False

    filterable = [str(c) for c in df.columns if _filtrable(c)]

    fields = [
        {"key": "date_col", "label": "Columna de fecha",
         "type": "select", "options": [str(c) for c in date_cols],
         "default": str(date_cols[0]) if date_cols else None},
        {"key": "region_col", "label": "Columna de región / geo",
         "type": "select",
         "options": [str(c) for c in region_cols] or [str(c) for c in df.columns],
         "default": str(region_cols[0]) if region_cols else None},
        {"key": "kpi_col", "label": "KPI a analizar",
         "type": "select", "options": [str(c) for c in kpi_cols],
         "default": str(kpi_cols[0]) if kpi_cols else None},
        {"key": "market_col", "label": "Columna de mercado (opcional)",
         "type": "select",
         "options": ["(ninguna)"] + [str(c) for c in market_cols],
         "default": "(ninguna)"},
        {"key": "market_value", "label": "Valor de mercado a filtrar",
         "hint": "vacío = no filtrar", "type": "text", "default": ""},
        {"key": "duration_days", "label": "Duración del experimento (días)",
         "hint": "vacío = 14", "type": "number", "default": 14},
        {"key": "budget", "label": "Presupuesto por celda",
         "hint": "vacío = 50000", "type": "number", "default": 50000},
        {"key": "cost_per_incremental_conversion",
         "label": "Coste por conversión incremental",
         "hint": "vacío = 1.0", "type": "number", "default": 1.0},
        {"key": "design_output_count", "label": "Nº de diseños a generar",
         "hint": "vacío = 5", "type": "number", "default": 5},
        {"key": "cell_count", "label": "Nº de celdas",
         "hint": "vacío = 1", "type": "number", "default": 1},
        {"key": "experiment_type", "label": "Tipo de experimento",
         "type": "select", "options": experiment_types,
         "default": experiment_types[0]},
        {"key": "methodology", "label": "Metodología",
         "type": "select", "options": methodologies,
         "default": methodologies[0]},
        {"key": "geo_assignment_rule", "label": "Regla de asignación",
         "type": "select", "options": geo_rules,
         "default": geo_rules[0]},
        {"key": "filters", "label": "Filtros adicionales",
         "type": "filters", "options": filterable},
    ]

    return {"title": "Configuración · GeoX", "fields": fields}


# ==================================================================
# RUN
# ==================================================================
def run(df: pd.DataFrame,
        date_col=None, region_col=None, kpi_col=None,
        market_col=None, market_value=None,
        top_n=None, duration_days=None, budget=None,
        cost_per_incremental_conversion=None,
        design_output_count=None, cell_count=None,
        experiment_type=None, methodology=None,
        geo_assignment_rule=None,
        included_control_geos=None, excluded_geos=None,
        max_conversions_percent=None, alpha=None, power=None,
        test_type=None, n_candidates=None, n_ranked_candidates=None,
        seed=None, slope_tolerance=None, min_r2=None,
        num_strata=None, k_means_iterations=None,
        filters=None, progress_callback=None, **kwargs) -> dict:

    _emit_progress(progress_callback, 2, "Preparando Geo Test...")

    DEFAULTS = {
        "duration_days": 14, "budget": 50000,
        "cost_per_incremental_conversion": 1.0,
        "design_output_count": 5, "cell_count": 1,
        "experiment_type": "HOLDBACK", "methodology": "TBR",
        "geo_assignment_rule": "STRATIFIED_SAMPLING",
        "max_conversions_percent": 0.30, "alpha": 0.10,
        "power": 0.80, "test_type": "TWO_SIDED",
        "n_candidates": 100000, "n_ranked_candidates": 100,
        "seed": 42, "slope_tolerance": 0.20, "min_r2": 0.80,
        "num_strata": 4, "k_means_iterations": 10,
    }

    def _val(v, key):
        return DEFAULTS[key] if (v is None or v == "") else v

    duration_days = int(_val(duration_days, "duration_days"))
    budget = float(_val(budget, "budget"))
    cost_per_incremental_conversion = float(
        _val(cost_per_incremental_conversion,
             "cost_per_incremental_conversion"))
    design_output_count = int(_val(design_output_count, "design_output_count"))
    cell_count = int(_val(cell_count, "cell_count"))
    experiment_type = _val(experiment_type, "experiment_type")
    methodology = _val(methodology, "methodology")
    if str(methodology).upper() != "TBR":
        return {"Estado": "ERROR · metodología no soportada",
                "Detalle": ("Meridian GeoX 1.0.1 solo implementa TBR para "
                            "run_design. Selecciona TBR para el diseño PRE-test.")}
    geo_assignment_rule = _val(geo_assignment_rule, "geo_assignment_rule")
    max_conversions_percent = float(_val(
        max_conversions_percent, "max_conversions_percent"))
    alpha = float(_val(alpha, "alpha"))
    power = float(_val(power, "power"))
    test_type = _val(test_type, "test_type")
    n_candidates = int(_val(n_candidates, "n_candidates"))
    n_ranked_candidates = int(_val(n_ranked_candidates, "n_ranked_candidates"))
    seed = int(_val(seed, "seed"))
    slope_tolerance = float(_val(slope_tolerance, "slope_tolerance"))
    min_r2 = float(_val(min_r2, "min_r2"))
    num_strata = int(_val(num_strata, "num_strata"))
    k_means_iterations = int(_val(k_means_iterations, "k_means_iterations"))
    included_control_geos = _normalise_geo_list(included_control_geos)
    excluded_geos = _normalise_geo_list(excluded_geos)

    t0 = time.time()

    def log(msg):
        print(msg, flush=True)

    log("=" * 60)
    log("[GeoX] Inicio")
    log(f"[GeoX] dur={duration_days}d budget={budget} "
        f"cost={cost_per_incremental_conversion} designs={design_output_count} "
        f"cells={cell_count}")
    log(f"[GeoX] exp={experiment_type} method={methodology} rule={geo_assignment_rule}")
    log("=" * 60)

    # ---------- Import ----------
    log("[GeoX] Importando meridian_geox...")
    _emit_progress(progress_callback, 8, "Cargando motor GeoX...")
    try:
        import meridian_geox as geox
        log(f"[GeoX]   OK · versión: {getattr(geox, '__version__', '?')}")
    except Exception as e:
        return {"Estado": "ERROR · import meridian_geox",
                "Detalle": str(e),
                "Solución": "pip install meridian-geox"}

    # ---------- Filtros ----------
    if filters:
        for col, allowed in filters.items():
            if col not in df.columns or not allowed:
                continue
            n0 = len(df)
            df = df[df[col].astype(str).isin([str(v) for v in allowed])].copy()
            log(f"[GeoX]   filtro '{col}': {n0} → {len(df)}")
        if df.empty:
            return {"Estado": "ERROR · filtros vacíos"}

    # ---------- Autodetección ----------
    def _auto(keys, numeric_only=False):
        pool = (df.select_dtypes("number").columns
                if numeric_only else df.columns)
        for c in pool:
            if any(k in str(c).lower() for k in keys):
                return c
        return None

    if not date_col or date_col not in df.columns:
        date_col = _auto(("fecha", "date", "dia", "day"))
    if not region_col or region_col not in df.columns:
        region_col = _auto(_REGION_KEYS)
    if not kpi_col or kpi_col not in df.columns:
        kpi_col = _auto(_KPI_KEYS, numeric_only=True)
    if (not market_col or market_col in ("", "(ninguna)")
            or market_col not in df.columns):
        market_col = None

    log(f"[GeoX] Fecha={date_col} Región={region_col} KPI={kpi_col} "
        f"Mercado={market_col} ('{market_value}')")

    if not date_col or not region_col or not kpi_col:
        return {"Estado": "ERROR · faltan columnas",
                "Detectado": {"fecha": date_col, "región": region_col,
                              "KPI": kpi_col}}

    # ---------- Normalización ----------
    _emit_progress(progress_callback, 16, "Normalizando fechas y regiones...")
    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work = work.dropna(subset=[date_col])
    numeric_kpi = pd.to_numeric(work[kpi_col], errors="coerce")
    invalid_kpi = work[kpi_col].notna() & numeric_kpi.isna()
    if invalid_kpi.any():
        return {"Estado": "ERROR · KPI no numérico",
                "Detalle": (f"'{kpi_col}' contiene {int(invalid_kpi.sum())} "
                            "valor(es) no convertibles. Corrige el dataset "
                            "antes de ejecutar GeoX.")}
    missing_kpi = numeric_kpi.isna()
    if missing_kpi.any():
        return {"Estado": "ERROR · KPI con valores ausentes",
                "Detalle": (f"'{kpi_col}' tiene {int(missing_kpi.sum())} "
                            "observaciones ausentes. GeoX distingue ausencia "
                            "de un cero observado; completa o excluye esos "
                            "registros antes de continuar.")}
    work[kpi_col] = numeric_kpi
    work[region_col] = (work[region_col].fillna("(vacío)")
                                        .astype(str).str.strip())

    if market_col and market_value:
        work = work[work[market_col].astype(str).str.lower()
                    == str(market_value).lower()].copy()
        log(f"[GeoX] Filtro mercado '{market_value}': {len(work)} filas")
        if work.empty:
            return {"Estado": f"ERROR · sin datos para '{market_value}'"}

    work = work[~work[region_col].str.lower().isin(
        ["(not set)", "", "nan", "none", "unknown", "(vacío)"])]
    log(f"[GeoX] Tras limpiar: {len(work)} filas")

    # ---------- Panel ----------
    _emit_progress(progress_callback, 28, "Construyendo panel geográfico...")
    panel = (work.groupby([date_col, region_col], as_index=False)
                  .agg({kpi_col: "sum"})
                  .rename(columns={date_col: "date",
                                    region_col: "location",
                                    kpi_col: "conversions"}))
    panel["conversions"] = panel["conversions"].astype("float64")
    panel = panel.sort_values(["location", "date"]).reset_index(drop=True)

    gran = _detect_granularity(panel)
    log(f"[GeoX] Granularidad: {gran}")
    if gran != "D":
        return {"Estado": "ERROR · GeoX requiere datos diarios reales",
                "Detalle": (f"La granularidad detectada es {gran}. No se "
                            "prorratearán observaciones no diarias; carga "
                            "una observación diaria real por geo.")}

    dates = pd.DatetimeIndex(pd.to_datetime(panel["date"]).unique()).sort_values()
    if (not dates.equals(dates.normalize())
            or (len(dates) > 1 and
                not (dates[1:] - dates[:-1] == pd.Timedelta(days=1)).all())):
        return {"Estado": "ERROR · serie no diaria o con fechas ausentes",
                "Detalle": ("Las fechas deben ser días consecutivos reales, "
                            "sin huecos ni horas intradía. No se crearán "
                            "observaciones artificiales ni se imputarán "
                            "huecos como cero.")}

    expected_dates = set(pd.to_datetime(panel["date"]).unique())
    observed_dates = panel.groupby("location")["date"].agg(
        lambda values: set(pd.to_datetime(values).unique()))
    incomplete = observed_dates[
        observed_dates.map(lambda values: values != expected_dates)]
    if not incomplete.empty:
        return {"Estado": "ERROR · panel geo-día incompleto",
                "Detalle": ("Faltan observaciones diarias para algunas "
                            "regiones; no se imputarán como cero. Regiones: "
                            + ", ".join(map(str, incomplete.index[:10])))}

    n_days = panel["date"].nunique()
    log(f"[GeoX] Días: {n_days}")
    if n_days < 7:
        return {"Estado": "ERROR · pocos días",
                "Detalle": f"Solo {n_days} días."}

    geo_volume = panel.groupby("location")["conversions"].sum()
    available_geos = set(geo_volume.index.astype(str))
    unknown_control = sorted(set(included_control_geos) - available_geos)
    unknown_excluded = sorted(set(excluded_geos) - available_geos)
    overlap = sorted(set(included_control_geos) & set(excluded_geos))
    if overlap:
        return {"Estado": "ERROR · regiones incompatibles",
                "Detalle": "Una región no puede ser control fijo y estar excluida: "
                           + ", ".join(overlap)}
    if unknown_control or unknown_excluded:
        missing = unknown_control + unknown_excluded
        return {"Estado": "ERROR · regiones no encontradas",
                "Detalle": "No existen en los datos filtrados: " + ", ".join(missing)}

    # El volumen no define el universo elegible; la calidad y restricciones
    # posteriores las gestiona GeoX.
    valid = geo_volume[geo_volume > 0].index.astype(str).tolist()
    # Un control fijado por el usuario no debe desaparecer solo porque no
    # esté entre las primeras Top N regiones.
    valid.extend(geo for geo in included_control_geos
                 if geo_volume.get(geo, 0) > 0 and geo not in valid)
    valid = [geo for geo in valid if geo not in excluded_geos]
    log(f"[GeoX] Regiones válidas: {len(valid)}")
    if len(valid) < 4:
        return {"Estado": "ERROR · pocas regiones", "Regiones": valid}

    panel_clean = panel[panel["location"].isin(valid)].copy()

    # ---------- Config + run ----------
    log("[GeoX] Ejecutando run_design...")
    _emit_progress(
        progress_callback, 38,
        f"Evaluando diseños para {len(valid)} regiones...")
    try:
        exp_type = _get_enum(geox, "ExperimentType", experiment_type)
        method = _get_enum(geox, "Methodology", methodology)
        rule = _get_enum(geox, "GeoAssignmentRule", geo_assignment_rule)
        test = _get_enum(geox, "TestType", test_type)
        design_config_kwargs = {
            "experiment_duration": datetime.timedelta(days=duration_days),
            "experiment_types": exp_type,
            "methodology": method,
            "geo_assignment_rule": rule,
            "cost_per_incremental_conversion": cost_per_incremental_conversion,
            "cell_count": cell_count,
            "design_output_count": design_output_count,
            "alpha": alpha,
            "power": power,
            "test_type": test,
            "n_candidates": n_candidates,
            "n_ranked_candidates": n_ranked_candidates,
            "seed": seed,
            "slope_tolerance": slope_tolerance,
            "num_strata": num_strata,
            "k_means_iterations": k_means_iterations,
        }

        def _make_design_config(r2_threshold):
            config_kwargs = dict(design_config_kwargs)
            config_kwargs["min_r2"] = r2_threshold
            return geox.DesignConfig(**config_kwargs)

        design_config = _make_design_config(min_r2)
        cell_budgets = {f"cell_{i+1}": geox.Budget(budget=budget)
                        for i in range(cell_count)}
        constraints = geox.Constraints(
            budget_constraint=cell_budgets,
            included_control_geos=set(included_control_geos),
            excluded_geos=set(excluded_geos),
            max_conversions_percent=max_conversions_percent,
        )
    except Exception as e:
        return {"Estado": "ERROR · config", "Detalle": str(e)}

    effective_min_r2 = min_r2
    t1 = time.time()
    design_call_kwargs = {
        "data": panel_clean,
        "design_config": design_config,
        "constraints": constraints,
    }
    quality_config_factory = getattr(geox, "QualityCheckConfig", None)
    if callable(quality_config_factory):
        design_call_kwargs["data_quality_check_config"] = quality_config_factory()
    try:
        design_results = geox.run_design(**design_call_kwargs)
    except Exception as first_error:
        if _is_min_r2_failure(first_error):
            return {
                "Estado": "ERROR · sin diseños válidos",
                "Detalle": str(first_error),
                "Solución": (
                    "GeoX no encontró diseños que alcancen el R² mínimo "
                    "solicitado. Revisa cobertura, duración, restricciones "
                    "y regiones; cualquier cambio de umbral debe ser manual."),
                "R² mínimo solicitado": min_r2,
                "quality_status": "No válida",
            }
        import traceback
        log(traceback.format_exc())
        return {"Estado": "ERROR · run_design", "Detalle": str(first_error)}

    elapsed = time.time() - t1
    log(f"[GeoX] run_design OK en {elapsed:.1f}s")
    _emit_progress(
        progress_callback, 76,
        "Diseños calculados; extrayendo resultados...")

    # =========================================================
    # EXTRACCIÓN EXHAUSTIVA
    # =========================================================
    log("[GeoX] Extrayendo TODOS los atributos...")

    # ------------------------------------------------------------------
    # Volcado recursivo del objeto
    # ------------------------------------------------------------------
    def _dump(obj, prefix="", max_depth=3, depth=0):
        """Recorre recursivamente un objeto y devuelve una lista de filas
        {Atributo, Tipo, Valor, Ruta}."""
        rows = []
        if depth > max_depth or obj is None:
            return rows
        try:
            attrs = dir(obj)
        except Exception:
            return rows

        skip = {"__class__", "__dict__", "__weakref__", "__module__",
                "__doc__", "__init__", "__new__", "__reduce__",
                "__reduce_ex__", "__getstate__", "__setstate__",
                "__sizeof__", "__dir__", "__format__", "__hash__",
                "__str__", "__repr__", "__eq__", "__ne__", "__lt__",
                "__le__", "__gt__", "__ge__", "__getattribute__",
                "__delattr__", "__setattr__", "__subclasshook__",
                "__init_subclass__", "__class_getitem__", "__annotations__"}

        for name in attrs:
            if name.startswith("__") or name in skip:
                continue
            try:
                value = getattr(obj, name)
            except Exception as e:
                rows.append({
                    "Atributo": f"{prefix}{name}",
                    "Tipo": "error",
                    "Valor": f"[No accesible: {e}]",
                })
                continue

            if callable(value):
                continue

            tipo = type(value).__name__

            # Aplanar listas / tuplas simples
            if isinstance(value, (list, tuple)):
                if len(value) <= 20 and all(
                        isinstance(v, (str, int, float, bool, type(None)))
                        for v in value):
                    rows.append({
                        "Atributo": f"{prefix}{name}",
                        "Tipo": tipo,
                        "Valor": str(list(value)),
                    })
                else:
                    rows.append({
                        "Atributo": f"{prefix}{name}",
                        "Tipo": tipo,
                        "Valor": f"[{len(value)} elementos]",
                    })
                    # Si los primeros son dicts o tuplas, expandimos
                    if value and isinstance(value[0], dict):
                        try:
                            df_sub = pd.DataFrame(value)
                            rows.append({
                                "Atributo": f"{prefix}{name} (como tabla)",
                                "Tipo": "DataFrame",
                                "Valor": df_sub,
                            })
                        except Exception:
                            pass
                continue

            # Dicts
            if isinstance(value, dict):
                if len(value) <= 50 and all(
                        isinstance(k, (str, int, float))
                        for k in value.keys()):
                    rows.append({
                        "Atributo": f"{prefix}{name}",
                        "Tipo": "dict",
                        "Valor": str(value)[:400],
                    })
                else:
                    rows.append({
                        "Atributo": f"{prefix}{name}",
                        "Tipo": "dict",
                        "Valor": f"[{len(value)} claves]",
                    })
                continue

            # DataFrames
            if isinstance(value, pd.DataFrame):
                rows.append({
                    "Atributo": f"{prefix}{name}",
                    "Tipo": "DataFrame",
                    "Valor": value,
                })
                continue

            # NumPy / pandas Series
            if hasattr(value, "shape") and hasattr(value, "dtype"):
                try:
                    shp = getattr(value, "shape", None)
                    if shp and len(shp) <= 2 and np.prod(shp) <= 2000:
                        try:
                            df_v = pd.DataFrame(value)
                            if df_v.shape[1] == 1:
                                df_v.columns = ["valor"]
                            rows.append({
                                "Atributo": f"{prefix}{name}",
                                "Tipo": "array",
                                "Valor": df_v,
                            })
                        except Exception:
                            rows.append({
                                "Atributo": f"{prefix}{name}",
                                "Tipo": "array",
                                "Valor": f"shape={shp}",
                            })
                    else:
                        rows.append({
                            "Atributo": f"{prefix}{name}",
                            "Tipo": "array",
                            "Valor": f"shape={shp}",
                        })
                except Exception:
                    pass
                continue

            # Escalares
            if isinstance(value, (int, float, bool, str, np.number)):
                # Recortamos strings largos
                v_str = str(value)
                if len(v_str) > 300:
                    v_str = v_str[:300] + "..."
                rows.append({
                    "Atributo": f"{prefix}{name}",
                    "Tipo": tipo,
                    "Valor": v_str,
                })
                continue

            # Objetos complejos: recursión
            rows.append({
                "Atributo": f"{prefix}{name}",
                "Tipo": tipo,
                "Valor": f"<objeto {tipo}>",
            })
            rows.extend(_dump(value, prefix=f"{prefix}{name}.",
                              max_depth=max_depth, depth=depth + 1))

        return rows

    # ------------------------------------------------------------------
    # Volcado del objeto raíz
    # ------------------------------------------------------------------
    rows_root = _dump(design_results, prefix="design_results.")
    df_root = pd.DataFrame(rows_root)
    _emit_progress(progress_callback, 82, "Resumen general extraído...")

    # ------------------------------------------------------------------
    # Extracción de los diseños
    # ------------------------------------------------------------------
    designs = getattr(design_results, "designs", None)
    if designs is None:
        _emit_progress(progress_callback, 100, "Geo Test completado")
        return {
            "Estado": "OK · sin 'designs' (solo dump raíz)",
            "Dump · design_results": df_root,
        }

    if isinstance(designs, dict):
        design_items = list(designs.items())
    else:
        design_items = [(f"Design {i+1}", d)
                        for i, d in enumerate(designs)]

    # DataFrame con TODOS los atributos de TODOS los diseños
    rows_designs = []
    for index, (name, d) in enumerate(design_items, start=1):
        for r in _dump(d, prefix=""):
            rows_designs.append({"Diseño": name, **r})
        _emit_progress(
            progress_callback,
            82 + int(9 * index / max(1, len(design_items))),
            f"Extrayendo diseño {index}/{len(design_items)}: {name}")
    df_designs = pd.DataFrame(rows_designs)

    # ------------------------------------------------------------------
    # Resumen tabular de control / tratamiento
    # ------------------------------------------------------------------
    all_geos = set(panel_clean["location"].unique())
    control_map, treat_map = {}, {}
    for name, d in design_items:
        ctrl = list(getattr(d, "control_geos", []) or [])
        trt = list(getattr(d, "treatment_geos", []) or [])
        if not trt:
            trt = list(all_geos - set(ctrl))
        control_map[name] = ctrl
        treat_map[name] = trt

    best_name, _ = design_items[0]
    control_geos = control_map[best_name]
    treatment_geos = treat_map[best_name]

    # ------------------------------------------------------------------
    # Métricas "bonitas" a partir del dump
    # ------------------------------------------------------------------
    # Buscamos en el dump atributos con nombres que suenen a métricas clave
    metric_keys = (
        "lift", "iroas", "roas", "incremental", "icpa", "cpa",
        "cost", "spend", "conversion", "revenue", "mde", "power",
        "p_value", "pvalue", "confidence", "significance", "absolute",
        "percentage", "value", "effect", "prior", "mean", "median",
        "ci_low", "ci_high", "ci", "credible",
    )

    metric_rows = []
    for _, r in df_designs.iterrows():
        attr_low = str(r["Atributo"]).lower()
        if any(k in attr_low for k in metric_keys):
            metric_rows.append({
                "Diseño": r["Diseño"],
                "Atributo": r["Atributo"],
                "Tipo": r["Tipo"],
                "Valor": r["Valor"],
            })
    df_metrics = pd.DataFrame(metric_rows) if metric_rows else pd.DataFrame(
        {"Info": ["No se encontraron atributos con nombres de métricas "
                  "conocidas. Revisa el dump completo."]})

    # ------------------------------------------------------------------
    # Tablas de regiones
    # ------------------------------------------------------------------
    top_df = (panel_clean.groupby("location")["conversions"]
                          .sum().reset_index()
                          .rename(columns={"location": "Region",
                                            "conversions": "Volumen"})
                          .sort_values("Volumen", ascending=False))
    top_df["Grupo"] = np.where(
        top_df["Region"].isin(control_geos), "Control", "Tratamiento")
    top_df["Volumen"] = top_df["Volumen"].round(2)

    ctrl_df = pd.DataFrame({"Región (Control)": control_geos})
    trt_df = pd.DataFrame({"Región (Tratamiento)": treatment_geos})

    designs_long = []
    for name, ctrl in control_map.items():
        for r in ctrl:
            designs_long.append({"Diseño": name, "Region": r,
                                 "Grupo": "Control"})
        for r in treat_map[name]:
            designs_long.append({"Diseño": name, "Region": r,
                                 "Grupo": "Tratamiento"})
    designs_long_df = pd.DataFrame(designs_long)

    # ------------------------------------------------------------------
    # Figuras
    # ------------------------------------------------------------------
    _emit_progress(progress_callback, 95, "Generando gráficos GeoX...")
    fig_split = _plot_geo_split(top_df, control_geos, treatment_geos)
    fig_balance = _plot_balance(top_df)

    # ------------------------------------------------------------------
    # Configuración usada
    # ------------------------------------------------------------------
    config_resumen = pd.DataFrame([
        {"Parámetro": "Fecha", "Valor": str(date_col)},
        {"Parámetro": "Región", "Valor": str(region_col)},
        {"Parámetro": "KPI", "Valor": str(kpi_col)},
        {"Parámetro": "Mercado", "Valor": market_value or "—"},
        {"Parámetro": "Duración (días)", "Valor": duration_days},
        {"Parámetro": "Presupuesto", "Valor": f"{budget:,.0f}"},
        {"Parámetro": "Coste / conv. incr.",
         "Valor": cost_per_incremental_conversion},
        {"Parámetro": "Diseños", "Valor": design_output_count},
        {"Parámetro": "Celdas", "Valor": cell_count},
        {"Parámetro": "Experimento", "Valor": experiment_type},
        {"Parámetro": "Metodología", "Valor": methodology},
        {"Parámetro": "Asignación", "Valor": geo_assignment_rule},
        {"Parámetro": "Control fijo", "Valor": ", ".join(included_control_geos) or "—"},
        {"Parámetro": "Regiones excluidas", "Valor": ", ".join(excluded_geos) or "—"},
        {"Parámetro": "Máx. conversión en tratamiento", "Valor": max_conversions_percent},
        {"Parámetro": "Alpha / potencia", "Valor": f"{alpha:g} / {power:g}"},
        {"Parámetro": "R² mínimo solicitado", "Valor": min_r2},
        {"Parámetro": "R² mínimo aplicado", "Valor": effective_min_r2},
        {"Parámetro": "Candidatos / finalistas", "Valor": f"{n_candidates:,} / {n_ranked_candidates:,}"},
        {"Parámetro": "Regiones válidas", "Valor": len(valid)},
        {"Parámetro": "Observaciones", "Valor": len(panel_clean)},
    ])

    log(f"[GeoX] COMPLETADO en {time.time()-t0:.1f}s")
    log("=" * 60)

    # ------------------------------------------------------------------
    # SALIDA COMPLETA
    # ------------------------------------------------------------------
    out = {
        "Estado": "OK",
        "Configuración usada": config_resumen,
        "Métricas detectadas": df_metrics,
        "Dump · design_results": df_root,
        "Dump · diseños": df_designs,
        f"Regiones · Control ({best_name})": ctrl_df,
        f"Regiones · Tratamiento ({best_name})": trt_df,
        "Tabla por región": top_df,
        "Asignaciones por diseño": designs_long_df,
        "Distribución control/tratamiento": {"plot": fig_split},
        "Balance de volumen": {"plot": fig_balance},
    }
    out["quality_status"] = "Media"
    out["Diagnóstico de calidad"] = (
        "Panel diario completo validado; Meridian GeoX ejecuta sus Quality "
        "Checks oficiales durante run_design. Revisa las métricas y "
        "restricciones antes de asignar el experimento.")

    _emit_progress(progress_callback, 100, "Geo Test completado")
    return out


def _emit_progress(callback, percent, message):
    """Publica progreso desde el worker sin depender de Tkinter."""
    pct = max(0, min(100, int(percent)))
    text = str(message).strip()
    if callback is not None:
        callback(pct, text)
    else:
        print(f"[Progreso {pct}%] {text}")


def _is_min_r2_failure(error):
    """Identifica el rechazo esperado de GeoX por falta de candidatos."""
    return "no designs passed the min r2 check" in str(error).lower()


# ==================================================================
# HELPERS
# ==================================================================
def _get_enum(geox_module, enum_name, value):
    try:
        return getattr(getattr(geox_module, enum_name), value)
    except Exception:
        pass
    for v in {"ExperimentType": ["HOLDBACK", "HOLD_BACK", "HOLDOUT",
                                  "GO_DARK", "HEAVY_UP"],
              "Methodology": ["TBR", "SDID"],
              "GeoAssignmentRule": ["STRATIFIED_SAMPLING", "STRATIFIED",
                                    "RANDOM"],
              "TestType": ["TWO_SIDED", "ONE_SIDED"]
              }.get(enum_name, []):
        try:
            return getattr(getattr(geox_module, enum_name), v)
        except Exception:
            continue
    return value


def _normalise_geo_list(values):
    """Convierte la selección del diálogo en nombres de geo estables."""
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split("\n")
    return list(dict.fromkeys(
        str(value).strip() for value in values
        if value is not None and str(value).strip()))


def _detect_granularity(panel):
    dates = pd.to_datetime(panel["date"], errors="coerce").dropna()
    uniq = pd.Series(sorted(dates.unique()))
    if len(uniq) < 3:
        return "D"
    diffs = uniq.diff().dropna()
    if diffs.empty:
        return "D"
    med = diffs.median()
    if med <= pd.Timedelta(days=2):
        return "D"
    if med <= pd.Timedelta(days=10):
        return "W"
    if med <= pd.Timedelta(days=45):
        return "M"
    if med <= pd.Timedelta(days=120):
        return "Q"
    return "Y"


# ==================================================================
# FIGURAS
# ==================================================================
def _plot_geo_split(df_top, control_geos, treatment_geos):
    fig = Figure(figsize=(9, 6), dpi=100)
    ax = fig.add_subplot(111)
    df_top = df_top.sort_values("Volumen", ascending=True).copy()
    df_top["Grupo"] = np.where(
        df_top["Region"].isin(control_geos), "Control", "Tratamiento")
    colors = df_top["Grupo"].map(
        {"Control": "#F85149", "Tratamiento": "#58A6FF"}).tolist()
    ax.barh(df_top["Region"], df_top["Volumen"], color=colors)
    ax.set_xlabel("Volumen total del KPI")
    ax.set_ylabel("Región")
    ax.set_title("Distribución control vs. tratamiento",
                 fontsize=12, fontweight="bold")
    ax.grid(True, axis="x", alpha=0.3)
    ax.legend(handles=[
        Patch(color="#F85149", label=f"Control ({len(control_geos)})"),
        Patch(color="#58A6FF",
              label=f"Tratamiento ({len(treatment_geos)})"),
    ], loc="lower right", fontsize=9)
    fig.tight_layout()
    return fig


def _plot_balance(df_top):
    fig = Figure(figsize=(7, 5), dpi=100)
    ax = fig.add_subplot(111)
    agg = df_top.groupby("Grupo")["Volumen"].sum().reindex(
        ["Control", "Tratamiento"]).fillna(0)
    bars = ax.bar(agg.index, agg.values, color=["#F85149", "#58A6FF"])
    total = float(agg.sum()) or 1.0
    for b, v in zip(bars, agg.values):
        pct = v / total * 100
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(),
                f"{v:,.0f}\n({pct:.1f}%)",
                ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("Volumen total")
    ax.set_title("Balance de volumen control vs. tratamiento",
                 fontsize=12, fontweight="bold")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    return fig
