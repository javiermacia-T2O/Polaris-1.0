from types import SimpleNamespace
import logging

import pandas as pd
import pytest

from app_desktop import MMMApp
from core import memory_budget
from models.table_recipe import TableRecipe
from services.active_dataset import ActiveDataset
from services import table_service


def _dataset(tmp_path, frame):
    path = tmp_path / "source.parquet"
    frame.to_parquet(path)
    return ActiveDataset.open_file(path)


def test_adding_value_keeps_default_table_unpivoted(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "Semana": ["S1", "S1"], "Pais": ["UK", "DE"],
        "Ingreso": [2, 3]}))
    app = SimpleNamespace(
        tb_rows=["Semana"], tb_cols=["Pais"], tb_vals=[],
        tb_pivot_enabled=False,
        _tb_default_aggregation=lambda name: "sum",
        _tb_flash=lambda *args, **kwargs: None,
        _tb_refresh_single_card=lambda name: None,
        _tb_render_chips_quick=lambda: None,
        _tb_update_preview=lambda: None,
    )

    MMMApp._tb_set_role(app, "Ingreso", "val")
    recipe = TableRecipe.from_parts(app.tb_rows, app.tb_cols, app.tb_vals,
                                    pivot=app.tb_pivot_enabled)
    result = table_service.preview_to_pandas(source, recipe)

    assert app.tb_pivot_enabled is False
    assert set(result.columns) == {"Semana", "Pais", "Ingreso"}
    assert dict(zip(result["Pais"], result["Ingreso"])) == {"UK": 2,
                                                              "DE": 3}


def test_recipe_fingerprint_is_stable_and_tracks_dataset_version(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({"group": ["a", "b"],
                                               "value": [1, 2]}))
    first = TableRecipe.from_parts(["group"], [], [{"col": "value", "agg": "sum"}],
                                   {"group": {"b", "a"}})
    same = TableRecipe.from_parts(["group"], [], [{"col": "value", "agg": "sum"}],
                                  {"group": {"a", "b"}})
    assert first == same
    assert first.fingerprint(source, "preview") == same.fingerprint(source, "preview")
    assert first.fingerprint(source, "preview") != first.fingerprint(
        source.with_filters({"group": {"b"}}), "preview")
    assert first.fingerprint(source, "preview") != first.fingerprint(source, "full")
    assert not any(isinstance(value, pd.DataFrame)
                   for value in vars(first).values())


def test_recipe_keeps_per_value_pivots_and_legacy_default_in_fingerprint(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({"group": ["a"],
                                               "value": [1], "other": [2]}))
    mixed = TableRecipe.from_parts(
        ["group"], ["channel"],
        [{"col": "value", "agg": "sum", "pivot": True},
         {"col": "other", "agg": "sum", "pivot": False}], pivot=False)
    flat = TableRecipe.from_parts(
        ["group"], ["channel"],
        [{"col": "value", "agg": "sum", "pivot": False},
         {"col": "other", "agg": "sum", "pivot": False}], pivot=False)
    legacy = TableRecipe(values=(("value", "sum"),), pivot=False)

    assert mixed.values == (("value", "sum"), ("other", "sum"))
    assert mixed.value_pivots == (True, False)
    assert legacy.pivots_for_values() == (False,)
    assert mixed.fingerprint(source, "preview") != flat.fingerprint(
        source, "preview")


def test_recipe_defaults_a_missing_metric_aggregation_to_count(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({"group": ["a"],
                                               "label": ["x"]}))
    recipe = TableRecipe.from_parts(
        ["group"], [], [{"col": "label"}])

    assert recipe.values == (("label", "count"),)
    assert table_service.preview_to_pandas(source, recipe)["label"].tolist() == [1]


def test_mixed_pivot_keeps_totals_per_row_and_flat_metrics_unpivoted(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "region": ["a", "a", "b"], "channel": ["x", "y", "x"],
        "sales": [1, 2, 3], "spend": [10, 20, 30]}))
    recipe = TableRecipe.from_parts(
        ["region"], ["channel"],
        [{"col": "sales", "agg": "sum", "pivot": True},
         {"col": "spend", "agg": "sum", "pivot": False}], pivot=False)

    preview = table_service.preview_to_pandas(source, recipe)
    full = table_service.build_table_view(source, recipe)
    result = full.preview()

    assert tuple(preview.columns) == tuple(full.columns) == (
        "region", "x", "y", "spend")
    assert result.loc[result["region"] == "a", ["x", "y", "spend"]].iloc[0].tolist() == [1, 2, 30]
    b = result.loc[result["region"] == "b", ["x", "y", "spend"]].iloc[0]
    assert b["x"] == 3 and b["y"] == 0 and b["spend"] == 30


