"""Estadísticas descriptivas básicas."""

import pandas as pd

NAME = "Estadísticas descriptivas"
DESCRIPTION = "Resumen numérico, tipos, nulos y memoria"
CATEGORY = "Básicos"


def get_table_format() -> dict:
    """Estructura de tabla requerida por el análisis."""
    return {
        "summary": "Cualquier tabla · todas las columnas",
        "temporal": [],
        "required_dimensions": [],
        "optional_dimensions": [],
        "metrics": [],
        "investment": [],
        "rows": [],
        "columns": [],
        "values": [],
        "requires_pivot": False,
        "notes": "Trabaja sobre el dataframe completo; no requiere estructura.",
    }


def run(df: pd.DataFrame, **kwargs) -> dict:
    return {
        "Dimensiones": f"{df.shape[0]} filas × {df.shape[1]} columnas",
        "Columnas": list(df.columns),
        "Tipos de dato": pd.DataFrame({
            "columna": df.columns,
            "tipo": df.dtypes.astype(str).values,
        }),
        "Valores nulos": pd.DataFrame({
            "columna": df.columns,
            "nulos": df.isnull().sum().values,
            "porcentaje": (df.isnull().mean() * 100).round(2).values,
        }),
        "Resumen numérico": df.describe(include="all").T,
    }