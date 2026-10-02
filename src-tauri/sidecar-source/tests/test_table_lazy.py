import pandas as pd

from medicion_core import MedicionApplication
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


def test_pivot_preview_includes_values_outside_first_100_rows(tmp_path):
    from models.table_recipe import TableRecipe
    from services.table_service import preview_to_pandas

    source = tmp_path / "source.parquet"
    pd.DataFrame({"region": ["r"] * 101,
                  "channel": ["web"] * 100 + ["partner"],
                  "value": [1] * 101}).to_parquet(source)
    active = ActiveDataset.open_file(source)
    recipe = TableRecipe.from_parts(
        rows=["region"], cols=["channel"],
        val_specs=[{"col": "value", "agg": "sum", "pivot": True}])

    preview = preview_to_pandas(active, recipe)

    assert set(preview.columns) == {"region", "partner", "web"}
    assert len(preview) == 1
    assert preview.loc[0, "partner"] == 1
    assert preview.loc[0, "web"] == 100


def test_lazy_table_partial_preview_projects_source(tmp_path):
    source = tmp_path / "source.parquet"
    pd.DataFrame({"region": ["a"] * 30, "value": range(30)}).to_parquet(source)
    active = ActiveDataset.open_file(source)
    preview = build_table_lazy(active, rows=["region"], cols=[],
                               val_specs=[], preview=True)
    assert preview.shape == (30, 1)


def test_pandas_preview_is_sampled_but_apply_aggregates_full_input():
    from medicion_core.application import _DatasetEntry
    from services.active_dataset import ActiveDataset

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
    assert summary.attrs["approximate"] is False

    app = MedicionApplication(max_workers=1)
    dataset = ActiveDataset.from_frame(frame)
    app._datasets["dataset-full"] = _DatasetEntry(
        "dataset-full", "fixture", dataset, dataset, None)
    try:
        table_id = app.build_table("dataset-full", {
            "rows": ["group"], "columns": [],
            "values": [{"col": "value", "agg": "sum", "pivot": False}],
            "filters": {},
        })
        full_result = app._datasets[table_id].active.source
        assert dict(zip(full_result["group"], full_result["value"])) == {
            "a": 300, "b": 5,
        }
    finally:
        app.shutdown()


def test_mixed_pivot_predictors_and_nonpivot_target_use_complete_data(tmp_path):
    from models.table_recipe import TableRecipe
    from services.table_service import build_table_view, preview_to_pandas

    # Channel B appears beyond the former 100-row preview cutoff.
    frame = pd.DataFrame({
        "Fecha": ["2025-01-01"] * 101 + ["2025-01-02"] * 2,
        "Canal": ["A"] * 100 + ["B", "A", "B"],
        "Valor": [1] * 100 + [7, 3, 4],
        "Objetivo": [0] * 100 + [20, 30, 40],
    })
    source = tmp_path / "mixed.parquet"
    frame.to_parquet(source)
    recipe = TableRecipe.from_parts(
        rows=["Fecha"], cols=["Canal"], val_specs=[
            {"col": "Valor", "agg": "sum", "pivot": True},
            {"col": "Objetivo", "agg": "sum", "pivot": False},
        ])
    active = ActiveDataset.open_file(source)
    expected = pd.DataFrame({"Fecha": ["2025-01-01", "2025-01-02"],
                             "A": [100, 3], "B": [7, 4],
                             "Objetivo": [20, 70]})
    for actual in (preview_to_pandas(active, recipe),
                   build_table_view(active, recipe).preview(limit=100)):
        pd.testing.assert_frame_equal(actual.loc[:, expected.columns].reset_index(drop=True),
                                      expected, check_dtype=False)

    app = MedicionApplication(max_workers=1)
    try:
        pandas_result = app._build_pandas_table(frame, recipe)
        assert list(pandas_result.columns) == list(expected.columns)
        pd.testing.assert_frame_equal(pandas_result, expected, check_dtype=False)
    finally:
        app.shutdown()


def test_filter_values_include_null_and_blank_in_both_backends(tmp_path):
    frame = pd.DataFrame({"Canal": [None, "", "  ", "A"],
                          "Valor": [1, 2, 3, 4]})
    source = tmp_path / "nulls.parquet"
    frame.to_parquet(source)
    for active in (ActiveDataset.from_frame(frame), ActiveDataset.open_file(source)):
        values = active.distinct_values("Canal")
        assert values is not None and None in values and "" in values
        nulls = active.with_filters({"Canal": {None}})
        blanks = active.with_filters({"Canal": {""}})
        assert nulls.row_count() == 1
        assert blanks.row_count() == 2


def test_regression_models_the_pivoted_complete_table(tmp_path):
    from analyses.regression import run

    records = []
    for index, date in enumerate(pd.date_range("2025-01-01", periods=40)):
        first = 20 + index + index % 3
        second = 10 + (index * 7) % 13
        target = 15 + 2 * first + 3 * second
        records.extend([
            {"Fecha": date, "Canal": "A", "Valor": first, "Objetivo": target},
            {"Fecha": date, "Canal": "B", "Valor": second, "Objetivo": 0},
        ])
    source = tmp_path / "regression-mixed.parquet"
    pd.DataFrame(records).to_parquet(source)
    table = build_table_lazy(ActiveDataset.open_file(source), ["Fecha"],
                             ["Canal"], [
                                 {"col": "Valor", "agg": "sum", "pivot": True},
                                 {"col": "Objetivo", "agg": "sum", "pivot": False},
                             ])
    assert (table[["A", "B"]] > 0).all().all()
    outcome = run(table, regression_type="OLS (mínimos cuadrados, con p-values)",
                  date_col="Fecha", target_col="Objetivo", input_cols=["A", "B"],
                  progress_callback=lambda *_: None)
    assert outcome["Estado"] == "OK"
    assert outcome["RMSE"] < 1e-6


def test_487_dates_and_18_channels_keep_every_pivot_value(tmp_path):
    """A full wide table must not retain zeros from a source-row preview."""
    from models.table_recipe import TableRecipe
    from services.table_service import build_table_view, preview_to_pandas

    days = pd.date_range("2025-01-01", periods=487)
    channels = [f"Canal {index:02d}" for index in range(18)]
    records = []
    # Channel-major order makes every first-page source row the same channel.
    for channel_index, channel in enumerate(channels):
        for day_index, day in enumerate(days):
            records.append({
                "Fecha": day, "Canal": channel,
                "Valor": float(channel_index + day_index + 1),
                "Objetivo": float(day_index + 100) if channel_index == 0 else 0.0,
            })
    source = tmp_path / "wide-487.parquet"
    frame = pd.DataFrame(records)
    frame.to_parquet(source)
    recipe = TableRecipe.from_parts(
        rows=["Fecha"], cols=["Canal"], val_specs=[
            {"col": "Valor", "agg": "sum", "pivot": True},
            {"col": "Objetivo", "agg": "sum", "pivot": False},
        ])
    active = ActiveDataset.open_file(source)
    preview = preview_to_pandas(active, recipe)
    full = build_table_view(active, recipe).analysis_frame()
    resident = MedicionApplication(max_workers=1)
    try:
        pandas_full = resident._build_pandas_table(frame, recipe)
    finally:
        resident.shutdown()
    for table in (preview, full, pandas_full):
        assert set(channels).issubset(table.columns)
        assert (table[channels].to_numpy() > 0).all()
        assert table["Canal 17"].iloc[0] == 18
        assert table["Objetivo"].iloc[0] == 100
    assert len(full) == len(days)
