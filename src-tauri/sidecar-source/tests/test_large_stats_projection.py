"""Regression coverage for bounded table projections and large stats fallback."""

import duckdb
import pandas as pd

from models.table_recipe import TableRecipe
from services import table_service
from services.active_dataset import ActiveDataset


def _dataset(tmp_path, frame):
    path = tmp_path / "source.parquet"
    frame.to_parquet(path)
    return ActiveDataset.open_file(path)


def test_rows_without_values_project_only_requested_row(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "Semana": ["2025-W01", "2025-W02"],
        "Fecha": pd.to_datetime(["2025-01-01", "2025-01-08"]),
        "Mes": pd.to_datetime(["2025-01-01", "2025-01-01"]),
    }))
    recipe = TableRecipe.from_parts(
        rows=["Semana"], cols=[], val_specs=[], filters={},
        col_types={"Semana": "texto", "Fecha": "fecha", "Mes": "fecha"},
    )

    preview = table_service.preview_to_pandas(source, recipe)
    full = table_service.build_table_view(source, recipe)

    assert list(preview.columns) == ["Semana"]
    assert list(full.columns) == ["Semana"]
    assert list(full.preview()["Semana"]) == ["2025-W01", "2025-W02"]


def test_stats_falls_back_to_exact_per_column_after_oom(monkeypatch, tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "a": [1, None, 3], "b": [None, "x", "y"]
    }))
    # The service obtains its connection from core.engine; wrap the connection
    # so only the combined aggregation fails, while normal DuckDB queries work.
    from core import engine
    connection = engine.get_conn()
    original_execute = connection.execute
    calls = []

    class ConnectionWrapper:
        def execute(self, sql, *args, **kwargs):
            calls.append(sql)
            if ("COUNT(*) - COUNT" in sql
                    and len(calls) == 1):
                raise duckdb.OutOfMemoryException("simulated stats OOM")
            return original_execute(sql, *args, **kwargs)

    monkeypatch.setattr(engine, "get_conn", lambda: ConnectionWrapper())
    result = source.stats()

    assert result["rows"] == 3
    assert result["nulls"] == {"a": 1, "b": 1}
    assert any("COUNT(*) - COUNT" in sql for sql in calls)
    assert len([sql for sql in calls if "COUNT(" in sql]) >= 3
