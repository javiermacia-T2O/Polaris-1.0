"""Operaciones puras para combinar datasets con Pandas.

El módulo mantiene los límites de memoria y de cardinalidad antes de llamar a
``pandas.concat`` o ``pandas.merge``. No depende de la interfaz de Tk.
"""

import pandas as pd

from core import memory_budget


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
