"""Análisis de regresión con contribuciones y descomposición de KPI."""

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
import matplotlib.dates as mdates

from core.loader import get_date_columns, detect_date_column

NAME = "Regresión (atribución de contribución)"
DESCRIPTION = "Regresión con IC, forest plot y descomposición de contribuciones"
CATEGORY = "MMM"

CUSTOM_DIALOG = "RegressionDialog"

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

CI_LEVELS = [0.95, 0.90, 0.85]
BOOTSTRAP_ITER = 500
RNG_SEED = 42


# ==================================================================
# SCHEMA (compatibilidad)
# ==================================================================
def get_config_schema(df: pd.DataFrame) -> dict:
    date_cols = get_date_columns(df)
    num_cols = df.select_dtypes("number").columns.tolist()
    return {
        "title": "Regresión",
        "fields": [
            {"key": "regression_type", "label": "Tipo de regresión",
             "type": "select", "options": REGRESSION_TYPES,
             "default": REGRESSION_TYPES[0]},
            {"key": "date_col", "label": "Columna de fecha",
             "type": "select", "options": [str(c) for c in date_cols],
             "default": str(date_cols[0]) if date_cols else None},
            {"key": "target_col", "label": "Variable objetivo",
             "type": "select", "options": [str(c) for c in num_cols],
             "default": str(num_cols[0]) if num_cols else None},
            {"key": "input_cols", "label": "Variables de entrada",
             "type": "multi", "options": [str(c) for c in num_cols],
             "default": []},
        ],
    }


