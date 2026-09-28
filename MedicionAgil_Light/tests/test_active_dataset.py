import pandas as pd
import pytest

from services.active_dataset import ActiveDataset
from core.tasks import TaskCancelled


def test_analysis_uses_complete_filtered_lazy_dataset_with_memory_guard(
        tmp_path, monkeypatch):
    source = tmp_path / "analysis.parquet"
    pd.DataFrame({"group": ["a"] * 300 + ["b"] * 3,
                  "value": list(range(300)) + [7, 8, 9]}).to_parquet(source)
    active = ActiveDataset.open_file(source).with_filters({"group": {"b"}})
    result = active.analysis_frame()
    assert result["value"].tolist() == [7, 8, 9]
    from core import memory_budget
    monkeypatch.setattr(memory_budget, "pandas_limit_bytes", lambda: 1)
    with pytest.raises(MemoryError, match="Filtra o agrega"):
        active.analysis_frame()


def test_repeated_filter_reset_and_reapply_uses_original_source(tmp_path):
    source = tmp_path / "repeated.parquet"
    pd.DataFrame({"segment": ["a", "b", "a", None],
                  "value": [1, 2, 3, 4]}).to_parquet(source)
    original = ActiveDataset.open_file(source)
    for _ in range(3):
        filtered = original.with_filters({"segment": {"a"}})
        assert filtered.stats()["rows"] == 2
        assert filtered.preview()["value"].tolist() == [1, 3]
        reset = filtered.reset()
        assert reset.stats()["rows"] == 4
        assert reset.with_filters({"segment": {"b"}}).stats()["rows"] == 1
    assert original.stats()["rows"] == 4


@pytest.mark.parametrize("suffix", ["csv", "parquet"])
def test_lazy_active_dataset_filters_stats_preview_and_reset(tmp_path, suffix):
    frame = pd.DataFrame({
        "Country": ["Spain", "France", "Spain", "Spain"],
        "Value": [1, 2, None, 4],
    })
    path = tmp_path / f"source.{suffix}"
    if suffix == "csv":
        frame.to_csv(path, index=False)
    else:
        frame.to_parquet(path, index=False)

    original = ActiveDataset.open_file(path)
    assert original.row_count() == 4
    assert original.preview(limit=2).shape == (2, 2)
    active = original.with_filters({"Country": {"Spain"}})
    assert active.stats() == {
        "rows": 3, "columns": 2, "nulls": {"Country": 0, "Value": 1}}
    assert active.with_columns(["Country"]).preview(limit=2).columns.tolist() == ["Country"]
    assert active.reset().stats()["rows"] == 4
    assert original.stats()["rows"] == 4


