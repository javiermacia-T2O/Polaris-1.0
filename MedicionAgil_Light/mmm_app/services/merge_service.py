"""Operaciones puras para combinar datasets con Pandas.

El módulo mantiene los límites de memoria y de cardinalidad antes de llamar a
``pandas.concat`` o ``pandas.merge``. No depende de la interfaz de Tk.
"""

import pandas as pd
import hashlib

from core import memory_budget
from core import engine
from services.active_dataset import ActiveDataset


def _ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def _active_select(dataset: ActiveDataset) -> tuple[str, list]:
    columns = dataset.projection or dataset.columns
    where, params = dataset._where()
    return (f"SELECT {', '.join(_ident(column) for column in columns)} "
            f"FROM {dataset._source_sql()}{where}", dataset._params(params))


def concat_active(datasets: list[ActiveDataset], mode: str) -> ActiveDataset:
    """Build a lazy UNION without materialising file-backed inputs."""
    if len(datasets) < 2:
        raise ValueError("Selecciona al menos dos datasets")
    if mode not in {"all", "common"}:
        raise ValueError("Modo de concatenación no soportado")
    statements, params = [], []
    common = list(datasets[0].projection or datasets[0].columns)
    if mode == "common":
        for dataset in datasets[1:]:
            available = set(dataset.projection or dataset.columns)
            common = [column for column in common if column in available]
        if not common:
            raise ValueError("No hay columnas comunes.")
    for dataset in datasets:
        sql, values = _active_select(
            dataset.with_columns(common) if mode == "common" else dataset)
        statements.append(f"({sql})")
        params.extend(values)
    operator = " UNION ALL " if mode == "common" else " UNION ALL BY NAME "
    sql = operator.join(statements)
    version = hashlib.sha256(repr(("concat", mode,
        tuple(dataset.version_token() for dataset in datasets))).encode()).hexdigest()
    return ActiveDataset.from_query(sql, tuple(params), None, version,
                                    dependencies=tuple(datasets))


def join_active(datasets: list[ActiveDataset], keys: list[str], how: str,
                cancel=None) -> ActiveDataset:
    """Build a null-safe SQL join and preflight every expansion exactly."""
    if not keys:
        raise ValueError("Selecciona al menos una columna clave.")
    if how not in {"inner", "outer"}:
        raise ValueError("Modalidad de unión no soportada")
    for dataset in datasets:
        if any(key not in (dataset.projection or dataset.columns) for key in keys):
            raise KeyError("Una clave no existe en todos los datasets")
    current = datasets[0]
    dependencies = [datasets[0]]
    for index, right in enumerate(datasets[1:], start=1):
        left_sql, left_params = _active_select(current)
        right_sql, right_params = _active_select(right)
        conditions = " AND ".join(
            f"l.{_ident(key)} IS NOT DISTINCT FROM r.{_ident(key)}"
            for key in keys)
        join_kind = "INNER" if how == "inner" else "FULL OUTER"
        left_columns = list(current.projection or current.columns)
        right_columns = list(right.projection or right.columns)
        select = []
        for column in left_columns:
            if how == "outer" and column in keys:
                select.append(f"COALESCE(l.{_ident(column)}, r.{_ident(column)}) AS {_ident(column)}")
            else:
                select.append(f"l.{_ident(column)}")
        occupied = set(left_columns)
        for column in right_columns:
            if column in keys:
                continue
            label = column if column not in occupied else f"{column}_{index}"
            while label in occupied:
                label += f"_{index}"
            occupied.add(label)
            select.append(f"r.{_ident(column)} AS {_ident(label)}")
        sql = (f"SELECT {', '.join(select)} FROM ({left_sql}) l {join_kind} JOIN "
               f"({right_sql}) r ON {conditions}")
        params = tuple([*left_params, *right_params])
        if cancel is not None and cancel.is_set():
            from core.tasks import TaskCancelled
            raise TaskCancelled()
        # Exact COUNT catches many-to-many explosions before publishing a
        # potentially enormous result. Disk/RAM admission remains centralized.
        engine.get_conn(cancel=cancel).execute(
            f"SELECT COUNT(*) FROM ({sql}) _join_preflight", params).fetchone()
        dependencies.append(right)
        token = hashlib.sha256(repr(("join", how, tuple(keys), index,
            tuple(item.version_token() for item in dependencies))).encode()).hexdigest()
        current = ActiveDataset.from_query(sql, params, None, token,
                                           dependencies=tuple(dependencies))
    return current


def concat_datasets(datasets, sel, mode):
    """Concatena los datasets seleccionados respetando el presupuesto Pandas."""
    used = sum(int(datasets[name].memory_usage(deep=True).sum())
               for name in sel)
    if used * 2 > memory_budget.pandas_limit_bytes():
        raise MemoryError("La concatenación excedería el presupuesto de RAM")

    if mode == "common":
        common_columns = list(datasets[sel[0]].columns)
        for name in sel[1:]:
            common_columns = [column for column in common_columns
                              if column in datasets[name].columns]
        if not common_columns:
            raise ValueError("No hay columnas comunes.")

        frames = [datasets[name][common_columns] for name in sel]
        return pd.concat(frames, ignore_index=True)

    frames = [datasets[name] for name in sel]
    return pd.concat(frames, ignore_index=True, sort=False)


def merge_datasets(datasets, sel, keys, how):
    """Une datasets en cadena tras validar tamaño y cardinalidad de cada paso."""
    if not keys:
        raise ValueError("Selecciona al menos una columna clave.")

    frames = [datasets[name] for name in sel]
    limit = memory_budget.safe_merge_row_limit(frames)
    if limit < 1:
        raise MemoryError("No queda presupuesto de RAM para la unión")

    counts = [frame.groupby(keys, dropna=False, observed=True).size().rename(name)
              for name, frame in zip(sel, frames)]
    for end in range(2, len(sel) + 1):
        cardinalities = pd.concat(
            counts[:end], axis=1,
            join="inner" if how == "inner" else "outer",
        ).fillna(0)
        estimated = 0
        for row in cardinalities.itertuples(index=False, name=None):
            per_key = 1
            for count in row:
                if how == "inner" or count:
                    per_key *= int(count)
            estimated += per_key
            if estimated > limit:
                raise ValueError(
                    f"La unión de {end} datasets superaría "
                    f"{limit:,} filas. Reduce claves duplicadas o filtra "
                    "los datos antes de unir.".replace(",", "."))

    result = frames[0]
    for index, right in enumerate(frames[1:], start=1):
        result = pd.merge(
            result, right, on=keys, how=how,
            suffixes=("", f"_{index}"),
        )
    return result
