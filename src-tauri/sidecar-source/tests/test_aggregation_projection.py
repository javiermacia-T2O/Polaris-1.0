"""La agregación grande registra solo columnas útiles sin copiar las medidas."""

import numpy as np
import pandas as pd
import pytest

from core import engine, loader


@pytest.mark.parametrize("date_values", [
    pd.to_datetime(["2026-01-01", "2026-01-15", "2026-02-01"]),
    ["2026-01-01", "2026-01-15", "2026-02-01"],
])
def test_duck_aggregation_projects_without_copying_measures(monkeypatch, date_values):
    source = pd.DataFrame({
        "Fecha": date_values,
        "Mes": ["enero", "enero", "febrero"],
        "Canal": ["Web", "Web", "Web"],
        "Ingresos": [1.0, 2.0, 3.0],
    })
    before = source.copy(deep=True)
    registered = []
    real_register = engine.register

    def capture(frame, name):
        registered.append(frame)
        return real_register(frame, name)

    monkeypatch.setattr(engine, "register", capture)
    result = loader._aggregate_period_duck(source, "Fecha", "M")

    assert len(registered) == 1
    projected = registered[0]
    assert list(projected.columns) == ["Fecha", "Canal", "Ingresos"]
    assert np.shares_memory(source["Ingresos"].to_numpy(),
                            projected["Ingresos"].to_numpy())
    pd.testing.assert_frame_equal(source, before)
    assert result["Ingresos"].tolist() == [3.0, 3.0]


@pytest.mark.parametrize(("frequency", "sums"), [
    ("M", [1, 2, 0, 3]),
    ("Q", [3, 3]),
    ("Y", [6]),
])
def test_pandas_fallback_accepts_calendar_frequencies(monkeypatch, frequency, sums):
    monkeypatch.setattr(engine, "_DUCKDB_OK", False)
    source = pd.DataFrame({
        "Fecha": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-04-01"]),
        "Ingresos": [1, 2, 3],
    })

    result = loader.aggregate_period(source, "Fecha", frequency)

    assert result["Ingresos"].tolist() == sums
    assert source["Ingresos"].tolist() == [1, 2, 3]