def test_pivot_preserves_null_for_non_additive_missing_cells(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "region": ["a", "a", "b"], "channel": ["x", "y", "x"],
        "sales": [1, 2, 3]}))
    recipe = TableRecipe.from_parts(
        ["region"], ["channel"], [{"col": "sales", "agg": "mean", "pivot": True}])

    preview = table_service.preview_to_pandas(source, recipe)
    full = table_service.build_table_view(source, recipe).preview()

    assert pd.isna(preview.loc[preview["region"] == "b", "y"].iloc[0])
    assert pd.isna(full.loc[full["region"] == "b", "y"].iloc[0])


def test_legacy_builder_matches_lazy_pivot_missing_value_policy(tmp_path):
    frame = pd.DataFrame({
        "region": ["a", "a", "b"], "channel": ["x", "y", "x"],
        "sales": [1, 2, 3]})
    source = _dataset(tmp_path, frame)
    recipe = TableRecipe.from_parts(
        ["region"], ["channel"], [{"col": "sales", "agg": "sum", "pivot": True}])

    lazy = table_service.build_table_view(source, recipe).preview()
    legacy = table_service.build_table(
        frame, ["region"], ["channel"],
        [{"col": "sales", "agg": "sum", "pivot": True}], {}, {},
    )

    assert lazy.loc[lazy["region"] == "b", "y"].iloc[0] == 0
    assert legacy.loc[legacy["region"] == "b", "y"].iloc[0] == 0


def test_multiple_pivoted_metrics_use_metric_category_headers(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "region": ["a", "a"], "channel": ["x", "y"],
        "sales": [1, 2], "orders": [3, 4]}))
    recipe = TableRecipe.from_parts(
        ["region"], ["channel"],
        [{"col": "sales", "agg": "sum", "pivot": True},
         {"col": "orders", "agg": "sum", "pivot": True}])

    result = table_service.preview_to_pandas(source, recipe)

    assert tuple(result.columns) == (
        "region", "sales · x", "sales · y", "orders · x", "orders · y")


def test_no_metric_column_role_renders_blank_category_columns_lazily(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "Fecha": ["2025-01-01", "2025-01-01", "2025-01-02"],
        "Estrategia": ["MIX", "DGEN", "MIX"], "unused": [1, 2, 3]}))
    recipe = TableRecipe.from_parts(["Fecha"], ["Estrategia"], [])

    preview = table_service.preview_to_pandas(source, recipe)
    full = table_service.build_table_view(source, recipe)

    assert full.backend == "query"
    assert tuple(preview.columns) == ("Fecha", "DGEN", "MIX")
    assert preview["Fecha"].tolist() == ["2025-01-01", "2025-01-02"]
    assert preview[["DGEN", "MIX"]].isna().all().all()


def test_blank_column_preview_is_bounded_and_marks_sample_as_approximate(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "group": range(5_000),
        "channel": ["early"] * 4_999 + ["late"],
    }))
    recipe = TableRecipe.from_parts(["group"], ["channel"], [])

    preview = table_service.preview_to_pandas(source, recipe)
    full = table_service.build_table_view(source, recipe).preview()

    assert preview.attrs["approximate"] is True
    assert tuple(preview.columns) == ("group", "early")
    assert preview["group"].tolist() == list(range(20))
    assert tuple(full.columns) == ("group", "early", "late")


def test_preview_filters_before_limit_and_full_stays_lazy(tmp_path):
    frame = pd.DataFrame({"group": ["a"] * 5_000 + ["b"],
                          "value": [1] * 5_000 + [7]})
    source = _dataset(tmp_path, frame)
    local = TableRecipe.from_parts(["group"], [], [], {"group": {"b"}})
    preview = table_service.preview_to_pandas(source, local)
    assert preview["group"].tolist() == ["b"]
    assert len(preview) <= 20
    assert preview.attrs["approximate"] is False

    global_recipe = TableRecipe.from_parts(
        ["group"], [], [{"col": "value", "agg": "sum"}])
    partial = table_service.preview_to_pandas(source, global_recipe)
    full = table_service.build_table_view(source, global_recipe)
    assert full.backend == "query"
    assert full.parent is source
    assert partial.attrs["approximate"] is True
    assert len(partial) <= 20
    assert full.stats()["rows"] == 2
    full_preview = full.preview()
    assert dict(zip(full_preview["group"], full_preview["value"])) == {
        "a": 5_000, "b": 7}
    assert dict(zip(partial["group"], partial["value"]))["a"] == 4_096