# ==================================================================
# RUN
# ==================================================================
def run(df: pd.DataFrame,
        regression_type: str = REGRESSION_TYPES[0],
        date_col: str | None = None,
        target_col: str | None = None,
        input_cols: list[str] | None = None,
        anomaly_cols: list[str] | None = None,
        progress_callback=None,
        **kwargs) -> dict:

    _emit_progress(progress_callback, 2, "Preparando datos de regresión...")

    print("=" * 60)
    print(f"[Regresión] tipo     = {regression_type}")
    print(f"[Regresión] target   = {target_col}")
    print(f"[Regresión] inputs   = {input_cols}")
    print(f"[Regresión] anomalías= {anomaly_cols}")
    print(f"[Regresión] df.shape = {df.shape}")
    print("=" * 60)

    if not target_col or target_col not in df.columns:
        msg = f"ERROR · variable objetivo '{target_col}' no existe"
        print(f"[Regresión] {msg}")
        return {"Estado": msg,
                "Detalle": f"Columnas: {list(df.columns)}"}

    requested_regression_type = regression_type
    is_automatic = str(regression_type).lower().startswith("automático")
    is_its = str(regression_type).lower().startswith("evento / its")
    if not input_cols and not is_its:
        msg = "ERROR · sin variables de entrada"
        print(f"[Regresión] {msg}")
        return {"Estado": msg}

    if not date_col or date_col not in df.columns:
        date_col = detect_date_column(df)

    work = df.copy()

    if date_col and date_col in work.columns:
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        work = work.dropna(subset=[date_col]).sort_values(date_col)
        work = work.reset_index(drop=True)

    predictors = list(input_cols or [])
    if anomaly_cols and not is_its:
        predictors += [c for c in anomaly_cols if c in work.columns]
    event_cols = [c for c in (anomaly_cols or []) if c in work.columns]
    if is_its and (not date_col or date_col not in work.columns):
        return {"Estado": "ERROR · ITS requiere una columna de fecha"}
    if is_its and not event_cols:
        return {"Estado": "ERROR · ITS requiere al menos un evento marcado"}
    if is_its and len(event_cols) != 1:
        return {"Estado": "ERROR · ITS requiere un único evento o grupo"}

    missing = [c for c in predictors if c not in work.columns]
    if missing:
        msg = f"ERROR · faltan columnas: {missing}"
        print(f"[Regresión] {msg}")
        return {"Estado": msg,
                "Detalle": f"Columnas: {list(work.columns)}"}

    numeric_cols = list(dict.fromkeys(
        [target_col] + predictors + (event_cols if is_its else [])))
    for c in numeric_cols:
        work[c] = pd.to_numeric(work[c], errors="coerce")

    print(f"[Regresión] filas totales: {len(work)}")
    print(f"[Regresión] NaN por columna (antes de limpiar):")
    for c in numeric_cols:
        n = int(work[c].isna().sum())
        pct = n / len(work) * 100 if len(work) else 0
        if n > 0:
            print(f"    · {c}: {n} ({pct:.1f}%)")

    # ---------------- Dataset de modelado ----------------
    model_df = work.copy()
    if date_col and date_col in model_df.columns:
        model_df = model_df.dropna(subset=[date_col])
    for c in numeric_cols:
        model_df[c] = model_df[c].replace([np.inf, -np.inf], np.nan)
    model_df = model_df.dropna(subset=numeric_cols)
    source_rows = model_df.index.to_numpy()
    model_df = model_df.reset_index(drop=True)

    n_obs = len(model_df)
    print(f"[Regresión] filas tras limpieza: {n_obs}")

    if n_obs < 10:
        msg = (f"ERROR · solo {n_obs} filas utilizables. "
               "Se necesitan ≥10.")
        print(f"[Regresión] {msg}")
        return {"Estado": msg,
                "Detalle": f"Filas antes: {len(work)}, después: {n_obs}"}

    X = model_df[predictors].astype(float)
    y = model_df[target_col].astype(float).values

    print(f"[Regresión] X.shape={X.shape}  y.shape={y.shape}")

    if y.std() < 1e-12:
        msg = "ERROR · el target es constante (std ≈ 0)."
        print(f"[Regresión] {msg}")
        return {"Estado": msg}

    model_comparison = None
    if is_automatic:
        try:
            regression_type, rolling_metrics, model_comparison = \
                _select_automatic_model(X, y, progress_callback)
        except Exception as exc:
            return {
                "Estado": "ERROR · selección automática sin validación OOS",
                "Detalle": str(exc),
                "quality_status": "No válida",
            }
        print(f"[Regresión] Auto seleccionó: {regression_type}")
    else:
        rolling_metrics = None

    # Holdout cronológico independiente: las métricas OOS son la referencia
    # predictiva; el ajuste posterior con todos los datos sigue sirviendo para
    # describir coeficientes y generar la predicción histórica.
    oos_metrics = (_temporal_holdout_metrics(regression_type, X, y)
                   if not is_its else pd.DataFrame([{
                       "Estado": "No aplica",
                       "quality_status": "Media",
                       "Motivo": ("ITS estima cambios asociados a una "
                                  "intervención; no es un objetivo de "
                                  "predicción OOS."),
                   }]))
    if rolling_metrics is None:
        rolling_metrics = (_rolling_origin_metrics(regression_type, X, y)
                           if not is_its else pd.DataFrame([{
                           "Estado": "No aplica",
                           "Motivo": "ITS no es un objetivo de predicción.",
                           }]))

    # ---------------- Ajuste ----------------
    print("[Regresión] Ajustando modelo...")
    fit_progress_start = 55 if is_automatic else 15
    _emit_progress(progress_callback, fit_progress_start,
                    "Ajustando el modelo seleccionado...")
    try:
        if is_its:
            fit = _fit_segmented_its(
                X, y, model_df[event_cols],
                model_df[date_col].reset_index(drop=True),
                progress_callback=progress_callback)
        else:
            fit = _fit_with_ci(
                regression_type, X, y,
                progress_callback=progress_callback,
                progress_start=fit_progress_start, progress_end=72)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"Estado": f"ERROR · ajuste: {e}", "Detalle": str(e)}

    y_pred = fit["y_pred"]
    residuos = y - y_pred

    # ---------------- Contribuciones ----------------
    print("[Regresión] Calculando contribuciones por variable...")
    _emit_progress(
        progress_callback, 76, "Calculando contribuciones por variable...")
    try:
        if is_its:
            contrib_df = pd.DataFrame(index=X.index)
            baseline_series = pd.Series(y_pred, index=X.index)
        else:
            contrib_df, baseline_series = _compute_contributions(fit, X, y_pred)
    except Exception as e:
        print(f"[Regresión] Error calculando contribuciones: {e}")
        contrib_df = pd.DataFrame(index=X.index)
        baseline_series = pd.Series(0.0, index=X.index)

    # ---------------- Métricas ----------------
    metrics_df = _compute_metrics(y, y_pred, residuos,
                                  X.shape[1] + (3 if is_its else 0))

    # ---------------- Tabla de coeficientes ----------------
    coef_df = _build_coef_table(fit)

    # ---------------- Ranking de contribución ----------------
    contrib_summary = _summary_contributions(contrib_df, y_pred)

    # ---------------- Figuras ----------------
    print("[Regresión] Generando gráficos...")
    _emit_progress(progress_callback, 86, "Generando gráficos...")
    fig_fit = _plot_fit(model_df, date_col, target_col, y, y_pred, metrics_df)
    fig_forest = _plot_forest(fit["coefs"], target_col)
    fig_resid = _plot_residuals(model_df, date_col, residuos, y, y_pred)
    is_importance = bool(fit.get("coefs_original", {}).get("es_importancia"))
    fig_decomp = (None if (is_importance or is_its) else
                  _plot_decomposition(model_df, date_col, target_col,
                                      y, y_pred, contrib_df, baseline_series))

    # ---------------- Dataset aumentado ----------------
    _emit_progress(progress_callback, 94, "Preparando resultados exportables...")
    augmented = work.copy()
    augmented[f"Predicho_{target_col}"] = np.nan
    augmented[f"Residuo_{target_col}"] = np.nan
    idx_ok = source_rows
    augmented.loc[idx_ok, f"Predicho_{target_col}"] = y_pred
    augmented.loc[idx_ok, f"Residuo_{target_col}"] = residuos
    if is_its:
        start = int(fit["event_start_index"])
        augmented[f"Efecto ITS · {target_col}"] = np.nan
        augmented[f"Efecto ITS acumulado · {target_col}"] = np.nan
        augmented.loc[source_rows[start:], f"Efecto ITS · {target_col}"] = \
            fit["its_series"]["Cambio de nivel"].to_numpy()
        augmented.loc[source_rows[start:],
                      f"Efecto ITS acumulado · {target_col}"] = \
            fit["its_series"]["Efecto acumulado"].to_numpy()

    # Añadir contribuciones al dataset aumentado
    for c in contrib_df.columns:
        col = f"Contrib · {c}"
        augmented[col] = np.nan
        augmented.loc[idx_ok, col] = contrib_df[c].values
    if not is_importance and not is_its:
        augmented["Contrib · Baseline"] = np.nan
        augmented.loc[idx_ok, "Contrib · Baseline"] = baseline_series.values

    out = {
        "Estado": "OK",
        "Interpretación": (
            "Estimación ITS asociada al evento; no implica causalidad por sí sola."
            if is_its else "Atribución de la predicción, no atribución causal."
            if not is_importance else
            "Importancia de variables del modelo; no es atribución de "
            "predicción ni atribución causal."),
        "Modelo solicitado": requested_regression_type,
        "Modelo ajustado": regression_type,
        "Validación temporal (OOS)": oos_metrics,
        "Validación temporal (rolling OOS)": rolling_metrics,
        "Métricas in-sample (diagnóstico)": metrics_df,
        "Coeficientes (con IC 95/90/85%)": coef_df,
        "Ajuste · Real vs Predicho": {"plot": fig_fit},
        "Coeficientes · Forest plot": {"plot": fig_forest},
        "Residuos y diagnóstico": {"plot": fig_resid},
        "Datos con anomalías y predicciones": augmented,
    }

    if not is_its:
        rolling_quality = rolling_metrics.iloc[0]
        quality = (rolling_quality if rolling_quality.get("Estado") == "OK"
                   else oos_metrics.iloc[0])
        out["quality_status"] = quality.get("quality_status", "No válida")
        out["Diagnóstico de calidad"] = quality.get(
            "Diagnóstico de calidad", quality.get(
                "Motivo", "Validación OOS no disponible."))
        out["Calidad del resultado"] = pd.DataFrame([{
            "Estado": out["quality_status"],
            "Criterio": out["Diagnóstico de calidad"],
            "Validación usada": ("Rolling OOS" if rolling_quality.get(
                "Estado") == "OK" else "Holdout final"),
        }])

    if not is_its:
        out["Resumen de contribución por variable"] = contrib_summary
    else:
        out["quality_status"] = "Media"
        out["Diagnóstico de calidad"] = (
            "Series PRE y POST superan los mínimos operativos. Revisa "
            "autocorrelación, estacionalidad y cambios concurrentes antes de "
            "interpretar el evento; ITS no prueba causalidad.")
        out["Calidad del resultado"] = pd.DataFrame([{
            "Estado": out["quality_status"],
            "Criterio": out["Diagnóstico de calidad"],
            "Validación usada": "Diagnóstico ITS; no aplica OOS predictivo",
        }])

    if model_comparison is not None:
        out["Comparativa temporal de modelos"] = model_comparison

    if fig_decomp is not None:
        out["Contribución apilada por variable"] = {"plot": fig_decomp}
    if is_its:
        out["Efectos ITS"] = fit["its_summary"]
        out["Serie de efecto del evento"] = fit["its_series"]
        out["Efecto ITS · gráfico"] = {
            "plot": _plot_its_effect(fit["its_series"], target_col)}

    if "summary" in fit and fit["summary"]:
        out["Resumen statsmodels"] = fit["summary"]

    notes = []
    if is_importance:
        notes.append("Los árboles no proporcionan descomposición aditiva válida; "
                     "se omite el gráfico y sus importancias no se presentan "
                     "como contribuciones.")
    if is_its:
        notes.append("OLS segmentada con covarianza HAC/Newey-West. Revisar "
                     "cambios concurrentes, estacionalidad y duración PRE/POST; "
                     "el efecto no es automáticamente causal.")
    if fit.get("bootstrap_used"):
        notes.append(f"IC calculados por bootstrap con {BOOTSTRAP_ITER} iteraciones.")
    if notes:
        out["Nota metodológica"] = pd.DataFrame([{"Info": note}
                                                  for note in notes])

    print("[Regresión] OK · resultados generados")
    _emit_progress(progress_callback, 100, "Regresión completada")
    return out