def test_lazy_source_change_is_rejected(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text("v\n1\n", encoding="utf-8")
    dataset = ActiveDataset.open_file(path)
    path.write_text("v\n100\n200\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cambió"):
        dataset.preview()


def test_pandas_active_dataset_retains_original_reference():
    frame = pd.DataFrame({"x": [1, 2, 3]})
    original = ActiveDataset.from_frame(frame)
    active = original.with_filters({"x": {2}})
    assert active.row_count() == 1
    assert original.row_count() == 3
    assert active.reset().source is frame


def test_preview_does_not_allow_full_materialization(tmp_path):
    path = tmp_path / "source.parquet"
    pd.DataFrame({"x": range(10)}).to_parquet(path)
    dataset = ActiveDataset.open_file(path)
    with pytest.raises(ValueError, match="1000"):
        dataset.preview(limit=1001)


def test_lazy_file_never_calls_full_pandas_reader(tmp_path, monkeypatch):
    path = tmp_path / "source.parquet"
    pd.DataFrame({"x": range(100)}).to_parquet(path)
    monkeypatch.setattr(pd, "read_parquet", lambda *_a, **_k: pytest.fail("full Parquet read"))
    monkeypatch.setattr(pd, "read_csv", lambda *_a, **_k: pytest.fail("full CSV read"))
    dataset = ActiveDataset.open_file(path)
    assert dataset.row_count() == 100
    assert len(dataset.preview(limit=3)) == 3


def test_disk_backed_dataset_exposes_ui_safe_resource_status(tmp_path):
    path = tmp_path / "source.parquet"
    pd.DataFrame({"x": range(2)}).to_parquet(path)
    status = ActiveDataset.open_file(path).resource_status()
    assert status["mode"] == "disk"
    assert status["uses_disk"] is True
    assert "desde disco" in status["message"]


def test_date_range_scans_whole_filtered_source(tmp_path):
    path = tmp_path / "dated.parquet"
    pd.DataFrame({"date": ["2020-01-01", "2024-06-01", "2026-01-01"],
                  "group": ["a", "a", "b"]}).to_parquet(path)
    active = ActiveDataset.open_file(path).with_filters({"group": {"a"}})
    first, last = active.date_range("date")
    assert str(first) == "2020-01-01"
    assert str(last) == "2024-06-01"
    assert active.with_date_range("date", "2024-01-01", None).row_count() == 1
    assert active.with_date_range("date", "2024-01-01", None).reset().row_count() == 3


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_lazy_filtered_export_is_complete_and_atomic(tmp_path, fmt):
    source = tmp_path / "source.parquet"
    pd.DataFrame({"group": ["a", "b", "a"], "value": [1, 2, 3]}).to_parquet(source)
    active = ActiveDataset.open_file(source).with_filters({"group": {"a"}})
    target = tmp_path / f"out.{fmt}"
    active.export(target, fmt)
    result = pd.read_csv(target) if fmt == "csv" else pd.read_parquet(target)
    assert result["value"].tolist() == [1, 3]


def test_cancelled_lazy_export_preserves_previous_file(tmp_path):
    from threading import Event
    source = tmp_path / "source.parquet"
    pd.DataFrame({"value": range(10)}).to_parquet(source)
    target = tmp_path / "out.csv"
    target.write_text("original", encoding="utf-8")
    cancel = Event()
    cancel.set()
    with pytest.raises(TaskCancelled):
        ActiveDataset.open_file(source).export(target, "csv", cancel=cancel)
    assert target.read_text(encoding="utf-8") == "original"


def test_full_profiles_use_filtered_rows_beyond_first_preview(tmp_path):
    source = tmp_path / "source.parquet"
    frame = pd.DataFrame({"day": ["2020-01-01"] * 300 + ["2026-01-01"],
                          "group": ["a"] * 300 + ["b"],
                          "value": [1.0] * 300 + [None]})
    frame.to_parquet(source)
    original = ActiveDataset.open_file(source)
    details = original.profile_rows("day")
    assert details["rows"] == 301
    assert details["rows_with_null"] == 1
    assert str(details["date_range"][1]) == "2026-01-01"
    numeric = original.profile_columns("numeric")
    assert numeric[0]["nulls"] == 1
    filtered = original.with_filters({"group": {"a"}})
    assert filtered.profile_rows()["rows"] == 300
    assert filtered.profile_columns("numeric")[0]["nulls"] == 0


def test_streamed_csv_cache_is_reused_and_invalidated(tmp_path, monkeypatch):
    from core import engine
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    source = tmp_path / "same.csv"
    source.write_text("group,value\na,1\nb,2\n", encoding="utf-8")
    direct = ActiveDataset.open_file(source)
    assert direct.backend == "duckdb_csv"
    cache = direct.cache_as_parquet()
    assert cache.exists() and engine.is_cached(source)
    assert engine.clear_cache([cache]) == 0
    assert cache.exists()
    reused = ActiveDataset.open_file(source)
    assert reused.backend == "parquet"
    assert reused.row_count() == 2
    source.write_text("group,value\na,1\nb,2\nc,3\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cambió"):
        reused.stats()
    assert ActiveDataset.open_file(source).backend == "duckdb_csv"


def test_cancelled_csv_conversion_does_not_publish_cache(tmp_path, monkeypatch):
    from threading import Event
    from core import engine
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    source = tmp_path / "source.csv"
    source.write_text("x\n1\n2\n", encoding="utf-8")
    cancel = Event()
    cancel.set()
    with pytest.raises(TaskCancelled):
        ActiveDataset.open_file(source).cache_as_parquet(cancel)
    assert not engine.is_cached(source)


def test_distinct_values_reads_beyond_preview_with_limit(tmp_path):
    source = tmp_path / "source.parquet"
    pd.DataFrame({"group": ["a"] * 300 + ["b"]}).to_parquet(source)
    active = ActiveDataset.open_file(source)
    assert active.distinct_values("group") == ["a", "b"]
    assert active.distinct_values("group", limit=1) is None
