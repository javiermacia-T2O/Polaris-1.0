"""Procesamiento puro del constructor de tablas.

Este módulo no depende de Tk. Mantiene el pipeline pandas/DuckDB y libera
siempre los registros temporales de DuckDB.
"""

import time as _time
from collections import OrderedDict
import hashlib
import threading

import pandas as pd

from core import engine as db_engine
from core import memory_budget as _memory
from core.diagnostics import debug_enabled, log_debug, memory_snapshot
from models.table_recipe import TableRecipe
from services.active_dataset import ActiveDataset
from services.table_result_cache import materialize_table_result


_SQL_AGG_MAP = {
    "sum": "SUM",
    "mean": "AVG",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNT",
    "median": "MEDIAN",
    "std": "STDDEV",
    "first": "FIRST",
    "last": "LAST",
    # nunique se maneja aparte con COUNT(DISTINCT CASE WHEN ...)
}

# DuckDB permite ``MIN``/``MAX`` sobre texto, pero las métricas estadísticas
# requieren una columna numérica. Validarlo antes de ejecutar evita que una
# selección accidental del constructor termine en ``SUM(VARCHAR)``.
_NUMERIC_AGGS = {"sum", "mean", "median", "std"}
_ZERO_FILLED_PIVOT_AGGS = {"sum", "count", "nunique"}
_PREVIEW_SOURCE_LIMIT = 4096


def _is_numeric_type(dataset: ActiveDataset, column: str,
                     recipe: TableRecipe) -> bool:
    typed = dict(recipe.column_types).get(column)
    if typed == "numero":
        return True
    if typed in {"texto", "categorica", "fecha"}:
        return False
    try:
        dtype = dict(zip(dataset.columns, dataset.types)).get(column, "")
        # DuckDB describe names (DECIMAL, DOUBLE, BIGINT, ...), plus pandas
        # dtype strings for the registered DataFrame compatibility path.
        upper = str(dtype).upper()
        return (any(token in upper for token in
                    ("INT", "DECIMAL", "NUMERIC", "DOUBLE", "FLOAT",
                     "REAL", "HUGEINT", "BIGNUM"))
                or pd.api.types.is_numeric_dtype(dtype))
    except (TypeError, ValueError):
        return False


_PREVIEW_CACHE = OrderedDict()
_PIVOT_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()


def _cached(cache, key, loader, maximum=32):
    with _CACHE_LOCK:
        if key in cache:
            cache.move_to_end(key)
            log_debug("QUERY", "cache HIT", key=key[:12])
            return cache[key]
    log_debug("QUERY", "cache MISS", key=key[:12])
    value = loader()
    if (isinstance(value, pd.DataFrame)
            and value.memory_usage(deep=True).sum() > 2 * 1024**2):
        log_debug("QUERY", "preview cache SKIP oversized", key=key[:12])
        return value
    with _CACHE_LOCK:
        cache[key] = value
        if len(cache) > maximum:
            cache.popitem(last=False)
    return value


def _recipe_source(dataset: ActiveDataset, recipe: TableRecipe):
    combined = {column: set(values) for column, values in dataset.filters}
    for column, allowed in recipe.filters:
        combined[column] = (combined[column] & set(allowed)
                            if column in combined else set(allowed))
    filtered = dataset.with_filters(combined)
    selected = list(dict.fromkeys([
        *recipe.selected, *recipe.rows, *recipe.columns,
        *(column for column, _ in recipe.values)]))
    if not recipe.values and not selected:
        ignored = {col for col, kind in recipe.column_types
                   if kind == "ignorar"}
        selected = [col for col in filtered.columns if col not in ignored]
        if not selected:
            raise ValueError("Todas las columnas están ignoradas")
    if any(column not in dataset.columns for column in selected):
        raise KeyError("Columna del constructor ausente en el dataset activo")
    where, params = filtered._where()
    source = filtered._source_sql()
    typed = dict(recipe.column_types)
    if typed:
        needed = list(dict.fromkeys([
            *(selected or filtered.columns),
            *(col for col, _ in filtered.filters),
            *((filtered.date_bounds[0],) if filtered.date_bounds else ())]))
        expressions = []
        for column in needed:
            quoted = f'"{_sql_ident(column)}"'
            kind = typed.get(column)
            if kind == "numero":
                expressions.append(f'CAST({quoted} AS DOUBLE) AS {quoted}')
            elif kind == "fecha":
                expressions.append(
                    f'CAST(CAST({quoted} AS VARCHAR) AS TIMESTAMP) AS {quoted}')
            elif kind == "categorica":
                expressions.append(f"CAST({quoted} AS VARCHAR) AS {quoted}")
            else:
                expressions.append(quoted)
        source = f"(SELECT {', '.join(expressions)} FROM {source}) AS _typed"
    return filtered, selected, source, where, filtered._params(params)


def _pivot_categories(dataset, recipe, columns, source, where, params,
                      *, sample_rows: int | None = None):
    """Return pivot headings, optionally from the same bounded preview source.

    A preview must never scan a multi-million-row source merely to render
    category headings.  The full table still uses every filtered row; the
    preview uses a bounded, explicitly approximate sample when necessary.
    """
    columns = tuple(columns)
    key = hashlib.sha256(repr(("pivot", dataset.version_token(), columns,
                              recipe.column_types, sample_rows))
                         .encode("utf-8")).hexdigest()
    def load():
        category_source = f"{source}{where}"
        expressions = ", ".join(f'"{_sql_ident(column)}"' for column in columns)
        if sample_rows is not None:
            category_source = (
                f"(SELECT {expressions} FROM {category_source} "
                f"LIMIT {sample_rows}) AS _pivot_sample")
        rows = db_engine.get_conn().execute(
            f'SELECT DISTINCT {expressions} FROM {category_source} '
            f'ORDER BY {", ".join(f"{index + 1} NULLS FIRST" for index in range(len(columns)))} '
            'LIMIT 51', params).fetchall()
        if len(rows) > 50:
            raise MemoryError("Pivot con más de 50 columnas; filtra sus valores")
        return tuple(tuple(row) for row in rows)
    return _cached(_PIVOT_CACHE, key, load)