def _emit_progress(callback, percent, message):
    """Publica progreso desde el worker sin depender de la interfaz."""
    pct = max(0, min(100, int(percent)))
    text = str(message).strip()
    if callback is not None:
        callback(pct, text)
    else:
        print(f"[Progreso {pct}%] {text}")


def _fit_segmented_its(X: pd.DataFrame, y: np.ndarray,
                       event_frame: pd.DataFrame, dates: pd.Series,
                       progress_callback=None) -> dict:
    """OLS segmentada con salto de nivel/pendiente e IC HAC Newey-West."""
    n = len(y)
    active = event_frame.fillna(0).gt(0).any(axis=1).to_numpy()
    indices = np.flatnonzero(active)
    if not len(indices):
        raise ValueError("Los eventos seleccionados no coinciden con observaciones.")
    start = int(indices[0])
    if start < 8 or n - start < 4:
        raise ValueError("ITS requiere al menos 8 observaciones PRE y 4 POST.")

    time_index = np.arange(n, dtype=float)
    after = (time_index >= start).astype(float)
    time_after = np.maximum(0.0, time_index - start)
    design = pd.DataFrame({
        "const": np.ones(n),
        "Tendencia": time_index,
        "Cambio_nivel_evento": after,
        "Cambio_pendiente_evento": time_after,
    })
    control_names = {}
    for index, name in enumerate(X.columns):
        safe_name = f"Control_{index + 1} · {name}"
        design[safe_name] = X[name].to_numpy(dtype=float)
        control_names[name] = safe_name

    import statsmodels.api as sm
    maxlags = max(1, min(int(np.sqrt(n)), n - 1))
    model = sm.OLS(np.asarray(y, dtype=float), design).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags})
    params = model.params
    cov = model.cov_params()
    names = list(design.columns)
    coefs = {}
    for name in names:
        row = {"coef": float(params[name]), "se": float(model.bse[name]),
               "p": float(model.pvalues[name]), "t": float(model.tvalues[name])}
        for level in CI_LEVELS:
            lower, upper = model.conf_int(alpha=1 - level).loc[name]
            row[f"IC{int(level * 100)}_low"] = float(lower)
            row[f"IC{int(level * 100)}_high"] = float(upper)
        coefs[name] = row

    post_tau = time_after[start:]
    h = np.column_stack([np.ones(len(post_tau)), post_tau])
    beta = np.array([params["Cambio_nivel_evento"],
                     params["Cambio_pendiente_evento"]], dtype=float)
    cov_names = ["Cambio_nivel_evento", "Cambio_pendiente_evento"]
    cov_event = np.asarray(cov.loc[cov_names, cov_names], dtype=float)
    effects = h @ beta
    point_var = np.einsum("ij,jk,ik->i", h, cov_event, h)
    point_se = np.sqrt(np.maximum(point_var, 0.0))
    z95 = 1.959963984540054
    cumulative = np.cumsum(effects)
    cumulative_se = []
    for end in range(1, len(h) + 1):
        h_sum = h[:end].sum(axis=0)
        cumulative_se.append(float(np.sqrt(max(h_sum @ cov_event @ h_sum, 0.0))))
    cumulative_se = np.asarray(cumulative_se)
    dates_values = pd.to_datetime(dates).to_numpy()[start:]
    its_series = pd.DataFrame({
        "Fecha": dates_values,
        "Cambio de nivel": effects,
        "IC95 inferior": effects - z95 * point_se,
        "IC95 superior": effects + z95 * point_se,
        "Efecto acumulado": cumulative,
        "IC95 acumulado inferior": cumulative - z95 * cumulative_se,
        "IC95 acumulado superior": cumulative + z95 * cumulative_se,
    })
    total_h = h.sum(axis=0)
    total_effect = float(np.sum(effects))
    total_se = float(np.sqrt(max(total_h @ cov_event @ total_h, 0.0)))
    summary = pd.DataFrame([
        {"Métrica": "Cambio inmediato de nivel", "Estimación": beta[0],
         "IC95 inferior": beta[0] - z95 * np.sqrt(max(cov_event[0, 0], 0)),
         "IC95 superior": beta[0] + z95 * np.sqrt(max(cov_event[0, 0], 0))},
        {"Métrica": "Cambio de pendiente por periodo", "Estimación": beta[1],
         "IC95 inferior": beta[1] - z95 * np.sqrt(max(cov_event[1, 1], 0)),
         "IC95 superior": beta[1] + z95 * np.sqrt(max(cov_event[1, 1], 0))},
        {"Métrica": "Efecto acumulado POST", "Estimación": total_effect,
         "IC95 inferior": total_effect - z95 * total_se,
         "IC95 superior": total_effect + z95 * total_se},
    ])
    _emit_progress(progress_callback, 72, "Modelo ITS y covarianza HAC estimados")
    return {
        "coefs": coefs,
        "coefs_original": {"intercept": float(params["const"]),
                           "coefs": {}, "es_its": True},
        "y_pred": np.asarray(model.fittedvalues, dtype=float),
        "summary": str(model.summary()),
        "bootstrap_used": False,
        "model": model,
        "its_summary": summary,
        "its_series": its_series,
        "event_start_index": start,
        "hac_maxlags": maxlags,
        "control_names": control_names,
    }


def _temporal_holdout_metrics(regression_type: str, X: pd.DataFrame,
                              y: np.ndarray) -> pd.DataFrame:
    """Evalúa un único holdout final sin mezclar observaciones futuras."""
    n = len(y)
    test_n = max(2, int(np.ceil(n * 0.2)))
    split = n - test_n
    if split < max(5, X.shape[1] + 2):
        return pd.DataFrame([{
            "Estado": "No disponible",
            "quality_status": "No válida",
            "Motivo": "Histórico insuficiente para separar entrenamiento y prueba temporal.",
        }])

    try:
        if regression_type.lower().startswith("ols"):
            train_x = np.column_stack([np.ones(split), X.iloc[:split].values])
            test_x = np.column_stack([np.ones(test_n), X.iloc[split:].values])
            beta, *_ = np.linalg.lstsq(train_x, y[:split], rcond=None)
            predicted = np.asarray(test_x @ beta, dtype=float)
        else:
            from sklearn.preprocessing import StandardScaler
            model = _make_sklearn_model(
                regression_type, seed=RNG_SEED, validation=True)
            scaler = StandardScaler()
            train_x = scaler.fit_transform(X.iloc[:split])
            test_x = scaler.transform(X.iloc[split:])
            model.fit(train_x, y[:split])
            predicted = np.asarray(model.predict(test_x), dtype=float)
    except Exception as exc:
        return pd.DataFrame([{"Estado": "No disponible",
                              "quality_status": "No válida",
                              "Motivo": str(exc)}])

    actual = np.asarray(y[split:], dtype=float)
    residual = actual - predicted
    baseline_residual = actual - float(np.mean(y[:split]))
    baseline_rmse = float(np.sqrt(np.mean(baseline_residual ** 2)))
    model_rmse = float(np.sqrt(np.mean(residual ** 2)))
    better_than_baseline = model_rmse < baseline_rmse
    denominator = float(np.sum(np.abs(actual)))
    total = float(np.sum((actual - actual.mean()) ** 2))
    r2 = (1.0 - float(np.sum(residual ** 2)) / total
          if total > 1e-12 else np.nan)
    return pd.DataFrame([{
        "Estado": "OK",
        "quality_status": "Media" if better_than_baseline else "Baja",
        "Diagnóstico de calidad": (
            "El error OOS mejora la predicción constante del promedio PRE."
            if better_than_baseline else
            "El error OOS no mejora la predicción constante del promedio PRE."),
        "Observaciones entrenamiento": split,
        "Observaciones prueba": len(actual),
        "RMSE OOS": model_rmse,
        "RMSE baseline PRE": baseline_rmse,
        "MAE OOS": float(np.mean(np.abs(residual))),
        "WAPE OOS (%)": (float(np.sum(np.abs(residual)) / denominator * 100)
                         if denominator > 1e-12 else np.nan),
        "Sesgo OOS": float(np.mean(residual)),
        "R² OOS": r2,
        "Método": "Holdout cronológico final (80/20 aprox.); sin barajar.",
    }])


