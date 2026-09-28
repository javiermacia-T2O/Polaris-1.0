"""Comportamiento del constructor antes de extraer su procesamiento."""

from types import SimpleNamespace

import pandas as pd
import pytest

from app_desktop import MMMApp
from services.table_service import build_table
from services import table_service


def _snapshot(frame, *, rows=(), values=(), filters=None):
    return SimpleNamespace(
        _tb_source_df=lambda: frame,
        tb_rows=list(rows), tb_cols=[], tb_vals=list(values),
        tb_filters=filters or {}, tb_col_types={},
    )


def test_table_full_groupby_keeps_aggregation_and_filter():
    frame = pd.DataFrame({
        "region": ["A", "A", "B", "B"],
        "value": [1, 2, 10, 20],
    })
    state = _snapshot(
        frame, rows=["region"],
        values=[{"col": "value", "agg": "sum", "pivot": False}],
        filters={"region": {"A"}},
    )
    result = MMMApp._tb_build_df(state, preview=False)
    assert result["region"].tolist() == ["A"]
    assert result["value"].tolist() == [3]
    assert frame["value"].tolist() == [1, 2, 10, 20]


def test_table_preview_does_not_materialize_full_input():
    frame = pd.DataFrame({"value": range(100)})
    result = MMMApp._tb_build_df(_snapshot(frame), preview=True)
    assert len(result) == 20
    assert result["value"].iloc[-1] == 19


def test_service_preview_truncates_before_filters():
    frame = pd.DataFrame({
        "region": ["A"] * 20 + ["B"] * 10,
        "value": range(30),
    })

    with pytest.raises(ValueError, match="Todas las columnas"):
        build_table(
            frame, rows=[], cols=[], val_specs=[],
            filters={"region": {"B"}}, col_types={}, preview=True,
        )


def test_service_builds_filtered_groupby_without_tk_state():
    frame = pd.DataFrame({
        "region": ["A", "A", "B"],
        "value": [2, 3, 100],
    })

    result = build_table(
        frame, rows=["region"], cols=[],
        val_specs=[{"col": "value", "agg": "sum", "pivot": False}],
        filters={"region": {"A"}}, col_types={}, preview=False,
    )

    assert result.to_dict("records") == [{"region": "A", "value": 5}]


def test_duckdb_registration_is_released_after_full_build(monkeypatch):
    released = []

    class _ArrowResult:
        def to_pandas(self):
            return pd.DataFrame({"value": [1]})

    monkeypatch.setattr(table_service.db_engine, "_DUCKDB_OK", True)
    monkeypatch.setattr(table_service.db_engine, "UMBRAL_PANDAS", 0)
    monkeypatch.setattr(
        table_service.db_engine, "register",
        lambda frame, name: "_build_src",
    )
    monkeypatch.setattr(
        table_service.db_engine, "query_arrow",
        lambda sql: _ArrowResult(),
    )
    monkeypatch.setattr(
        table_service.db_engine, "unregister",
        lambda name: released.append(name),
    )

    result = build_table(
        pd.DataFrame({"value": [1]}), rows=[], cols=[], val_specs=[],
        filters={}, col_types={}, preview=False,
    )

    assert result.to_dict("records") == [{"value": 1}]
    assert released == ["_build_src"]


def test_duck_full_pipeline_registers_only_columns_needed_for_filter_and_result(
        monkeypatch):
    """El registro DuckDB no debe arrastrar columnas ajenas al cálculo."""
    if not table_service.db_engine._DUCKDB_OK:
        pytest.skip("DuckDB no está disponible")

    frame = pd.DataFrame({
        "region": ["A", "A", "B"],
        "value": [2, 3, 100],
        "unused_text": ["x", "y", "z"],
        "unused_date": ["2024-01-01"] * 3,
    })
    registered_columns = []
    real_register = table_service.db_engine.register

    def capture_register(projected, name):
        registered_columns.append(list(projected.columns))
        return real_register(projected, name)

    monkeypatch.setattr(table_service.db_engine, "register", capture_register)

    result = build_table(
        frame, rows=["region"], cols=[],
        val_specs=[{"col": "value", "agg": "sum", "pivot": False}],
        filters={"region": {"A"}},
        col_types={"unused_date": "fecha"}, preview=False,
    )

    assert registered_columns == [["region", "value", "unused_date"]]
    assert result.to_dict("records") == [{"region": "A", "value": 5}]


def test_duck_table_without_values_keeps_configured_date_column(monkeypatch):
    if not table_service.db_engine._DUCKDB_OK:
        pytest.skip("DuckDB no está disponible")

    frame = pd.DataFrame({
        "region": ["A", "B"],
        "Fecha": ["2026-01-01", "2026-01-02"],
        "unused": [100, 200],
    })
    monkeypatch.setattr(table_service.db_engine, "UMBRAL_PANDAS", 0)

    result = build_table(
        frame, rows=[], cols=[], val_specs=[],
        filters={"region": {"A"}},
        col_types={"Fecha": "fecha"}, preview=False,
    )

    assert result.columns.tolist() == ["region", "Fecha"]
    assert result["region"].tolist() == ["A"]


def test_duck_pivot_with_rows_reads_categories_from_filtered_cte():
    if not table_service.db_engine._DUCKDB_OK:
        pytest.skip("DuckDB no está disponible")

    frame = pd.DataFrame({
        "region": ["A", "A", "B"],
        "category": [None, "y", "hidden"],
        "value": [2, 3, 100],
    })

    result = build_table(
        frame, rows=["region"], cols=["category"],
        val_specs=[{"col": "value", "agg": "sum", "pivot": True}],
        filters={"region": {"A"}},
        col_types={"category": "categorica"}, preview=False,
    )

    assert result.to_dict("records") == [
        {"region": "A", "(vacío)": 2, "y": 3}
    ]
    assert "hidden" not in result.columns


def test_duck_pivot_without_rows_reads_categories_from_filtered_cte():
    if not table_service.db_engine._DUCKDB_OK:
        pytest.skip("DuckDB no está disponible")

    frame = pd.DataFrame({
        "region": ["A", "A", "B"],
        "category": ["x", "y", "hidden"],
        "value": [2, 3, 100],
    })

    result = build_table(
        frame, rows=[], cols=["category"],
        val_specs=[{"col": "value", "agg": "sum", "pivot": True}],
        filters={"region": {"A"}}, col_types={}, preview=False,
    )

    assert result.to_dict("records") == [{"x": 2, "y": 3}]
    assert "hidden" not in result.columns


def test_duck_pivot_rejects_unsafe_width_before_building_result(monkeypatch):
    if not table_service.db_engine._DUCKDB_OK:
        pytest.skip("DuckDB no está disponible")

    frame = pd.DataFrame({"category": ["x", "y"], "value": [1, 2]})
    monkeypatch.setattr(table_service._memory, "pandas_limit_bytes", lambda: 1)

    with pytest.raises(MemoryError, match="Pivot rechazado por memoria"):
        build_table(
            frame, rows=[], cols=["category"],
            val_specs=[{"col": "value", "agg": "sum", "pivot": True}],
            filters={}, col_types={}, preview=False,
        )
