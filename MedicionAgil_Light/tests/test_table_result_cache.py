import json
import threading

import pandas as pd
import pytest

from core import engine, memory_budget
from core.tasks import TaskCancelled
from models.table_recipe import TableRecipe
from services.active_dataset import ActiveDataset
from services.table_result_cache import materialize_table_result
from services.table_service import build_table_view


def _view(tmp_path):
    path = tmp_path / "origen.parquet"
    pd.DataFrame({"Semana": ["S1", "S1", "S2"],
                  "Ingreso": [2, 3, 7]}).to_parquet(path)
    source = ActiveDataset.open_file(path)
    recipe = TableRecipe.from_parts(
        rows=["Semana"], cols=[],
        val_specs=[{"col": "Ingreso", "agg": "sum"}])
    return source, build_table_view(source, recipe)


def test_materialized_table_is_parquet_and_analysis_does_not_repeat_groupby(
        tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    _source, view = _view(tmp_path)
    active = materialize_table_result(view)

    assert active.backend == "parquet"
    assert active.row_count() == 2
    assert "read_parquet" in active._source_sql()
    assert "GROUP BY" not in active._source_sql()
    result = active.analysis_frame().set_index("Semana")
    assert result.loc["S1", "Ingreso"] == 5
    assert result.loc["S2", "Ingreso"] == 7


def test_second_application_reuses_valid_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    _source, view = _view(tmp_path)
    first = materialize_table_result(view)
    monkeypatch.setattr(ActiveDataset, "export", lambda *args, **kwargs: (
        pytest.fail("La segunda aplicación no debe recalcular la tabla")))

    second = materialize_table_result(view)

    assert second.source == first.source
    assert second.row_count() == 2


def test_cache_cleanup_protects_active_table_result(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    _source, view = _view(tmp_path)
    active = materialize_table_result(view)
    assert engine.cache_size_mb() > 0

    assert engine.clear_cache([active.source]) == 0
    assert active.source.exists()
    assert engine.clear_cache() == 1
    assert not active.source.exists()
    assert not active.source.with_suffix(".json").exists()


def test_invalid_metadata_recomputes_instead_of_reusing(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    _source, view = _view(tmp_path)
    first = materialize_table_result(view)
    meta = first.source.with_suffix(".json")
    metadata = json.loads(meta.read_text(encoding="utf-8"))
    metadata["version"] = "stale"
    meta.write_text(json.dumps(metadata), encoding="utf-8")
    original = ActiveDataset.export
    calls = []

    def tracked(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ActiveDataset, "export", tracked)
    second = materialize_table_result(view)

    assert calls == [1]
    assert second.row_count() == 2


def test_cancel_during_materialization_does_not_publish_cache(
        tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    _source, view = _view(tmp_path)
    cancel = threading.Event()

    def progress(update):
        if "Exportadas" in update[1]:
            cancel.set()

    with pytest.raises(TaskCancelled):
        materialize_table_result(view, cancel=cancel, progress=progress)

    assert not list((tmp_path / "cache").rglob("table-*.parquet"))


def test_source_changed_during_export_does_not_publish_cache(
        tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    source, view = _view(tmp_path)
    original = ActiveDataset.export

    def changed(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        source.source.write_bytes(source.source.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(ActiveDataset, "export", changed)
    with pytest.raises(RuntimeError, match="origen cambió"):
        materialize_table_result(view)
    assert not list((tmp_path / "cache").rglob("table-*.parquet"))


def test_table_budget_reserves_main_connection_and_python_buffers():
    status = memory_budget.MemoryStatus(
        total=16 * memory_budget.GIB,
        available=int(3.5 * memory_budget.GIB))
    table_limit = memory_budget.table_query_duckdb_limit_bytes(status)
    joint = memory_budget.app_limit_bytes(status)
    main_limit = memory_budget.duckdb_limit_bytes(status) // 2

    assert 512 * 1024**2 < table_limit < 1.5 * memory_budget.GIB
    assert table_limit + main_limit + 512 * 1024**2 <= joint


def test_table_worker_can_use_more_ram_only_when_system_has_headroom():
    status = memory_budget.MemoryStatus(16 * memory_budget.GIB,
                                        8 * memory_budget.GIB)
    joint = memory_budget.app_limit_bytes(status)
    worker = memory_budget.table_query_duckdb_limit_bytes(status)
    main = memory_budget.duckdb_limit_bytes(status) // 2

    assert 2 * memory_budget.GIB < worker <= 3 * memory_budget.GIB
    assert worker <= int(joint * 0.55)
    assert worker + main + 512 * 1024**2 <= joint