def _rolling_origin_metrics(regression_type: str, X: pd.DataFrame,
                            y: np.ndarray) -> pd.DataFrame:
    """Métricas OOS agregadas de splits expanding, siempre cronológicos."""
    n = len(y)
    min_train = max(10, X.shape[1] + 3)
    n_splits = min(5, n - 1)
    while n_splits >= 2 and n // (n_splits + 1) < min_train:
        n_splits -= 1
    if n_splits < 2:
        return pd.DataFrame([{
            "Estado": "No disponible",
            "quality_status": "No válida",
            "Motivo": ("Histórico insuficiente para al menos dos folds "
                       "rolling con entrenamiento mínimo."),
        }])

    actual_parts, predicted_parts, baseline_parts = [], [], []
    fold_rmse = []
    try:
        from sklearn.model_selection import TimeSeriesSplit
        splitter = TimeSeriesSplit(n_splits=n_splits)
        for train_idx, test_idx in splitter.split(X):
            if len(train_idx) < min_train or not len(test_idx):
                continue
            if regression_type.lower().startswith("ols"):
                train_x = np.column_stack([
                    np.ones(len(train_idx)), X.iloc[train_idx].values])
                test_x = np.column_stack([
                    np.ones(len(test_idx)), X.iloc[test_idx].values])
                beta, *_ = np.linalg.lstsq(train_x, y[train_idx], rcond=None)
                predicted = np.asarray(test_x @ beta, dtype=float)
            else:
                from sklearn.preprocessing import StandardScaler
                scaler = StandardScaler()
                train_x = scaler.fit_transform(X.iloc[train_idx])
                test_x = scaler.transform(X.iloc[test_idx])
                model = _make_sklearn_model(
                    regression_type, seed=RNG_SEED, validation=True)
                model.fit(train_x, y[train_idx])
                predicted = np.asarray(model.predict(test_x), dtype=float)
            actual = np.asarray(y[test_idx], dtype=float)
            actual_parts.append(actual)
            predicted_parts.append(predicted)
            baseline_parts.append(np.full(len(test_idx),
                                          float(np.mean(y[train_idx]))))
            fold_rmse.append(float(np.sqrt(np.mean((actual - predicted) ** 2))))
    except Exception as exc:
        return pd.DataFrame([{
            "Estado": "No disponible",
            "quality_status": "No válida",
            "Motivo": str(exc),
        }])

    if not actual_parts:
        return pd.DataFrame([{
            "Estado": "No disponible", "quality_status": "No válida",
            "Motivo": "No se pudo formar ningún fold temporal válido.",
        }])
    actual = np.concatenate(actual_parts)
    predicted = np.concatenate(predicted_parts)
    baseline = np.concatenate(baseline_parts)
    residual = actual - predicted
    baseline_rmse = float(np.sqrt(np.mean((actual - baseline) ** 2)))
    model_rmse = float(np.sqrt(np.mean(residual ** 2)))
    denominator = float(np.sum(np.abs(actual)))
    total = float(np.sum((actual - actual.mean()) ** 2))
    r2 = (1.0 - float(np.sum(residual ** 2)) / total
          if total > 1e-12 else np.nan)
    better = model_rmse < baseline_rmse
    return pd.DataFrame([{
        "Estado": "OK",
        "quality_status": "Media" if better else "Baja",
        "Diagnóstico de calidad": (
            "Rolling OOS mejora el baseline temporal de promedio PRE."
            if better else
            "Rolling OOS no mejora el baseline temporal de promedio PRE."),
        "Folds": len(actual_parts),
        "Observaciones OOS": len(actual),
        "RMSE OOS": model_rmse,
        "RMSE por fold (media)": float(np.mean(fold_rmse)),
        "RMSE baseline PRE": baseline_rmse,
        "MAE OOS": float(np.mean(np.abs(residual))),
        "WAPE OOS (%)": (float(np.sum(np.abs(residual)) / denominator * 100)
                         if denominator > 1e-12 else np.nan),
        "Sesgo OOS": float(np.mean(residual)),
        "R² OOS": r2,
        "Método": ("Expanding window, TimeSeriesSplit, sin barajar ni gap; "
                   "baseline promedio de cada entrenamiento."),
    }])


def _select_automatic_model(X: pd.DataFrame, y: np.ndarray,
                            progress_callback=None):
    """Selecciona por expanding OOS y favorece simplicidad ante empates.

    Cada candidato usa exactamente los mismos folds. Un modelo complejo sólo
    desplaza a otro más simple si mejora el mejor RMSE en más de un 2 %.
    Modelos que no superan el baseline PRE quedan fuera.
    """
    candidates = [
        "OLS (mínimos cuadrados, con p-values)",
        "Ridge (regularización L2)",
        "Elastic Net",
        "Huber (robusto a outliers)",
        "Random Forest",
        "Gradient Boosting",
    ]
    evaluated = []
    metrics_by_name = {}
    for index, name in enumerate(candidates):
        _emit_progress(
            progress_callback, 4 + int(42 * index / len(candidates)),
            f"Comparando modelos temporalmente ({index + 1}/"
            f"{len(candidates)}): {name}")
        metric = _rolling_origin_metrics(name, X, y).iloc[0].to_dict()
        if (metric.get("Estado") != "OK"
                or not np.isfinite(metric.get("RMSE OOS", np.nan))
                or not np.isfinite(metric.get("RMSE baseline PRE", np.nan))):
            continue
        improves_baseline = (metric["RMSE OOS"]
                             < metric["RMSE baseline PRE"])
        evaluated.append({
            "Modelo": name,
            "RMSE OOS": float(metric["RMSE OOS"]),
            "RMSE baseline PRE": float(metric["RMSE baseline PRE"]),
            "Mejora vs baseline (%)": float(
                (metric["RMSE baseline PRE"] - metric["RMSE OOS"])
                / max(metric["RMSE baseline PRE"], 1e-12) * 100),
            "Folds": int(metric.get("Folds", 0)),
            "Estado": "Candidato" if improves_baseline else
                      "Descartado: no mejora baseline",
            "Seleccionado": False,
        })
        metrics_by_name[name] = pd.DataFrame([metric])

    valid = [row for row in evaluated if row["Estado"] == "Candidato"]
    if not valid:
        raise ValueError(
            "Ningún modelo probado mejora el baseline temporal PRE; "
            "no se presenta un modelo como predictivo fiable.")
    best_rmse = min(row["RMSE OOS"] for row in valid)
    selected = next(row for row in valid
                    if row["RMSE OOS"] <= best_rmse * 1.02)
    selected["Seleccionado"] = True
    comparison = pd.DataFrame(evaluated)
    comparison["Regla"] = (
        "Se elige el más simple dentro del 2 % del mejor RMSE OOS; "
        "deben superar el promedio PRE.")
    return selected["Modelo"], metrics_by_name[selected["Modelo"]], comparison