def test_preview_and_full_keep_separate_category_caches(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "region": ["a", "a", "b"], "channel": ["x", "y", "x"],
        "value": [1, 2, 3], "other": [4, 5, 6]}))
    table_service._PIVOT_CACHE.clear()
    columns = []
    for enabled in [True, False, True, False]:
        recipe = TableRecipe.from_parts(
            ["region"], ["channel"],
            [{"col": "value", "agg": "sum"}], pivot=enabled)
        preview = table_service.preview_to_pandas(source, recipe)
        full = table_service.build_table_view(source, recipe)
        assert tuple(preview.columns) == tuple(full.columns)
        columns.append(tuple(full.columns))
    assert columns[0] == columns[2]
    assert columns[1] == columns[3]
    assert "channel" not in columns[0]
    assert "channel" in columns[1]
    # A preview may only know categories present in its bounded sample, so it
    # must never satisfy the exact full-table category lookup from cache.
    assert len(table_service._PIVOT_CACHE) == 2
    changed_metric = TableRecipe.from_parts(
        ["region"], ["channel"],
        [{"col": "other", "agg": "sum"}], pivot=True)
    table_service.preview_to_pandas(source, changed_metric)
    assert len(table_service._PIVOT_CACHE) == 2
    filtered_recipe = TableRecipe.from_parts(
        ["region"], ["channel"],
        [{"col": "value", "agg": "sum"}],
        {"region": {"a"}}, pivot=True)
    table_service.preview_to_pandas(source, filtered_recipe)
    assert len(table_service._PIVOT_CACHE) == 3


def test_small_preview_and_stats_cache_invalidate_on_filter(tmp_path, monkeypatch):
    source = _dataset(tmp_path, pd.DataFrame({
        "group": ["a", "b"], "value": [1, None]}))
    recipe = TableRecipe.from_parts(["group"], [], [])
    table_service._PREVIEW_CACHE.clear()
    first = table_service.preview_to_pandas(source, recipe)
    assert len(table_service._PREVIEW_CACHE) == 1
    monkeypatch.setattr(table_service, "compile_table",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            AssertionError("cache miss")))
    again = table_service.preview_to_pandas(source, recipe)
    assert first.equals(again)
    again.loc[0, "group"] = "changed"
    assert table_service.preview_to_pandas(source, recipe).loc[0, "group"] == "a"
    with pytest.raises(AssertionError, match="cache miss"):
        table_service.preview_to_pandas(
            source.with_filters({"group": {"b"}}), recipe)

    stats = source.stats()
    assert stats["rows"] == 2 and stats["nulls"]["value"] == 1
    assert source.with_filters({"group": {"b"}}).stats()["rows"] == 1


def test_cached_preview_rejects_changed_source_file(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({"value": [1, 2]}))
    recipe = TableRecipe.from_parts([], [], [])
    table_service.preview_to_pandas(source, recipe)
    pd.DataFrame({"value": [9, 8, 7]}).to_parquet(source.source)
    with pytest.raises(RuntimeError, match="cambió"):
        table_service.preview_to_pandas(source, recipe)


def test_unsafe_full_materialization_rejected_before_fetch(tmp_path, monkeypatch):
    source = _dataset(tmp_path, pd.DataFrame({
        "group": ["a"] * 100, "value": range(100)}))
    view = table_service.build_table_view(
        source, TableRecipe.from_parts(["group"], [], []))
    monkeypatch.setattr(memory_budget, "pandas_limit_bytes", lambda: 1)
    with pytest.raises(MemoryError, match="presupuesto"):
        table_service.materialize_if_safe(view)
    assert view.stats()["rows"] == 100


def test_exact_stats_cache_includes_date_and_rejects_changed_source(
        tmp_path, monkeypatch):
    source = _dataset(tmp_path, pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01", "2025-02-01", "2025-03-01"]),
        "value": [1, None, 3]}))
    first = source.stats(date_column="date")
    assert first["rows"] == 3
    assert first["nulls"]["value"] == 1
    assert first["date_range"] == (
        pd.Timestamp("2025-01-01").date(),
        pd.Timestamp("2025-03-01").date())
    from services import active_dataset
    monkeypatch.setattr(active_dataset.engine, "get_conn",
                        lambda: (_ for _ in ()).throw(AssertionError("query")))
    assert source.stats(date_column="date")["rows"] == 3
    with pytest.raises(AssertionError, match="query"):
        source.with_filters({"value": {1}}).stats(date_column="date")


def test_lazy_full_view_streams_export_without_pandas_full(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "group": ["a", "a", "b"], "value": [2, 3, 5]}))
    view = table_service.build_table_view(
        source, TableRecipe.from_parts(
            ["group"], [], [{"col": "value", "agg": "sum"}]))
    target = tmp_path / "result.parquet"
    view.export(target, "parquet")
    assert dict(zip(pd.read_parquet(target)["group"],
                    pd.read_parquet(target)["value"])) == {"a": 5, "b": 5}