def _category_label(category) -> str:
    """Keep null category headings readable and deterministic."""
    def component(value):
        if value is None:
            return "(vacío)"
        text = str(value)
        return f'"{text}"' if text == "(vacío)" or " | " in text else text
    values = category if isinstance(category, tuple) else (category,)
    return " | ".join(component(value) for value in values)


def _aggregate_expression(metric: str, aggregation: str) -> str:
    """Return the SQL aggregate expression after its validation is complete."""
    return (f"COUNT(DISTINCT {metric})" if aggregation == "nunique" else
            f"{_SQL_AGG_MAP[aggregation]}({metric})")


def _pivot_aggregate_expression(metric: str, aggregation: str) -> str:
    """Keep a missing additive pivot cell consistent across every pipeline.

    Zero has a clear meaning for sums and counts.  For averages, extrema and
    first/last, a missing value remains null because replacing it with zero
    would fabricate an observation.
    """
    expression = _aggregate_expression(metric, aggregation)
    return (f"COALESCE({expression}, 0)"
            if aggregation in _ZERO_FILLED_PIVOT_AGGS else expression)


def compile_table(dataset: ActiveDataset, recipe: TableRecipe,
                  *, preview=False):
    """Compile both modes from one recipe; preview bounds the filtered source."""
    if dataset.backend == "pandas":
        raise ValueError("Registra primero el DataFrame para compilar la tabla")
    filtered, selected, source, where, params = _recipe_source(dataset, recipe)
    has_aggregate = bool(recipe.values)
    value_pivots = recipe.pivots_for_values()
    has_pivoted_value = any(value_pivots)
    blank_column_preview = not has_aggregate and bool(recipe.columns)
    projected = selected or list(filtered.columns)
    source_sql = ("SELECT " + ", ".join(
        f'"{_sql_ident(column)}"' for column in projected)
        + f" FROM {source}{where}")
    approximate = False
    bounded_preview = preview and (
        has_aggregate or recipe.order or blank_column_preview)
    if bounded_preview:
        # A bounded existence query avoids claiming approximation for small data.
        more = db_engine.get_conn().execute(
            f"SELECT 1 FROM {source}{where} "
            f"LIMIT 1 OFFSET {_PREVIEW_SOURCE_LIMIT}", params).fetchone()
        approximate = more is not None
        source_sql += f" LIMIT {_PREVIEW_SOURCE_LIMIT}"
    base = f"({source_sql}) AS _filtered"
    # A single trailing LIMIT is appended for previews.  Some branches already
    # need their own LIMIT (a heading-only preview keeps one synthetic row), so
    # track it here to avoid emitting an invalid double LIMIT clause.
    limit_applied = False
    pivot_params = []
    if blank_column_preview:
        categories = _pivot_categories(filtered, recipe, recipe.columns,
                                       source, where, params,
                                       sample_rows=(_PREVIEW_SOURCE_LIMIT
                                                    if bounded_preview else None))
        groups = list(dict.fromkeys(recipe.rows))
        blank_cells = [
            f'CAST(NULL AS VARCHAR) AS "{_sql_ident(_category_label(category))}"'
            for category in categories
        ]
        group_sql = ", ".join(f'"{_sql_ident(c)}"' for c in groups)
        select_sql = ", ".join([*([group_sql] if groups else []), *blank_cells])
        # With no row identifier, retain a single empty row so the preview can
        # render the selected column headings.  Otherwise every row identifier
        # is shown once and the category cells intentionally remain null.
        if groups:
            sql = f"SELECT DISTINCT {select_sql} FROM {base}"
        else:
            sql = f"SELECT {select_sql} FROM {base} LIMIT 1"
            limit_applied = True
    elif not has_aggregate:
        # Without a metric the table is a dimension listing.  Selecting a
        # variable as Fila/Columna must project only the assigned dimensions,
        # never the whole dataset.
        projected = list(dict.fromkeys([
            *recipe.rows, *recipe.columns])) or list(
                recipe.selected or tuple(selected) or filtered.columns)
        expressions = ", ".join(f'"{_sql_ident(c)}"' for c in projected)
        sql = f"SELECT {expressions} FROM {base}"
    else:
        pivot = has_pivoted_value and bool(recipe.columns)
        groups = list(dict.fromkeys(
            recipe.rows if pivot else [*recipe.rows, *recipe.columns]))
        values = []
        categories = (_pivot_categories(filtered, recipe, recipe.columns,
                                       source, where, params,
                                       sample_rows=(_PREVIEW_SOURCE_LIMIT
                                                    if bounded_preview else None))
                      if pivot else ())
        pivoted_metric_count = sum(value_pivots)
        for (metric_name, aggregation), metric_pivoted in zip(
                recipe.values, value_pivots):
            metric = f'"{_sql_ident(metric_name)}"'
            # A missing aggregation is treated as a count, which is safe for
            # both text and numeric dimensions. Numeric operations on text are
            # rejected here with a useful message instead of a DuckDB binder
            # error several layers later.
            aggregation = (str(aggregation).strip().lower()
                           if aggregation else "count")
            if aggregation != "nunique" and aggregation not in _SQL_AGG_MAP:
                raise ValueError(f"Agregación no soportada: {aggregation}")
            if (aggregation in _NUMERIC_AGGS
                    and not _is_numeric_type(filtered, metric_name, recipe)):
                dtype = dict(zip(filtered.columns, filtered.types)).get(
                    metric_name, "desconocido")
                raise ValueError(
                    f"La agregación '{aggregation}' requiere una métrica "
                    f"numérica: '{metric_name}' es de tipo {dtype}. "
                    "Usa 'count' para valores textuales o marca la columna "
                    "como número si su contenido es convertible.")
            if pivot and metric_pivoted:
                for category in categories:
                    condition = " AND ".join(
                        f'"{_sql_ident(column)}" IS NOT DISTINCT FROM ?'
                        for column in recipe.columns)
                    pivot_params.extend(category)
                    term = f"CASE WHEN {condition} THEN {metric} END"
                    expression = _pivot_aggregate_expression(term, aggregation)
                    label = (_category_label(category)
                             if pivoted_metric_count == 1 else
                             f"{metric_name} · {_category_label(category)}")
                    values.append(f'{expression} AS "{_sql_ident(label)}"')
            else:
                expression = _aggregate_expression(metric, aggregation)
                values.append(f'{expression} AS "{_sql_ident(metric_name)}"')
        if len(values) > 500:
            raise MemoryError("Resultado demasiado ancho para el constructor")
        group_sql = ", ".join(f'"{_sql_ident(c)}"' for c in groups)
        select_sql = ", ".join([*([group_sql] if groups else []), *values])
        sql = f"SELECT {select_sql} FROM {base}"
        if groups:
            sql += f" GROUP BY {group_sql}"
    if recipe.order:
        if blank_column_preview:
            result_columns = [*recipe.rows,
                              *(_category_label(category)
                                for category in categories)]
        elif not has_aggregate:
            result_columns = list(recipe.selected or tuple(selected)
                                  or filtered.columns)
        elif has_pivoted_value and recipe.columns:
            pivoted_metric_count = sum(value_pivots)
            result_columns = [*recipe.rows]
            for (metric, _), metric_pivoted in zip(recipe.values, value_pivots):
                if metric_pivoted:
                    result_columns.extend(
                        _category_label(category)
                        if pivoted_metric_count == 1 else
                        f"{metric} · {_category_label(category)}"
                        for category in categories)
                else:
                    result_columns.append(metric)
        else:
            result_columns = [*recipe.rows, *recipe.columns,
                              *(name for name, _ in recipe.values)]
        for column, _ in recipe.order:
            if column not in result_columns:
                raise KeyError(f"Columna de orden ausente: {column}")
        sql += " ORDER BY " + ", ".join(
            f'"{_sql_ident(column)}" {"ASC" if asc else "DESC"}'
            for column, asc in recipe.order)
    elif (has_aggregate or blank_column_preview) and groups:
        sql += " ORDER BY " + ", ".join(
            f'"{_sql_ident(column)}"' for column in groups)
    if preview:
        if not limit_applied:
            sql += " LIMIT 20"
    # Placeholders in SELECT precede placeholders in the nested FROM/WHERE.
    return sql, tuple([*pivot_params, *params]), approximate