# ==================================================================
# AJUSTE CON IC
# ==================================================================
# ==================================================================
# HELPER DE MODELO (nivel de módulo para que joblib lo pickle)
# ==================================================================
def _make_sklearn_model(model_type: str, seed: int = 42,
                        validation: bool = False):
    """
    Crea el modelo sklearn correspondiente.
    Está a nivel de módulo para que joblib pueda picklarlo.
    """
    from sklearn.linear_model import (
        Ridge, Lasso, ElasticNet, BayesianRidge, HuberRegressor,
    )
    from sklearn.ensemble import (
        RandomForestRegressor, GradientBoostingRegressor,
    )

    t = model_type.lower()
    if t.startswith("ridge"):
        return Ridge(alpha=1.0)
    if t.startswith("lasso"):
        return Lasso(alpha=0.01, max_iter=10000)
    if t.startswith("elastic"):
        return ElasticNet(alpha=0.01, l1_ratio=0.5, max_iter=10000)
    if t.startswith("bayesian"):
        return BayesianRidge()
    if t.startswith("huber"):
        return HuberRegressor()
    if t.startswith("random"):
        return RandomForestRegressor(
            n_estimators=100 if validation else 300,
            random_state=seed, n_jobs=1)
    if t.startswith("gradient"):
        return GradientBoostingRegressor(
            n_estimators=100 if validation else 300, random_state=seed)
    raise ValueError(f"Tipo no soportado: {model_type}")


def _boot_iter_worker(seed_i, Xs, y, model_type):
    """
    Una iteración de bootstrap. Debe ser función de módulo
    (no closure) para que joblib pueda picklarla.
    """
    rng = np.random.default_rng(int(seed_i))
    n = len(y)
    idx = rng.integers(0, n, size=n)
    try:
        m = _make_sklearn_model(model_type, seed=int(seed_i))
        m.fit(Xs[idx], y[idx])
        if hasattr(m, "coef_"):
            return np.ravel(m.coef_)
        elif hasattr(m, "feature_importances_"):
            return np.asarray(m.feature_importances_)
    except Exception:
        pass
    return np.full(Xs.shape[1], np.nan)


