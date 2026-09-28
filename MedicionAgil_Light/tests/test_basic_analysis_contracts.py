import importlib.util
from pathlib import Path

import pandas as pd

from analyses import correlation, descriptive


def _load_corrplot():
    path = Path(__file__).parents[1] / "mmm_app" / "analyses" / \
        "Análisis de correlación.py"
    spec = importlib.util.spec_from_file_location("corrplot_analysis", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dataset():
    return pd.DataFrame({
        "a": [1.0, 2.0, 3.0, 4.0],
        "b": [2.0, 4.0, 6.0, 8.0],
        "label": ["x", "y", "x", "z"],
    })


def test_descriptive_contract_returns_expected_tables():
    result = descriptive.run(_dataset())
    assert result["Dimensiones"] == "4 filas × 3 columnas"
    assert set(result) == {"Dimensiones", "Columnas", "Tipos de dato",
                           "Valores nulos", "Resumen numérico"}
    assert result["Columnas"] == ["a", "b", "label"]
    assert list(result["Valores nulos"]["nulos"]) == [0, 0, 0]


def test_correlation_contract_detects_numeric_pairs_without_ui():
    result = correlation.run(_dataset())
    matrix = result["Matriz de correlación"]
    assert list(matrix.columns) == ["a", "b"]
    assert matrix.loc["a", "b"] == 1.0
    pairs = result["Pares con |r| > 0.7"]
    assert list(pairs.iloc[0]) == ["a", "b", 1.0]


def test_corrplot_contract_returns_two_figures_without_tk():
    corrplot = _load_corrplot()
    result = corrplot.run(_dataset())
    assert {"Matriz de correlación", "Corrplot (círculos)", "Heatmap"} <= set(result)
    assert result["Matriz de correlación"].shape == (2, 2)
    assert result["Corrplot (círculos)"]["plot"].axes
    assert result["Heatmap"]["plot"].axes