def build_table_view(dataset: ActiveDataset, recipe: TableRecipe):
    sql, params, _ = compile_table(dataset, recipe, preview=False)
    return ActiveDataset.from_query(
        sql, params, dataset, recipe.fingerprint(dataset, "full"))


def build_table_active_snapshot(dataset: ActiveDataset, recipe: TableRecipe,
                                value_filters: dict, date_column: str | None,
                                cancel=None, progress=None):
    """Valida el resultado completo y prepara la primera página antes de activarlo.

    No materializa la tabla completa en Pandas. El recuento y la vista previa
    se ejecutan en el mismo worker para que la UI confirme la aplicación solo
    cuando los datos nuevos ya se pueden mostrar.
    """
    def report(percent, message):
        if progress is not None:
            progress((percent, message))

    report(5, "Construyendo consulta de la tabla...")
    view = build_table_view(dataset, recipe)
    if recipe.values:
        # Las agregaciones se volverían a ejecutar sobre el origen completo
        # cada vez que se consultara una página o se iniciara un análisis.
        view = materialize_table_result(view, cancel=cancel, progress=progress)
    else:
        report(75, "Vista directa preparada...")
    filters = {col: values for col, values in value_filters.items()
               if col in view.columns and values is not None}
    if filters:
        view = view.with_filters(filters)
    report(80, "Contando filas del resultado...")
    rows = view.row_count(cancel)
    if rows == 0:
        raise ValueError("La tabla resultante está vacía.")
    report(90, "Cargando las primeras filas...")
    preview = view.preview(cancel=cancel)
    stats = {"rows": rows, "columns": len(view.columns), "nulls": {}}
    if date_column in view.columns:
        report(96, "Comprobando el rango de fechas...")
        stats["date_range"] = view.date_range(date_column, cancel)
    report(100, "Tabla lista")
    return view, stats, preview


def preview_to_pandas(dataset: ActiveDataset, recipe: TableRecipe):
    dataset._check_source()
    key = recipe.fingerprint(dataset, "preview")
    def load():
        sql, params, approximate = compile_table(dataset, recipe, preview=True)
        started = _time.perf_counter()
        result = db_engine.get_conn().execute(sql, params).fetchdf()
        result.attrs["approximate"] = approximate
        if debug_enabled():
            log_debug("QUERY", "table preview", backend=dataset.backend,
                      version=dataset.version_token()[:12], recipe=key[:12],
                      query=hashlib.sha256(sql.encode()).hexdigest()[:12],
                      approx_preview=approximate,
                      output_rows=len(result), output_cols=len(result.columns),
                      materialized_rows=len(result),
                      elapsed_ms=round((_time.perf_counter()-started)*1000, 1),
                      **memory_snapshot())
        return result
    return _cached(_PREVIEW_CACHE, key, load).copy()


def materialize_if_safe(view: ActiveDataset, reserved_bytes: int = 0):
    if view.row_count() > 100_000:
        raise MemoryError("La tabla supera 100.000 filas; filtra o agrega antes")
    return view.analysis_frame(reserved_bytes=reserved_bytes)


def build_table_full_pandas(df, recipe: TableRecipe):
    name = db_engine.register(df)
    try:
        registered = ActiveDataset(
            name, "registered", tuple(str(c) for c in df.columns),
            tuple(str(dtype) for dtype in df.dtypes),
            version_hint=f"{id(df)}:{df.shape}:{tuple(map(str, df.dtypes))}")
        return materialize_if_safe(
            build_table_view(registered, recipe),
            reserved_bytes=int(df.memory_usage(deep=True).sum()))
    finally:
        db_engine.unregister(name)


def build_table_lazy(dataset: ActiveDataset, rows, cols, val_specs,
                     filters=None, preview=False):
    """Compatibility entrypoint; the UI uses build_table_view for FULL."""
    # Each value spec owns its pivot state.  The recipe-wide default remains
    # true here for callers predating the per-card control; explicit False
    # values keep the prior flat grouping behavior.
    recipe = TableRecipe.from_parts(rows, cols, val_specs, filters, pivot=True)
    if preview:
        return preview_to_pandas(dataset, recipe)
    return materialize_if_safe(build_table_view(dataset, recipe))