# ==================================================================
# AJUSTE CON IC
# ==================================================================
def _fit_with_ci(regression_type: str, X: pd.DataFrame,
                  y: np.ndarray, progress_callback=None,
                  progress_start=15, progress_end=72) -> dict:
    t = regression_type.lower()

    # ---------------- OLS ----------------
    if t.startswith("ols"):
        _emit_progress(
            progress_callback, progress_start + 10,
            "Estimando coeficientes OLS...")
        import statsmodels.api as sm
        Xc = sm.add_constant(X)
        model = sm.OLS(y, Xc).fit()

        coefs = {}
        for name in model.params.index:
            coefs[name] = {
                "coef": float(model.params[name]),
                "se": float(model.bse[name]),
                "p": float(model.pvalues[name]),
                "t": float(model.tvalues[name]),
            }
            for level in CI_LEVELS:
                alpha = 1 - level
                lo, hi = model.conf_int(alpha=alpha).loc[name]
                coefs[name][f"IC{int(level*100)}_low"] = float(lo)
                coefs[name][f"IC{int(level*100)}_high"] = float(hi)

        coefs_original = {
            "intercept": float(model.params["const"]),
            "coefs": {n: float(model.params[n])
                       for n in model.params.index if n != "const"},
        }

        _emit_progress(
            progress_callback, progress_end,
            "Intervalos de confianza calculados")
        return {
            "coefs": coefs,
            "coefs_original": coefs_original,
            "y_pred": np.asarray(model.fittedvalues),
            "summary": str(model.summary()),
            "bootstrap_used": False,
            "model": model,
        }

    # ---------------- sklearn ----------------
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    # Nº de iteraciones según tipo de modelo
    if t.startswith("random") or t.startswith("gradient"):
        n_boot = 100        # árboles: lento → menos iteraciones
    elif t.startswith("bayesian"):
        n_boot = 300
    else:
        n_boot = BOOTSTRAP_ITER

    # --- Fit puntual ---
    model = _make_sklearn_model(regression_type)
    model.fit(Xs, y)
    y_pred = model.predict(Xs)
    _emit_progress(
        progress_callback, progress_start + 8,
        "Modelo base ajustado; iniciando bootstrap...")

    # --- Coeficientes en escala original ---
    coefs_original = {"intercept": 0.0, "coefs": {}}

    if hasattr(model, "coef_"):
        coefs_std = np.ravel(model.coef_)
        means = scaler.mean_
        scales = scaler.scale_
        coefs_orig = coefs_std / scales
        intercept_orig = float(model.intercept_) - float(
            np.sum(coefs_std * means / scales))
        coefs_original = {
            "intercept": intercept_orig,
            "coefs": {name: float(c)
                       for name, c in zip(X.columns, coefs_orig)},
        }
        coefs_center = coefs_std
    elif hasattr(model, "feature_importances_"):
        imps = np.asarray(model.feature_importances_)
        coefs_original = {
            "intercept": float(np.mean(y)) * 0.1,
            "coefs": {name: float(imp)
                       for name, imp in zip(X.columns, imps)},
            "es_importancia": True,
        }
        coefs_center = imps
    else:
        coefs_center = np.zeros(X.shape[1])

    # =============================================================
    # BOOTSTRAP PARALELO con joblib
    # =============================================================
    print(f"[Regresión] Bootstrap paralelo ({n_boot} iter, "
          f"modelo={regression_type.split(' ')[0]})...")

    seeds = np.random.default_rng(RNG_SEED).integers(
        0, 1_000_000, size=n_boot)

    # Threads comparten X/y y no crean procesos hijos. Esto evita ventanas
    # de consola en Windows/frozen y mantiene el presupuesto de workers de la
    # aplicación. El callback solo se ejecuta en el hilo principal.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    boot_list = []
    try:
        with ThreadPoolExecutor(max_workers=2,
                                thread_name_prefix="reg-bootstrap") as pool:
            futures = [pool.submit(_boot_iter_worker, int(seed), Xs, y,
                                   regression_type) for seed in seeds]
            for completed, future in enumerate(as_completed(futures), start=1):
                boot_list.append(future.result())
                if (completed == 1
                        or completed % max(1, n_boot // 10) == 0
                        or completed == n_boot):
                    pct = progress_start + 10 + int(
                        (progress_end - progress_start - 12)
                        * completed / n_boot)
                    _emit_progress(
                        progress_callback, pct,
                        f"Bootstrap: {completed}/{n_boot} iteraciones")
        boot_coefs = np.vstack(boot_list)
    except Exception as e:
        print(f"[Regresión] Bootstrap paralelo en threads falló ({e}), "
              "secuencial...")
        boot_coefs = np.zeros((n_boot, X.shape[1]))
        for i, s in enumerate(seeds):
            boot_coefs[i] = _boot_iter_worker(int(s), Xs, y, regression_type)
            if i == 0 or (i + 1) % max(1, n_boot // 10) == 0 \
                    or i + 1 == n_boot:
                pct = progress_start + 10 + int(
                    (progress_end - progress_start - 12) * (i + 1) / n_boot)
                _emit_progress(progress_callback, pct,
                               f"Bootstrap: {i + 1}/{n_boot} iteraciones")

    # --- IC por cuantiles del bootstrap ---
    coefs = {}
    for j, name in enumerate(X.columns):
        b = boot_coefs[:, j]
        b = b[~np.isnan(b)]
        if len(b) == 0:
            b = np.array([coefs_center[j]])

        coefs[name] = {
            "coef": float(coefs_center[j]),
            "se": float(np.std(b, ddof=1)) if len(b) > 1 else 0.0,
            "p": float("nan"),
            "t": float("nan"),
        }
        for level in CI_LEVELS:
            alpha = 1 - level
            lo = float(np.quantile(b, alpha / 2))
            hi = float(np.quantile(b, 1 - alpha / 2))
            coefs[name][f"IC{int(level*100)}_low"] = lo
            coefs[name][f"IC{int(level*100)}_high"] = hi

    _emit_progress(
        progress_callback, progress_end,
        "Intervalos de confianza calculados")
    return {
        "coefs": coefs,
        "coefs_original": coefs_original,
        "y_pred": y_pred,
        "summary": None,
        "bootstrap_used": True,
        "model": model,
    }

# ==================================================================
# CONTRIBUCIONES
# ==================================================================
def _compute_contributions(fit: dict, X: pd.DataFrame, y_pred: np.ndarray):
    """
    Descompone la predicción en baseline + contribución de cada variable.

    contribution_i(t) = coef_i * X_i(t)
    baseline(t)       = intercept

    La suma (baseline + Σ contrib) = y_pred.
    """
    coefs_orig = fit.get("coefs_original", {})
    intercept = float(coefs_orig.get("intercept", 0.0))
    coef_map = coefs_orig.get("coefs", {})

    # Los árboles no tienen coeficientes aditivos; no repartir su desviación
    # según feature_importances_, que no es una atribución de predicción.
    if coefs_orig.get("es_importancia"):
        contrib_df = pd.DataFrame(index=X.index)
        baseline = pd.Series(y_pred, index=X.index, dtype=float)
        return contrib_df, baseline

    # Contribución lineal estándar
    contrib_df = pd.DataFrame(index=X.index)
    for name in X.columns:
        c = float(coef_map.get(name, 0.0))
        contrib_df[name] = c * X[name].values

    baseline = pd.Series(intercept, index=X.index)
    return contrib_df, baseline


def _summary_contributions(contrib_df: pd.DataFrame,
                             y_pred: np.ndarray) -> pd.DataFrame:
    """Resumen: contribución total, media y % del KPI total por variable."""
    if contrib_df.empty:
        return pd.DataFrame([{"Info": "Sin contribuciones calculadas."}])

    total_kpi = float(np.sum(np.abs(y_pred)))
    if total_kpi < 1e-12:
        total_kpi = float(np.sum(np.abs(contrib_df.values))) or 1.0

    rows = []
    for name in contrib_df.columns:
        s = contrib_df[name]
        # Contribuciones absolutas acumuladas
        total = float(s.sum())
        total_abs = float(s.abs().sum())
        media = float(s.mean())
        pct_abs = (total_abs / total_kpi) * 100

        rows.append({
            "Variable": name,
            "Contribución total": round(total, 2),
            "Contribución absoluta": round(total_abs, 2),
            "Contribución media": round(media, 2),
            "% del KPI total": round(pct_abs, 2),
            "Signo": "Positivo" if total >= 0 else "Negativo",
        })

    df = pd.DataFrame(rows).sort_values(
        "Contribución absoluta", ascending=False).reset_index(drop=True)
    return df


# ==================================================================
# MÉTRICAS
# ==================================================================
def _compute_metrics(y, y_pred, residuos, n_pred):
    n = len(y)
    ss_res = float(np.sum(residuos ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))

    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    r2_adj = (1 - (1 - r2) * (n - 1) / (n - n_pred - 1)
              if n > n_pred + 1 else float("nan"))

    rmse = float(np.sqrt(np.mean(residuos ** 2)))
    mae = float(np.mean(np.abs(residuos)))
    std_res = float(np.std(residuos, ddof=1)) if n > 1 else float("nan")

    with np.errstate(divide="ignore", invalid="ignore"):
        y_safe = np.where(np.abs(y) < 1e-9, np.nan, y)
        mape = float(np.nanmean(np.abs(residuos / y_safe)) * 100)
        denom = np.abs(y) + np.abs(y_pred)
        denom = np.where(denom < 1e-9, np.nan, denom)
        smape = float(np.nanmean(2 * np.abs(residuos) / denom) * 100)

    k = n_pred + 1
    sigma2 = ss_res / n
    if sigma2 > 0:
        log_lik = -0.5 * n * (np.log(2 * np.pi * sigma2) + 1)
        aic = float(2 * k - 2 * log_lik)
        bic = float(k * np.log(n) - 2 * log_lik)
    else:
        aic = bic = float("nan")

    dw = float(np.sum(np.diff(residuos) ** 2) / ss_res) if ss_res > 0 else float("nan")

    return pd.DataFrame([
        {"Métrica": "N observaciones", "Valor": n},
        {"Métrica": "N predictores",   "Valor": n_pred},
        {"Métrica": "R²",              "Valor": round(r2, 4)},
        {"Métrica": "R² ajustado",     "Valor": round(r2_adj, 4)},
        {"Métrica": "RMSE",            "Valor": round(rmse, 4)},
        {"Métrica": "MAE",             "Valor": round(mae, 4)},
        {"Métrica": "MAPE (%)",        "Valor": round(mape, 2)},
        {"Métrica": "sMAPE (%)",       "Valor": round(smape, 2)},
        {"Métrica": "Desv. std residuos", "Valor": round(std_res, 4)},
        {"Métrica": "AIC",             "Valor": round(aic, 2) if np.isfinite(aic) else "—"},
        {"Métrica": "BIC",             "Valor": round(bic, 2) if np.isfinite(bic) else "—"},
        {"Métrica": "Durbin-Watson",   "Valor": round(dw, 3) if np.isfinite(dw) else "—"},
    ])


def _build_coef_table(fit: dict) -> pd.DataFrame:
    rows = []
    for name, c in fit["coefs"].items():
        row = {
            "Variable": name,
            "Coeficiente": round(c["coef"], 6),
            "Std Error": round(c["se"], 6),
            "p-value": (round(c["p"], 6)
                        if c["p"] == c["p"] else "—"),
        }
        for level in CI_LEVELS:
            key = int(level * 100)
            row[f"IC{key}% · low"] = round(c.get(f"IC{key}_low", np.nan), 6)
            row[f"IC{key}% · high"] = round(c.get(f"IC{key}_high", np.nan), 6)
        try:
            p = c["p"]
            row["Significancia"] = (
                "***" if p < 0.001 else
                "**" if p < 0.01 else
                "*" if p < 0.05 else
                "." if p < 0.10 else ""
            ) if p == p else ""
        except Exception:
            row["Significancia"] = ""
        rows.append(row)
    return pd.DataFrame(rows)


# ==================================================================
# FIGURAS
# ==================================================================
def _fmt_metric_line(metrics_df: pd.DataFrame) -> str:
    d = dict(zip(metrics_df["Métrica"], metrics_df["Valor"]))
    def g(k): return d.get(k, "—")
    return (f"R² = {g('R²')}   ·   R² ajust = {g('R² ajustado')}   ·   "
            f"RMSE = {g('RMSE')}   ·   MAE = {g('MAE')}   ·   "
            f"MAPE = {g('MAPE (%)')}%   ·   sMAPE = {g('sMAPE (%)')}%")


def _format_date_axis(ax, x_values, rotation=30):
    """Configura el eje X para mostrar fechas inclinadas sin solaparse."""
    try:
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=12))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        for label in ax.get_xticklabels():
            label.set_rotation(rotation)
            label.set_ha("right")
    except Exception:
        pass

