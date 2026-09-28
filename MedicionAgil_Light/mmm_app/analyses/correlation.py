"""Matriz de correlación de Pearson."""

import pandas as pd

NAME = "Matriz de correlación"
DESCRIPTION = "Correlación de Pearson entre variables numéricas"
CATEGORY = "Básicos"


def run(df: pd.DataFrame, **kwargs) -> dict:
    num = df.select_dtypes("number")
    if num.shape[1] < 2:
        return {"Error": "Se necesitan al menos 2 columnas numéricas."}

    corr = num.corr(numeric_only=True).round(3)

    # Pares con correlación alta (|r| > 0.7)
    pairs = []
    cols = corr.columns
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr.iloc[i, j]
            if abs(r) > 0.7:
                pairs.append((cols[i], cols[j], round(r, 3)))
    high_corr = pd.DataFrame(pairs, columns=["Var 1", "Var 2", "r"]) if pairs else "Ninguna"

    return {
        "Matriz de correlación": corr,
        "Pares con |r| > 0.7": high_corr,
    }