def test_table_recipe_type_conversion_is_lossless_and_ignored_columns_drop(
        tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "group": ["a", "b"], "amount": ["1", "bad"],
        "unused": [9, 8]}))
    projection = TableRecipe.from_parts(
        [], [], [], col_types={"unused": "ignorar"})
    assert "unused" not in table_service.build_table_view(
        source, projection).columns
    numeric = TableRecipe.from_parts(
        ["group"], [], [{"col": "amount", "agg": "sum"}],
        col_types={"amount": "numero"})
    with pytest.raises(Exception, match="bad"):
        table_service.preview_to_pandas(source, numeric)


def test_text_metric_defaults_to_count_when_aggregation_is_missing(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "group": ["a", "a", "b"], "label": ["x", "y", "z"]}))
    recipe = TableRecipe.from_parts(
        ["group"], [], [{"col": "label", "agg": ""}])
    result = table_service.preview_to_pandas(source, recipe)
    assert dict(zip(result["group"], result["label"])) == {"a": 2, "b": 1}


def test_numeric_aggregation_on_text_metric_has_clear_error(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "group": ["a"], "label": ["x"]}))
    recipe = TableRecipe.from_parts(
        ["group"], [], [{"col": "label", "agg": "sum"}])
    with pytest.raises(ValueError, match="requiere una métrica numérica"):
        table_service.preview_to_pandas(source, recipe)


def test_recipe_order_applies_to_preview_and_full(tmp_path):
    source = _dataset(tmp_path, pd.DataFrame({
        "group": ["b", "a", "c"], "value": [2, 1, 3]}))
    recipe = TableRecipe.from_parts(
        [], [], [], selected=["group", "value"],
        order=[("group", False)])
    assert table_service.preview_to_pandas(source, recipe)["group"].tolist() == [
        "c", "b", "a"]
    assert table_service.build_table_view(source, recipe).preview()[
        "group"].tolist() == ["c", "b", "a"]


def test_debounce_keeps_one_timer_and_stale_result_is_discarded(tmp_path):
    frame = pd.DataFrame({"value": [1, 2]})
    active = ActiveDataset.from_frame(frame)
    scheduled, cancelled, submitted, rendered = [], [], [], []
    state = SimpleNamespace(
        _tb_preview_revision=0, _tb_preview_after_id=None,
        _tb_builder_dataset=None, session=SimpleNamespace(active_dataset=active),
        tb_preview_tree=SimpleNamespace(get_children=lambda: [], delete=lambda *_: None),
        tb_lbl_preview_info=SimpleNamespace(config=lambda **_k: None),
        tasks=SimpleNamespace(busy=False, closing=False),
        _tb_source_df=lambda: frame,
        _tb_recipe=lambda: TableRecipe.from_parts([], [], []),
        _tb_update_preview_now=lambda: None,
        _tb_render_preview_result=lambda *args: rendered.append(args),
        _start_task=lambda work, done, label: submitted.append((work, done)),
        after=lambda delay, callback: scheduled.append((delay, callback)) or len(scheduled),
        after_cancel=cancelled.append,
    )
    MMMApp._tb_update_preview(state)
    MMMApp._tb_update_preview(state)
    assert [delay for delay, _ in scheduled] == [150, 150]
    assert cancelled == [1]
    MMMApp._tb_update_preview_now(state)
    assert len(submitted) == 1
    state._tb_preview_revision += 1
    submitted[0][1](pd.DataFrame({"value": [1]}))
    assert rendered == []


def test_busy_preview_defers_without_submitting_another_worker():
    frame = pd.DataFrame({"value": [1]})
    timers, submitted = [], []
    state = SimpleNamespace(
        _tb_preview_after_id=None, _tb_preview_revision=7,
        tb_preview_tree=SimpleNamespace(),
        _tb_source_df=lambda: frame,
        tasks=SimpleNamespace(busy=True, closing=False),
        after=lambda delay, callback: timers.append(delay) or 1,
        _tb_update_preview_now=lambda: None,
        _start_task=lambda *_: submitted.append(True),
    )
    MMMApp._tb_update_preview_now(state)
    assert timers == [300]
    assert submitted == []


def test_debug_logs_compact_preview_cache_and_query_fingerprint(
        tmp_path, monkeypatch):
    from core import diagnostics
    logger = logging.getLogger("mmm_app.tasks")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    monkeypatch.setenv("MMM_DEBUG", "1")
    monkeypatch.setattr(diagnostics, "writable_root", lambda: tmp_path)
    source = _dataset(tmp_path, pd.DataFrame({"value": range(4_100)}))
    recipe = TableRecipe.from_parts([], [], [
        {"col": "value", "agg": "sum"}])
    table_service.preview_to_pandas(source, recipe)
    table_service.preview_to_pandas(source, recipe)
    content = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "cache MISS" in content and "cache HIT" in content
    assert "approx_preview=True" in content
    assert "query=" in content and "materialized_rows=1" in content
    assert "SELECT " not in content
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