def _plot_fit(model_df, date_col, target_col, y, y_pred, metrics_df):
    fig = Figure(figsize=(10, 5.5), dpi=100)
    ax = fig.add_subplot(111)

    use_dates = bool(date_col and date_col in model_df.columns)
    if use_dates:
        x = pd.to_datetime(model_df[date_col]).values
    else:
        x = np.arange(len(y))

    residuos = y - y_pred
    y_low = y_pred - np.abs(residuos)
    y_high = y_pred + np.abs(residuos)

    ax.fill_between(x, y_low, y_high, color="#F85149", alpha=0.15,
                    label="Error |residuo|")
    ax.plot(x, y, color="#58A6FF", lw=2.2, label="Real", zorder=5)
    ax.plot(x, y_pred, color="#F85149", lw=2.0, linestyle="--",
            label="Predicho", zorder=6)
    ax.scatter(x, y, color="#58A6FF", s=14, alpha=0.6, zorder=4)

    ax.set_title(f"Ajuste del modelo · {target_col}",
                 fontsize=12, fontweight="bold", pad=12)
    ax.text(0.5, 1.02, _fmt_metric_line(metrics_df),
            transform=ax.transAxes, ha="center", va="bottom",
            fontsize=9, color="#8B949E")
    ax.set_xlabel(date_col or "Índice")
    ax.set_ylabel(target_col)
    ax.grid(True, alpha=0.3)
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.15),
        ncol=3,
        fontsize=8,
        framealpha=0.9,
        facecolor="#1A2029",
        edgecolor="#333B45",
        borderaxespad=0.0,
    )
    fig.subplots_adjust(bottom=0.20)

    if use_dates:
        _format_date_axis(ax, x, rotation=30)

    fig.tight_layout()
    return fig


def _plot_its_effect(its_series: pd.DataFrame, target_col: str) -> Figure:
    """Muestra efecto por periodo e intervalo HAC y su acumulado."""
    fig = Figure(figsize=(10, 6), dpi=100)
    ax_effect = fig.add_subplot(211)
    ax_cumulative = fig.add_subplot(212, sharex=ax_effect)
    x = pd.to_datetime(its_series["Fecha"]).to_numpy()
    effect = its_series["Cambio de nivel"].to_numpy(dtype=float)
    lower = its_series["IC95 inferior"].to_numpy(dtype=float)
    upper = its_series["IC95 superior"].to_numpy(dtype=float)
    cumulative = its_series["Efecto acumulado"].to_numpy(dtype=float)
    cum_lower = its_series["IC95 acumulado inferior"].to_numpy(dtype=float)
    cum_upper = its_series["IC95 acumulado superior"].to_numpy(dtype=float)
    ax_effect.axhline(0, color="#8B949E", linewidth=0.8)
    ax_effect.fill_between(x, lower, upper, color="#58A6FF", alpha=0.22,
                           label="IC 95 % HAC")
    ax_effect.plot(x, effect, color="#58A6FF", linewidth=1.8,
                   label="Efecto por periodo")
    ax_effect.set_title(f"Cambio asociado al evento · {target_col}",
                        fontsize=12, fontweight="bold")
    ax_effect.set_ylabel("Efecto")
    ax_effect.legend(loc="best", fontsize=8)
    ax_effect.grid(True, alpha=0.25)
    ax_cumulative.fill_between(x, cum_lower, cum_upper,
                               color="#3FB950", alpha=0.20,
                               label="IC 95 % acumulado HAC")
    ax_cumulative.plot(x, cumulative, color="#3FB950", linewidth=1.8,
                       label="Efecto acumulado")
    ax_cumulative.axhline(0, color="#8B949E", linewidth=0.8)
    ax_cumulative.set_ylabel("Acumulado")
    ax_cumulative.set_xlabel("Fecha")
    ax_cumulative.legend(loc="best", fontsize=8)
    ax_cumulative.grid(True, alpha=0.25)
    _format_date_axis(ax_cumulative, x)
    fig.tight_layout()
    return fig

def _plot_decomposition(model_df, date_col, target_col,
                          y, y_pred, contrib_df, baseline):
    """
    Gráfico apilado tipo 'waterfall': muestra cómo cada variable contribuye
    al KPI a lo largo del tiempo.

    Estructura visual:
      - Capa 1: BASELINE (intercepto) apilado desde 0.
      - Capas 2..N: cada contribución apilada encima, en cascada.
      - El borde superior del stack coincide EXACTAMENTE con y_pred.
      - Línea blanca = KPI real (puede diferir de y_pred = error del modelo).

    Al sumar baseline + Σ contribuciones = y_pred, no quedan huecos.
    """
    fig = Figure(figsize=(11, 6), dpi=100)
    ax = fig.add_subplot(111)

    if date_col and date_col in model_df.columns:
        x = pd.to_datetime(model_df[date_col]).values
    else:
        x = np.arange(len(y))

    palette = ["#58A6FF", "#F85149", "#3FB950", "#D29922", "#BC8CFF",
                "#79B8FF", "#FF7B72", "#7EE787", "#FFA657", "#D2A8FF",
                "#FFD166", "#06D6A0", "#EF476F", "#118AB2", "#073B4C"]

    y_pred = np.asarray(y_pred, dtype=float)
    y = np.asarray(y, dtype=float)

    # Baseline: puede venir como escalar o como Series
    if np.isscalar(baseline) or isinstance(baseline, (int, float)):
        b_vals = np.full_like(y_pred, float(baseline))
    else:
        b_vals = np.asarray(baseline, dtype=float)

    # ------------------------------------------------------------------
    # CAPA 1 · Baseline apilado desde 0
    # ------------------------------------------------------------------
    cum = b_vals.copy()
    ax.fill_between(x, 0, cum,
                    color="#8B949E", alpha=0.45, zorder=2,
                    label="Baseline (intercepto)")

    # ------------------------------------------------------------------
    # CAPAS 2..N · Contribuciones en cascada
    # ------------------------------------------------------------------
    # Orden por peso medio absoluto (mayor abajo, más legible)
    try:
        order = (contrib_df.abs().mean()
                 .sort_values(ascending=False).index.tolist())
    except Exception:
        order = list(contrib_df.columns)

    for i, c in enumerate(order):
        vals = contrib_df[c].values.astype(float)
        new_cum = cum + vals
        color = palette[i % len(palette)]
        ax.fill_between(x, cum, new_cum,
                        color=color, alpha=0.85, zorder=3,
                        label=str(c))
        cum = new_cum

    # Ahora cum == y_pred en cada punto (sin huecos)

    # ------------------------------------------------------------------
    # LÍNEAS SUPERIORES · Predicho y real
    # ------------------------------------------------------------------
    ax.plot(x, y_pred, color="#FFE066", lw=1.6, linestyle="--",
            label="Predicho (suma)", zorder=9)
    ax.plot(x, y, color="#FFFFFF", lw=2.4,
            label=f"{target_col} (real)", zorder=10)

    # Sombreado suave del error del modelo (diferencia real vs predicho)
    try:
        ax.fill_between(x, y_pred, y,
                        color="#FFFFFF", alpha=0.07, zorder=1)
    except Exception:
        pass

    ax.axhline(0, color="#8B949E", lw=0.6, alpha=0.5, zorder=1)

    ax.set_title(f"Descomposición de {target_col} por variable",
                 fontsize=12, fontweight="bold", pad=12)
    ax.set_xlabel(date_col or "Índice")
    ax.set_ylabel(target_col)
    ax.grid(True, alpha=0.3)

    # --- Leyenda FUERA del área de trazado (debajo del eje X) ---
    # ncol dinámico: reparte los elementos en 3-4 columnas según cuántos haya
    n_items = len(order) + 3  # variables + baseline + predicho + real
    ncol = 4 if n_items > 10 else (3 if n_items > 6 else 2)

    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),   # debajo del eje X
        ncol=ncol,
        fontsize=8,
        framealpha=0.9,
        facecolor="#1A2029",
        edgecolor="#333B45",
        borderaxespad=0.0,
    )

    if date_col and date_col in model_df.columns:
        _format_date_axis(ax, x, rotation=30)

    # Dejar sitio abajo para la leyenda
    fig.subplots_adjust(bottom=0.30)
    return fig

