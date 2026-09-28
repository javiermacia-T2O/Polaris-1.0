import pandas as pd

from services.active_dataset import ActiveDataset
from services.table_service import build_table_lazy, build_table_preview_pandas


def test_lazy_table_aggregates_all_rows_and_obeys_active_filters(tmp_path):
    source = tmp_path / "source.parquet"
    frame = pd.DataFrame({"group": ["a"] * 300 + ["b"],
                          "channel": ["x"] * 300 + ["y"],
                          "value": [1] * 300 + [5]})
    frame.to_parquet(source)
    active = ActiveDataset.open_file(source)
    result = build_table_lazy(
        active, rows=["group"], cols=[],
        val_specs=[{"col": "value", "agg": "sum", "pivot": False}])
    assert dict(zip(result["group"], result["value"])) == {"a": 300, "b": 5}
    filtered = active.with_filters({"group": {"b"}})
    result = build_table_lazy(
        filtered, rows=["group"], cols=[],
        val_specs=[{"col": "value", "agg": "sum", "pivot": False}])
    assert result["value"].tolist() == [5]


def test_lazy_table_pivot_and_nonpivot_differ(tmp_path):
    source = tmp_path / "source.parquet"
    pd.DataFrame({"region": ["a", "a", "b"],
                  "channel": ["x", "y", "x"],
                  "value": [1, 2, 3]}).to_parquet(source)
    active = ActiveDataset.open_file(source)
    specs = [{"col": "value", "agg": "sum", "pivot": True}]
    pivoted = build_table_lazy(active, ["region"], ["channel"], specs)
    assert set(pivoted.columns) == {"region", "x", "y"}
    flat = build_table_lazy(active, ["region"], ["channel"],
                            [{**specs[0], "pivot": False}])
    assert set(flat.columns) == {"region", "channel", "value"}


def test_lazy_table_partial_preview_projects_source(tmp_path):
    source = tmp_path / "source.parquet"
    pd.DataFrame({"region": ["a"] * 30, "value": range(30)}).to_parquet(source)
    active = ActiveDataset.open_file(source)
    preview = build_table_lazy(active, rows=["region"], cols=[],
                               val_specs=[], preview=True)
    assert preview.shape == (20, 1)


def test_pandas_preview_filters_before_limit_and_aggregates_full_input():
    frame = pd.DataFrame({"group": ["a"] * 300 + ["b"],
                          "value": [1] * 300 + [5]})
    partial = build_table_preview_pandas(
        frame, rows=["group"], cols=[], val_specs=[],
        filters={"group": {"b"}})
    assert partial["group"].tolist() == ["b"]
    summary = build_table_preview_pandas(
        frame, rows=["group"], cols=[],
        val_specs=[{"col": "value", "agg": "sum", "pivot": False}])
    assert dict(zip(summary["group"], summary["value"])) == {"a": 300, "b": 5}