def build_table_preview_pandas(df, rows, cols, val_specs, filters=None,
                               recipe: TableRecipe | None = None):
    """Build a bounded preview from the whole resident input on a worker."""
    name = db_engine.register(df)
    try:
        registered = ActiveDataset(
            name, "registered", tuple(str(c) for c in df.columns),
            tuple(str(dtype) for dtype in df.dtypes),
            version_hint=f"{id(df)}:{df.shape}:{tuple(map(str, df.dtypes))}")
        recipe = recipe or TableRecipe.from_parts(
            rows, cols, val_specs, filters, pivot=True)
        return preview_to_pandas(registered, recipe)
    finally:
        db_engine.unregister(name)


def _sql_escape(s: str) -> str:
    """Escapa comillas simples para SQL."""
    return str(s).replace("'", "''")


def _sql_ident(s: str) -> str:
    """Escapa comillas dobles para identificadores SQL."""
    return str(s).replace('"', '""')


def _build_pivot_select(vals_df, cols, value_cols, agg_map):
    """
    Construye la lista de expresiones SQL para el pivot:

        COALESCE(
            SUM(CASE WHEN col1='v1' AND col2='v2' THEN "metric" END),
            0
        ) AS "metric · v1 | v2"

    Args:
        vals_df: DataFrame con las combinaciones únicas de `cols`
        cols: lista de columnas del pivot (las que van a columnas)
        value_cols: lista de métricas
        agg_map: dict {métrica: 'sum'|'mean'|...}

    Returns:
        list[str] de expresiones SQL listas para pegar en el SELECT
    """
    select_parts = []

    if vals_df is None or len(vals_df) == 0:
        return select_parts

    for _, row in vals_df.iterrows():
        # --- Condición SQL: todas las cols del pivot coinciden ---
        cond_parts = []
        for c in cols:
            v = _sql_escape(row[c])
            safe_c = _sql_ident(c)
            cond_parts.append(f'"{safe_c}" = \'{v}\'')
        cond = " AND ".join(cond_parts)

        # --- Etiqueta del pivot ---
        label_parts = [str(row[c]) for c in cols]
        base_label = " | ".join(label_parts)

        # --- Por cada value_col ---
        for vcol in value_cols:
            if len(value_cols) == 1:
                col_name = base_label
            else:
                col_name = f"{vcol} · {base_label}"
            col_name_safe = _sql_ident(col_name)

            func = agg_map.get(vcol, "sum")

            if func == "nunique":
                expr = (f'COUNT(DISTINCT CASE WHEN {cond} '
                        f'THEN "{_sql_ident(vcol)}" END)')
            else:
                sql_func = _SQL_AGG_MAP.get(func, "SUM")
                expr = (f'{sql_func}(CASE WHEN {cond} '
                        f'THEN "{_sql_ident(vcol)}" END)')

            # Mantener la misma semántica que la ruta lazy: cero solo para
            # agregaciones aditivas o de recuento; null para medidas cuyo cero
            # inventaría un dato (media, mínimo, máximo, etc.).
            if func in _ZERO_FILLED_PIVOT_AGGS:
                expr = f"COALESCE({expr}, 0)"

            select_parts.append(f'{expr} AS "{col_name_safe}"')

    return select_parts


# ==================================================================
# SQL BUILDERS · para el pipeline con CTE
# ==================================================================
def _build_total_sql(cte_name, agg_map):
    """SQL: SELECT aggs FROM cte."""
    parts = []
    for c, func in agg_map.items():
        f = func.upper()
        if func == "nunique":
            parts.append(f'COUNT(DISTINCT "{c}") AS "{c}"')
        else:
            parts.append(f'{f}("{c}") AS "{c}"')
    return f"SELECT {', '.join(parts)} FROM {cte_name}"


def _build_groupby_sql(cte_name, by, agg_map):
    """SQL: SELECT by, aggs FROM cte GROUP BY by."""
    by_cols = ", ".join(f'"{c}"' for c in by)
    parts = []
    for c, func in agg_map.items():
        f = func.upper()
        if func == "nunique":
            parts.append(f'COUNT(DISTINCT "{c}") AS "{c}"')
        else:
            parts.append(f'{f}("{c}") AS "{c}"')
    return (f"SELECT {by_cols}, {', '.join(parts)} "
            f"FROM {cte_name} GROUP BY {by_cols}")


def _build_pivot_cte_sql(cte_name, rows, cols, value_cols,
                         agg_map, vals_df):
    """
    Construye la query de pivot usando las combinaciones ya obtenidas
    del CTE normalizado y filtrado.
    """
    rows_sql = ", ".join(f'"{c}"' for c in rows)

    if vals_df.empty:
        return f"SELECT DISTINCT {rows_sql} FROM {cte_name}"

    print(f"[TB-PIVOT] {len(vals_df)} cols únicas · "
          f"{len(value_cols)} valores · "
          f"{len(vals_df) * len(value_cols)} columnas salida")

    select_parts = _build_pivot_select(vals_df, cols, value_cols, agg_map)

    return (f"SELECT {rows_sql}, {', '.join(select_parts)} "
            f"FROM {cte_name} GROUP BY {rows_sql}")


def _build_pivot_norows_cte_sql(cte_name, cols, value_cols,
                                 agg_map, vals_df):
    """Pivot sin filas con valores ya leídos del CTE."""

    if vals_df.empty:
        return "SELECT 1"

    select_parts = _build_pivot_select(vals_df, cols, value_cols, agg_map)
    return f"SELECT {', '.join(select_parts)} FROM {cte_name}"