def _plot_forest(coefs: dict, target_col: str):
    names = list(coefs.keys())
    n = len(names)

    fig = Figure(figsize=(10, max(4, 0.55 * n + 2)), dpi=100)
    ax = fig.add_subplot(111)

    y_pos = np.arange(n)

    colors = {
        95: {"color": "#58A6FF", "alpha": 0.20, "lw": 8},
        90: {"color": "#58A6FF", "alpha": 0.45, "lw": 8},
        85: {"color": "#58A6FF", "alpha": 0.80, "lw": 8},
    }

    for i, name in enumerate(names):
        c = coefs[name]
        for lvl in [95, 90, 85]:
            lo = c.get(f"IC{lvl}_low", None)
            hi = c.get(f"IC{lvl}_high", None)
            if lo is None or hi is None:
                continue
            if lo != lo or hi != hi:
                continue
            ax.plot([lo, hi], [i, i], solid_capstyle="round",
                    zorder=3 + (100 - lvl) / 100, **colors[lvl])

        ax.scatter([c["coef"]], [i], s=44,
                   facecolor="#FFFFFF", edgecolor="#1F6FEB",
                   linewidth=1.6, zorder=10)

    ax.axvline(0, color="#8B949E", lw=1.0, linestyle="--", zorder=1)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Coeficiente estimado", fontsize=10)
    ax.set_title(f"Coeficientes con IC al 95%, 90% y 85% · {target_col}",
                 fontsize=12, fontweight="bold", pad=12)
    ax.grid(True, axis="x", alpha=0.3)

    from matplotlib.lines import Line2D
    handles = [
        Line2D([0], [0], color="#58A6FF", alpha=0.20, lw=8, label="IC 95%"),
        Line2D([0], [0], color="#58A6FF", alpha=0.45, lw=8, label="IC 90%"),
        Line2D([0], [0], color="#58A6FF", alpha=0.80, lw=8, label="IC 85%"),
        Line2D([0], [0], marker="o", color="w",
               markerfacecolor="#FFFFFF", markeredgecolor="#1F6FEB",
               markersize=8, label="Estimación puntual"),
    ]
    ax.legend(handles=handles, loc="lower right", fontsize=9, ncol=4,
              framealpha=0.9)

    fig.tight_layout()
    return fig


def _plot_residuals(model_df, date_col, residuos, y, y_pred):
    fig = Figure(figsize=(11, 7), dpi=100)
    fig.suptitle("Diagnóstico de residuos", fontsize=12, fontweight="bold")

    if date_col and date_col in model_df.columns:
        x = pd.to_datetime(model_df[date_col]).values
    else:
        x = np.arange(len(residuos))

    ax1 = fig.add_subplot(2, 2, 1)
    ax1.axhline(0, color="#8B949E", lw=1, ls="--")
    ax1.scatter(x, residuos, s=16, color="#F85149", alpha=0.7)
    ax1.set_title("Residuos vs tiempo", fontsize=10)
    ax1.set_xlabel(date_col or "Índice")
    ax1.set_ylabel("Residuo")
    ax1.grid(True, alpha=0.3)

    ax2 = fig.add_subplot(2, 2, 2)
    ax2.axhline(0, color="#8B949E", lw=1, ls="--")
    ax2.scatter(y_pred, residuos, s=16, color="#D29922", alpha=0.7)
    ax2.set_title("Residuos vs predicho", fontsize=10)
    ax2.set_xlabel("Predicho")
    ax2.set_ylabel("Residuo")
    ax2.grid(True, alpha=0.3)

    ax3 = fig.add_subplot(2, 2, 3)
    res_sorted = np.sort(residuos)
    n = len(res_sorted)
    probs = (np.arange(1, n + 1) - 0.5) / n
    from statistics import NormalDist
    q_theo = np.array([NormalDist().inv_cdf(p) for p in probs])
    mu = res_sorted.mean()
    sigma = res_sorted.std(ddof=1) if n > 1 else 1.0
    q_theo_scaled = mu + sigma * q_theo

    ax3.scatter(q_theo_scaled, res_sorted, s=14, color="#58A6FF", alpha=0.7)
    lims = [min(q_theo_scaled.min(), res_sorted.min()),
            max(q_theo_scaled.max(), res_sorted.max())]
    ax3.plot(lims, lims, color="#F85149", lw=1.4, ls="--")
    ax3.set_title("QQ plot · Normalidad", fontsize=10)
    ax3.set_xlabel("Cuantiles teóricos")
    ax3.set_ylabel("Cuantiles observados")
    ax3.grid(True, alpha=0.3)

    ax4 = fig.add_subplot(2, 2, 4)
    ax4.hist(residuos, bins=30, color="#3FB950", alpha=0.65,
             edgecolor="white", linewidth=0.4)
    ax4.axvline(0, color="#8B949E", lw=1, ls="--")
    ax4.axvline(mu, color="#F85149", lw=1.4, label=f"Media = {mu:.2f}")
    ax4.set_title("Histograma de residuos", fontsize=10)
    ax4.set_xlabel("Residuo")
    ax4.set_ylabel("Frecuencia")
    ax4.grid(True, axis="y", alpha=0.3)
    ax4.legend(fontsize=8)

    if date_col and date_col in model_df.columns:
        _format_date_axis(ax1, x, rotation=30)

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    return fig