def _pivot_values_from_cte(cte_sql, cols, rows, source, value_count):
    """Acota el pivot antes de pasar combinaciones distintas a Pandas.

    Se reservan 32 bytes por celda de salida (valor, nulos y transitorios) y
    64 KiB por expresión SQL para la planificación de DuckDB. Esta estimación
    conservadora comparte el presupuesto Pandas con el DataFrame residente.
    """
    conn = db_engine.get_conn()
    if rows:
        rows_sql = ", ".join(f'"{_sql_ident(c)}"' for c in rows)
        group_count = conn.execute(
            f"{cte_sql}\nSELECT COUNT(*) FROM ("
            f"SELECT DISTINCT {rows_sql} FROM _norm) AS _groups"
        ).fetchone()[0]
    else:
        group_count = 1

    resident_bytes = int(source.memory_usage(deep=True).sum())
    free_bytes = max(0, _memory.pandas_limit_bytes() - resident_bytes)
    bytes_per_combination = max(1, value_count) * (
        max(1, group_count) * 32 + 64 * 1024
    )
    max_combinations = min(len(source), free_bytes // bytes_per_combination)

    cols_sql = ", ".join(f'"{_sql_ident(c)}"' for c in cols)
    vals_df = db_engine.query_arrow(
        f"{cte_sql}\nSELECT DISTINCT {cols_sql} FROM _norm "
        f"ORDER BY {cols_sql} LIMIT {max_combinations + 1}"
    ).to_pandas()
    if len(vals_df) > max_combinations:
        raise MemoryError(
            "Pivot rechazado por memoria: demasiadas combinaciones de "
            "columnas para el presupuesto conjunto de Pandas y DuckDB. "
            "Filtra los datos o reduce las columnas del pivot."
        )
    return vals_df


def _duck_full_pipeline(df, filters, rows, cols, val_specs,
                         col_types, t_start, tick=None):
    """
    Pipeline COMPLETO del constructor en UNA sola query DuckDB con CTE.

    Optimizaciones:
      1. CTE en lugar de CREATE TEMP TABLE → sin materializar intermedio
      2. Solo registra las columnas que se usan → 5x menos datos
      3. No aplica CAST si el dtype ya es correcto
      4. Pivot directo en el CTE → 1 sola materialización al final
    """
    import time as _t

    def _log(msg):
        print(f"[TB-DUCK] {msg}", flush=True)

    # =========================================================
    # PASO 1 · ¿Qué columnas necesitamos?
    # =========================================================
    cols_pivot = set(cols)
    cols_rows = set(rows)
    cols_vals = {v["col"] for v in val_specs}
    cols_filter = set((filters or {}).keys())

    cols_needed = cols_pivot | cols_rows | cols_vals | cols_filter

    # La ruta sin agregaciones muestra también las fechas configuradas.
    for c in df.columns:
        if col_types.get(str(c)) == "fecha":
            cols_needed.add(c)

    if not cols_needed:
        cols_needed = set(df.columns)

    # Filtrar solo las columnas que existen
    cols_needed = [c for c in df.columns if c in cols_needed]

    _log(f"Columnas usadas: {len(cols_needed)} de {len(df.columns)}")
    _log(f"  pivot={len(cols_pivot)}  rows={len(cols_rows)}  "
         f"vals={len(cols_vals)}  filtros={len(cols_filter)}")

    # =========================================================
    # PASO 2 · Proyectar el df antes de registrarlo
    # =========================================================
    # Registrar únicamente las columnas necesarias para el resultado y las
    # fechas que el comportamiento existente incluye en la ruta sin valores.
    df_min = df.loc[:, cols_needed]

    # =========================================================
    # PASO 3 · Registrar SOLO esas columnas en DuckDB
    # =========================================================
    t_reg = db_engine.register(df_min, "_build_src")

    try:
        # =====================================================
        # PASO 4 · Construir SELECT solo para columnas usadas
        # =====================================================
        t_select = _t.time()
        select_parts = []

        for c in df.columns:
            if c not in cols_needed:
                continue

            tipo = col_types.get(str(c), "")
            dtype = df[c].dtype
            safe_c = str(c).replace('"', '""')

            # --- Caso A: columna ya datetime ---
            if pd.api.types.is_datetime64_any_dtype(dtype):
                if tipo == "fecha":
                    select_parts.append(
                        f'CAST("{safe_c}" AS DATE) AS "{safe_c}"')
                else:
                    select_parts.append(f'"{safe_c}"')
                continue

            # --- Caso B: columna ya numérica ---
            if pd.api.types.is_numeric_dtype(dtype):
                select_parts.append(f'"{safe_c}"')
                continue

            # --- Caso C: columna object/string ---
            if tipo == "categorica":
                if df[c].isna().any():
                    select_parts.append(
                        f"COALESCE(CAST(\"{safe_c}\" AS VARCHAR), "
                        f"'(vacío)') AS \"{safe_c}\"")
                else:
                    select_parts.append(f'"{safe_c}"')
            elif tipo == "numero":
                select_parts.append(
                    f'CAST("{safe_c}" AS DOUBLE) AS "{safe_c}"')
            elif tipo == "fecha":
                select_parts.append(
                    f'CAST(CAST("{safe_c}" AS VARCHAR) AS TIMESTAMP) '
                    f'AS "{safe_c}"')
            else:
                select_parts.append(f'"{safe_c}"')

        _log(f"SELECT preparado: {len(select_parts)} columnas "
             f"en {_t.time()-t_select:.2f}s")

        # =====================================================
        # PASO 5 · WHERE (filtros)
        # =====================================================
        where_parts = []
        for col, allowed in (filters or {}).items():
            if col not in df.columns or not allowed:
                continue
            safe_col = str(col).replace('"', '""')
            valores = ", ".join(
                "'" + str(v).replace("'", "''") + "'"
                for v in allowed
            )
            where_parts.append(
                f'CAST("{safe_col}" AS VARCHAR) IN ({valores})')

        where_sql = " AND ".join(where_parts) if where_parts else "1=1"

        # =====================================================
        # PASO 6 · Separar val_specs
        # =====================================================
        pivot_vals = [v for v in val_specs if v.get("pivot", True)]
        flat_vals = [v for v in val_specs if not v.get("pivot", True)]

        # =====================================================
        # PASO 7 · CTE + pivot en UNA query
        # =====================================================
        t_query = _t.time()

        cte_sql = (
            f"WITH _norm AS (\n"
            f"    SELECT {', '.join(select_parts)}\n"
            f"    FROM {t_reg}\n"
            f"    WHERE {where_sql}\n"
            f")"
        )

        # -----------------------------------------------------
        # CASO A · Sin valores
        # -----------------------------------------------------
        if not val_specs:
            sql = f"{cte_sql}\nSELECT * FROM _norm"
            _log("Ejecutando (sin pivot)...")
            result = db_engine.query_arrow(sql).to_pandas()
            _log(f"Resultado: {result.shape}")

        # -----------------------------------------------------
        # CASO B · Solo flat_vals
        # -----------------------------------------------------
        elif not pivot_vals:
            agg_map = {v["col"]: v["agg"] for v in flat_vals}
            flat_groups = list(dict.fromkeys([*rows, *cols]))
            if not flat_groups:
                sql = f"{cte_sql}\n" + _build_total_sql("_norm", agg_map)
            else:
                sql = f"{cte_sql}\n" + _build_groupby_sql(
                    "_norm", flat_groups, agg_map)

            _log("Ejecutando (solo flat_vals)...")
            result = db_engine.query_arrow(sql).to_pandas()

        # -----------------------------------------------------
        # CASO C · Con pivot
        # -----------------------------------------------------
        else:
            agg_pivot_map = {v["col"]: v["agg"] for v in pivot_vals}
            value_cols = [v["col"] for v in pivot_vals]

            if rows and cols:
                vals_df = _pivot_values_from_cte(
                    cte_sql, cols, rows, df, len(value_cols))
                pivot_sql = _build_pivot_cte_sql(
                    "_norm", rows, cols, value_cols, agg_pivot_map, vals_df)
                sql = f"{cte_sql}\n{pivot_sql}"
            elif cols and not rows:
                vals_df = _pivot_values_from_cte(
                    cte_sql, cols, rows, df, len(value_cols))
                pivot_sql = _build_pivot_norows_cte_sql(
                    "_norm", cols, value_cols, agg_pivot_map, vals_df)
                sql = f"{cte_sql}\n{pivot_sql}"
            elif rows and not cols:
                sql = f"{cte_sql}\n" + _build_groupby_sql(
                    "_norm", rows, agg_pivot_map)
            else:
                sql = f"{cte_sql}\n" + _build_total_sql(
                    "_norm", agg_pivot_map)

            _log("Ejecutando (con pivot)...")
            _log(f"SQL: {len(sql):,} chars".replace(",", "."))
            result = db_engine.query_arrow(sql).to_pandas()

            # Añadir flat_vals si hay pivot + flat
            if flat_vals and rows:
                t_merge = _t.time()
                agg_flat = {v["col"]: v["agg"] for v in flat_vals}
                flat_sql = (
                    f"{cte_sql}\n"
                    + _build_groupby_sql("_norm", rows, agg_flat)
                )
                totals = db_engine.query_arrow(flat_sql).to_pandas()

                cols_to_add = [c for c in totals.columns
                               if c in rows
                               or c not in result.columns]
                totals = totals[cols_to_add]
                result = pd.merge(result, totals, on=rows, how="left")
                _log(f"Merge flat_vals: {_t.time()-t_merge:.2f}s")

        total = _t.time() - t_start
        print(f"[TB-BUILD] result.shape={result.shape}")
        print(f"[TB-BUILD] TOTAL: {total:.2f}s")
        print(f"{'='*60}\n")
        return result

    finally:
        db_engine.unregister(t_reg)
# ==================================================================
# HELPERS · PIVOT SOBRE TABLAS TEMPORALES (no registra de nuevo)
# ==================================================================
def _duck_pivot_on_table(table, rows, cols, value_cols, agg_map):
    """Pivot sobre una tabla DuckDB existente (no registra de nuevo)."""
    conn = db_engine.get_conn()
    cols_sql = ", ".join(f'"{c}"' for c in cols)
    rows_sql = ", ".join(f'"{c}"' for c in rows)

    vals_df = conn.execute(
        f"SELECT DISTINCT {cols_sql} FROM {table} ORDER BY {cols_sql}"
    ).df()

    if vals_df.empty:
        return conn.execute(
            f"SELECT DISTINCT {rows_sql} FROM {table}"
        ).df()

    select_parts = _build_pivot_select(vals_df, cols, value_cols, agg_map)

    print(f"[TB-PIVOT] {len(vals_df)} cols únicas · "
          f"{len(value_cols)} valores · "
          f"{len(vals_df) * len(value_cols)} columnas de salida")

    sql = (f"SELECT {rows_sql}, {', '.join(select_parts)} "
           f"FROM {table} GROUP BY {rows_sql}")

    return db_engine.query(sql)


def _duck_pivot_norows_on_table(table, cols, value_cols, agg_map):
    """Pivot sin filas sobre tabla DuckDB existente."""
    conn = db_engine.get_conn()
    cols_sql = ", ".join(f'"{c}"' for c in cols)

    vals_df = conn.execute(
        f"SELECT DISTINCT {cols_sql} FROM {table} ORDER BY {cols_sql}"
    ).df()

    if vals_df.empty:
        return pd.DataFrame()

    select_parts = _build_pivot_select(vals_df, cols, value_cols, agg_map)
    sql = f"SELECT {', '.join(select_parts)} FROM {table}"
    return db_engine.query(sql)


def _duck_groupby_on_table(table, by, agg_map):
    """GroupBy sobre tabla DuckDB existente."""
    by_cols = ", ".join(f'"{c}"' for c in by)
    parts = []
    for c, func in agg_map.items():
        f = func.upper()
        if func == "nunique":
            parts.append(f'COUNT(DISTINCT "{c}") AS "{c}"')
        else:
            parts.append(f'{f}("{c}") AS "{c}"')
    aggs = ", ".join(parts)
    sql = f"SELECT {by_cols}, {aggs} FROM {table} GROUP BY {by_cols}"
    return db_engine.query(sql)


def _duck_total_on_table(table, agg_map):
    """Total agregado sobre tabla DuckDB existente."""
    parts = []
    for c, func in agg_map.items():
        f = func.upper()
        if func == "nunique":
            parts.append(f'COUNT(DISTINCT "{c}") AS "{c}"')
        else:
            parts.append(f'{f}("{c}") AS "{c}"')
    sql = f"SELECT {', '.join(parts)} FROM {table}"
    row = db_engine.query(sql).iloc[0]
    return (row.to_frame("Valor")
               .reset_index()
               .rename(columns={"index": "Métrica"}))

def _duck_pivot_norows(df: pd.DataFrame, cols: list,
                        value_cols: list, agg_map: dict) -> pd.DataFrame:
    """Pivot sin filas: solo por columnas, una sola fila de resultado."""
    t = db_engine.register(df)
    col_sql = ", ".join(f'"{c}"' for c in cols)
    agg_parts = []
    for v in value_cols:
        func = agg_map[v].upper()
        if agg_map[v] == "nunique":
            agg_parts.append(f'COUNT(DISTINCT "{v}") AS "{v}"')
        else:
            agg_parts.append(f'{func}("{v}") AS "{v}"')
    val_sql = ", ".join(agg_parts)
    sql = (f"SELECT {col_sql}, {val_sql} "
           f"FROM {t} GROUP BY {col_sql}")
    long = db_engine.query(sql)
    db_engine.unregister(t)

    frames = []
    for metric in value_cols:
        pv = long.pivot(columns=cols, values=metric)
        if len(value_cols) == 1:
            pv.columns = [str(c) for c in pv.columns]
        else:
            pv.columns = [f"{metric} · {c}" for c in pv.columns]
        if agg_map[metric] in _ZERO_FILLED_PIVOT_AGGS:
            pv = pv.fillna(0)
        frames.append(pv)
    return pd.concat(frames, axis=1).reset_index(drop=True)

def _is_number(x) -> bool:
    """Comprueba si un valor se puede convertir a float."""
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False

# ==================================================================
# NORMALIZACIÓN Y AGREGACIÓN CON DUCKDB
# ==================================================================
def _duck_normalize_types(df, cols_usadas, tb_col_types):
    """
    Normaliza tipos (fecha, número, categoría) con DuckDB.
    10-20x más rápido que pandas sobre df grandes.
    """
    if not cols_usadas:
        return df

    t = db_engine.register(df, "_norm_tmp")
    try:
        select_parts = []
        for c in df.columns:
            tipo = tb_col_types.get(str(c), "")

            if c not in cols_usadas:
                select_parts.append(f'"{c}"')
                continue

            if (tipo == "fecha"
                    or pd.api.types.is_datetime64_any_dtype(df[c])):
                # CAST a TIMESTAMP y normalizar a DATE (sin hora)
                select_parts.append(
                    f'CAST(CAST("{c}" AS TIMESTAMP) AS DATE) '
                    f'AS "{c}"')
            elif tipo == "numero":
                select_parts.append(f'CAST("{c}" AS DOUBLE) AS "{c}"')
            elif tipo == "categorica":
                select_parts.append(
                    f"COALESCE(CAST(\"{c}\" AS VARCHAR), "
                    f"'(vacío)') AS \"{c}\"")
            else:
                select_parts.append(f'"{c}"')

        sql = f"SELECT {', '.join(select_parts)} FROM {t}"
        out = db_engine.query(sql)
        print(f"[TB-BUILD] normalización DuckDB OK: "
              f"{out.shape[0]:,} × {out.shape[1]}".replace(",", "."))
        return out
    finally:
        db_engine.unregister(t)


def _pandas_normalize_types(df, cols_usadas, tb_col_types):
    """
    Fallback pandas para DFs pequeños o cuando DuckDB no está.

    ⚠ Optimización: SOLO convierte columnas que realmente necesitan
    cambio. Si una columna ya es del tipo correcto, la deja intacta.
    Esto evita recorrer 22M filas cuando ya están bien.
    """
    import time as _tt

    for c in cols_usadas:
        t_col = tb_col_types.get(str(c), "")
        try:
            current = df[c].dtype

            # --- FECHA ---
            if t_col == "fecha":
                if pd.api.types.is_datetime64_any_dtype(current):
                    continue  # Ya es datetime, no tocar

                t0 = _tt.time()
                s = pd.to_datetime(df[c], errors="coerce")
                # Normalizar a fecha (sin hora) solo si hace falta
                try:
                    if ((s.dt.hour.fillna(0) != 0).any()
                            or (s.dt.minute.fillna(0) != 0).any()):
                        s = s.dt.normalize()
                except Exception:
                    pass
                df[c] = s
                print(f"[TB-NORM] '{c}' → fecha en "
                      f"{_tt.time()-t0:.2f}s")

            # --- NÚMERO ---
            elif t_col == "numero":
                if pd.api.types.is_numeric_dtype(current):
                    continue  # Ya es numérico
                t0 = _tt.time()
                df[c] = pd.to_numeric(df[c], errors="coerce")
                print(f"[TB-NORM] '{c}' → numérico en "
                      f"{_tt.time()-t0:.2f}s")

            # --- CATEGÓRICA ---
            elif t_col == "categorica":
                if isinstance(current, pd.CategoricalDtype):
                    df[c] = (df[c].astype(object)
                                  .where(df[c].notna(), "(vacío)")
                                  .astype(str))
                elif current == object:
                    df[c] = df[c].fillna("(vacío)").astype(str)
                # else: ya es str, no tocar

            # --- AUTO (object sin conversión específica) ---
            elif current == object:
                # Solo rellenar NaN, sin cambiar dtype
                if df[c].isna().any():
                    df[c] = df[c].fillna("(vacío)")

        except Exception as e:
            print(f"[TB-NORM] '{c}': {e}")

    return df

def _duck_groupby(df, by, agg_map):
    """GroupBy-Agg con DuckDB."""
    t = db_engine.register(df, "_gb_tmp")
    try:
        by_cols = ", ".join(f'"{c}"' for c in by)
        parts = []
        for c, func in agg_map.items():
            f = func.upper()
            if func == "nunique":
                parts.append(f'COUNT(DISTINCT "{c}") AS "{c}"')
            else:
                parts.append(f'{f}("{c}") AS "{c}"')
        aggs = ", ".join(parts)
        sql = (f"SELECT {by_cols}, {aggs} "
               f"FROM {t} GROUP BY {by_cols}")
        return db_engine.query(sql)
    finally:
        db_engine.unregister(t)


def _duck_total(df, agg_map):
    """Total agregado con DuckDB (una sola fila)."""
    t = db_engine.register(df, "_total_tmp")
    try:
        parts = []
        for c, func in agg_map.items():
            f = func.upper()
            if func == "nunique":
                parts.append(f'COUNT(DISTINCT "{c}") AS "{c}"')
            else:
                parts.append(f'{f}("{c}") AS "{c}"')
        sql = f"SELECT {', '.join(parts)} FROM {t}"
        row = db_engine.query(sql).iloc[0]
        return (row.to_frame("Valor")
                   .reset_index()
                   .rename(columns={"index": "Métrica"}))
    finally:
        db_engine.unregister(t)


def _pandas_pivot(df, rows, cols, value_cols, agg_map):
    """Pivot pandas (fallback para df pequeños)."""
    group_keys = rows + cols
    frames = []
    for metric in value_cols:
        agg = agg_map[metric]
        try:
            gb = (df.groupby(group_keys, dropna=False,
                              observed=True)[metric].agg(agg))
        except Exception:
            gb = (df.groupby(group_keys, dropna=False,
                              observed=True)[metric].sum())
        pv = gb.unstack(cols)
        if len(value_cols) == 1:
            pv.columns = [str(c) for c in pv.columns]
        else:
            pv.columns = [f"{metric} · {c}" for c in pv.columns]
        if agg in _ZERO_FILLED_PIVOT_AGGS:
            pv = pv.fillna(0)
        frames.append(pv)
    return pd.concat(frames, axis=1).reset_index()


def _pandas_pivot_norows(df, cols, value_cols, agg_map):
    """Pivot sin filas (fallback pandas)."""
    frames = []
    for metric in value_cols:
        agg = agg_map[metric]
        try:
            gb = (df.groupby(cols, dropna=False,
                              observed=True)[metric].agg(agg))
        except Exception:
            gb = (df.groupby(cols, dropna=False,
                              observed=True)[metric].sum())
        pv = gb.to_frame().T
        if len(value_cols) == 1:
            pv.columns = [str(c) for c in pv.columns]
        else:
            pv.columns = [f"{metric} · {c}" for c in pv.columns]
        if agg in _ZERO_FILLED_PIVOT_AGGS:
            pv = pv.fillna(0)
        frames.append(pv)
    return pd.concat(frames, axis=1).reset_index(drop=True)

# ==================================================================

def build_table(df, rows, cols, val_specs, filters, col_types, preview=False):
    """Construye una tabla aplicando roles, filtros y agregaciones.

    El modo preview recorta las primeras 20 filas de la fuente antes de
    aplicar filtros, igual que el comportamiento histórico de la interfaz.
    """
    def _tick(label, t0):
        dt = _time.time() - t0
        print(f"[TB-BUILD] {label}: {dt:.2f}s")
        return _time.time()

    t_start = _time.time()
    t = t_start
    if df is None:
        raise ValueError("Sin datos cargados.")

    n_original = len(df)
    es_big = n_original >= db_engine.UMBRAL_PANDAS
    usar_duck_full = not preview and db_engine._DUCKDB_OK
    print(f"[TB-BUILD] DIAG: preview={preview}  es_big={es_big}  "
          f"duckdb_ok={db_engine._DUCKDB_OK}  usar_duck_full={usar_duck_full}  "
          f"n_original={n_original:,}".replace(",", "."))

    if preview:
        preview_rows = 20
        df = df.head(preview_rows) if n_original > preview_rows else df
        print(f"[TB-BUILD] PREVIEW · {len(df)} filas")

    if usar_duck_full:
        return _duck_full_pipeline(
            df=df, filters=filters, rows=rows, cols=cols,
            val_specs=val_specs, col_types=col_types, t_start=t_start, tick=_tick,
        )

    if filters:
        for col, allowed in filters.items():
            if col in df.columns:
                n0 = len(df)
                df = df[df[col].astype(str).isin(allowed)]
                print(f"[TB-BUILD] filtro '{col}': {n0} → {len(df)}")
        t = _tick("filtros pandas", t)

    used = set(rows) | set(cols) | {v["col"] for v in val_specs} | set(filters)
    ignored = [
        c for c, col_type in col_types.items()
        if col_type == "ignorar" and c in df.columns and c not in used
    ]
    if ignored:
        df = df.drop(columns=ignored, errors="ignore")
    t = _tick("drop ignoradas", t)
    if df.empty or len(df.columns) == 0:
        raise ValueError("Todas las columnas están ignoradas.")

    cols_usadas = used & set(df.columns)
    df = _pandas_normalize_types(df, cols_usadas, col_types)
    t = _tick("normalizar tipos (pandas)", t)
    if not val_specs:
        print(f"[TB-BUILD] sin valores → df: {df.shape}")
        print(f"[TB-BUILD] TOTAL: {_time.time() - t_start:.2f}s")
        return df

    pivot_vals = [value for value in val_specs if value.get("pivot", True)]
    flat_vals = [value for value in val_specs if not value.get("pivot", True)]
    if not pivot_vals:
        agg_map = {value["col"]: value["agg"] for value in flat_vals}
        flat_groups = list(dict.fromkeys([*rows, *cols]))
        if not flat_groups:
            result = (df.agg(agg_map).to_frame("Valor").reset_index()
                      .rename(columns={"index": "Métrica"}))
        else:
            result = (df.groupby(flat_groups, dropna=False, observed=True)
                      .agg(agg_map).reset_index())
        _tick("agg sin pivote (pandas)", t)
        print(f"[TB-BUILD] TOTAL: {_time.time() - t_start:.2f}s")
        return result

    agg_pivot_map = {value["col"]: value["agg"] for value in pivot_vals}
    value_cols = [value["col"] for value in pivot_vals]
    if rows and cols:
        result = _pandas_pivot(df, rows, cols, value_cols, agg_pivot_map)
        t = _tick("pivot full (pandas)", t)
    elif not rows and cols:
        result = _pandas_pivot_norows(df, cols, value_cols, agg_pivot_map)
        t = _tick("pivot norows (pandas)", t)
    elif rows:
        result = (df.groupby(rows, dropna=False, observed=True)
                  .agg(agg_pivot_map).reset_index())
        t = _tick("groupby solo filas (pandas)", t)
    else:
        result = (df.agg(agg_pivot_map).to_frame("Valor").reset_index()
                  .rename(columns={"index": "Métrica"}))
        t = _tick("total (pandas)", t)

    if flat_vals and rows:
        flat_agg_map = {value["col"]: value["agg"] for value in flat_vals}
        totals = (df.groupby(rows, dropna=False, observed=True)
                  .agg(flat_agg_map).reset_index())
        cols_to_add = [
            col for col in totals.columns if col in rows or col not in result.columns
        ]
        result = pd.merge(result, totals[cols_to_add], on=rows, how="left")
        _tick("merge flat_vals (pandas)", t)

    print(f"[TB-BUILD] result.shape={result.shape}")
    print(f"[TB-BUILD] TOTAL: {_time.time() - t_start:.2f}s")
    return result
