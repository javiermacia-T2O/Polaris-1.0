from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from core import engine, memory_budget


def test_budget_on_16_gib_machine():
    status = memory_budget.MemoryStatus(total=16 * memory_budget.GIB,
                                        available=8 * memory_budget.GIB)
    assert memory_budget.app_limit_bytes(status) == 6 * memory_budget.GIB
    assert 2 * memory_budget.GIB < memory_budget.duckdb_limit_bytes(status) < 3 * memory_budget.GIB
    assert 3 * memory_budget.GIB < memory_budget.pandas_limit_bytes(status) < 4 * memory_budget.GIB


def test_budget_shrinks_when_free_ram_is_low():
    status = memory_budget.MemoryStatus(total=16 * memory_budget.GIB,
                                        available=2 * memory_budget.GIB)
    assert memory_budget.app_limit_bytes(status) == memory_budget.GIB // 2
    assert status.available - memory_budget.app_limit_bytes(status) >= int(1.5 * memory_budget.GIB)


def test_budget_adapts_to_smaller_and_larger_machines():
    smaller = memory_budget.MemoryStatus(8 * memory_budget.GIB,
                                         4 * memory_budget.GIB)
    larger = memory_budget.MemoryStatus(32 * memory_budget.GIB,
                                        16 * memory_budget.GIB)
    assert memory_budget.app_limit_bytes(smaller) == int(2.5 * memory_budget.GIB)
    assert memory_budget.app_limit_bytes(larger) == 8 * memory_budget.GIB


def test_parquet_row_count_prevents_underestimation(tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    path = tmp_path / "large.parquet"
    path.write_bytes(b"placeholder")
    meta = SimpleNamespace(num_rows=10_000_000, num_columns=30,
                           num_row_groups=1,
                           row_group=lambda i: SimpleNamespace(total_byte_size=10_000_000))
    monkeypatch.setattr(pq, "read_metadata", lambda p: meta)
    monkeypatch.setattr(memory_budget, "system_memory", lambda: (
        memory_budget.MemoryStatus(16 * memory_budget.GIB,
                                   8 * memory_budget.GIB)))
    assert memory_budget.estimate_load_peak_bytes(path) > 5 * memory_budget.GIB
    with pytest.raises(MemoryError):
        memory_budget.ensure_load_fits(path)


def test_load_budget_counts_existing_frames(tmp_path, monkeypatch):
    path = tmp_path / "small.csv"
    path.write_text("v\n1\n", encoding="utf-8")
    monkeypatch.setattr(memory_budget, "system_memory", lambda: (
        memory_budget.MemoryStatus(16 * memory_budget.GIB,
                                   8 * memory_budget.GIB)))
    with pytest.raises(MemoryError):
        memory_budget.ensure_load_fits(path, retained_bytes=4 * memory_budget.GIB)


def test_load_plan_uses_disk_for_large_lazy_source(tmp_path, monkeypatch):
    source = tmp_path / "large.csv"
    source.write_bytes(b"x\n" + b"1\n" * 1024)
    monkeypatch.setattr(memory_budget, "estimate_load_peak_bytes",
                        lambda _path: 2 * memory_budget.GIB)
    status = memory_budget.MemoryStatus(16 * memory_budget.GIB,
                                        2 * memory_budget.GIB)
    plan = memory_budget.plan_load(source, status=status)
    assert plan.mode == "disk"
    assert plan.uses_disk
    assert "desde disco" in plan.message
    assert "aplicación seguirá disponible" in plan.message


def test_load_plan_explains_non_incremental_format(tmp_path, monkeypatch):
    source = tmp_path / "large.xlsx"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(memory_budget, "estimate_load_peak_bytes",
                        lambda _path: 2 * memory_budget.GIB)
    status = memory_budget.MemoryStatus(16 * memory_budget.GIB,
                                        2 * memory_budget.GIB)
    plan = memory_budget.plan_load(source, status=status)
    assert plan.mode == "unavailable"
    assert "CSV o Parquet" in plan.message


def test_read_dataset_reports_disk_mode_without_materializing(tmp_path, monkeypatch):
    from services import data_service

    source = tmp_path / "large.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    plan = memory_budget.LoadPlan(
        "disk", 2 * memory_budget.GIB, 0, memory_budget.GIB // 2,
        "Se trabajará desde disco con DuckDB; puede tardar más.")
    monkeypatch.setattr(memory_budget, "plan_load", lambda *_a, **_k: plan)
    messages, calls = [], []
    lazy = SimpleNamespace(backend="duckdb_csv", columns=("value",))
    result = data_service.read_dataset(
        source, 0, messages.append,
        loader=lambda *_a, **_k: pytest.fail("no debe materializar Pandas"),
        lazy_opener=lambda path: calls.append(path) or lazy)
    assert result is lazy
    assert calls == [source]
    assert "desde disco" in messages[0]


def test_read_dataset_recovers_if_memory_changes_during_full_load(
        tmp_path, monkeypatch):
    from services import data_service

    source = tmp_path / "changing.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    plan = memory_budget.LoadPlan(
        "pandas", 1, 0, memory_budget.GIB, "Carga directa en memoria disponible.")
    monkeypatch.setattr(memory_budget, "plan_load", lambda *_a, **_k: plan)
    lazy = SimpleNamespace(backend="duckdb_csv", columns=("value",))
    messages = []
    result = data_service.read_dataset(
        source, 0, messages.append,
        ensure_load_fits=lambda *_a, **_k: 1,
        loader=lambda *_a, **_k: (_ for _ in ()).throw(MemoryError("changed")),
        lazy_opener=lambda _path: lazy)
    assert result is lazy
    assert "RAM cambió" in messages[0]


def test_duckdb_memory_limit_is_below_joint_app_limit(monkeypatch):
    status = memory_budget.MemoryStatus(16 * memory_budget.GIB,
                                        8 * memory_budget.GIB)
    monkeypatch.setattr(memory_budget, "system_memory", lambda: status)
    engine.reset_conn()
    try:
        conn = engine.get_conn()
        actual = conn.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        assert actual.endswith("GiB") or actual.endswith("MiB")
        number, unit = actual.split()
        actual_bytes = float(number) * (memory_budget.GIB if unit == "GiB"
                                        else 1024**2)
        assert actual_bytes < 2 * memory_budget.GIB
    finally:
        engine.reset_conn()


def test_merge_limit_accounts_for_resident_inputs():
    frame = pd.DataFrame({"key": [1, 2], "value": [3, 4]})
    constrained = memory_budget.MemoryStatus(16 * memory_budget.GIB,
                                              256 * 1024**2)
    assert memory_budget.safe_merge_row_limit([frame, frame], constrained) < 5_000_000


def test_dataframe_operation_guard_rejects_large_extra_copy(monkeypatch):
    frame = pd.DataFrame({"v": [1, 2]})
    monkeypatch.setattr(memory_budget, "pandas_limit_bytes", lambda: 1)
    with pytest.raises(MemoryError):
        memory_budget.ensure_dataframe_operation_fits(frame, 2, "test")


def test_dataframe_operation_guard_counts_resident_source(monkeypatch):
    frame = pd.DataFrame({"v": range(100)})
    used = int(frame.memory_usage(deep=True).sum())
    monkeypatch.setattr(memory_budget, "pandas_limit_bytes",
                        lambda: used * 2 - 1)
    with pytest.raises(MemoryError):
        memory_budget.ensure_dataframe_operation_fits(frame, 1, "test")


def test_explicit_numeric_conversion_preserves_source_on_error():
    from app_desktop import MMMApp

    frame = pd.DataFrame({"code": pd.Series(["101", "X102"], dtype=object)})
    app = SimpleNamespace(df_raw=frame, column_types={"code": "numero"},
                          _view_is_built=False, df_view=frame)
    with pytest.raises(ValueError):
        MMMApp._apply_types_to_df(app)
    assert app.df_raw is frame
    assert app.df_raw["code"].tolist() == ["101", "X102"]


def test_worker_loader_checks_memory_before_loading_file(tmp_path, monkeypatch):
    import app_desktop

    source = tmp_path / "data.csv"
    source.write_text("v\n1\n", encoding="utf-8")
    app = SimpleNamespace()
    called = []
    monkeypatch.setattr(app_desktop.memory_budget, "ensure_load_fits",
                        lambda *a, **k: (_ for _ in ()).throw(MemoryError("budget")))
    monkeypatch.setattr(app_desktop, "load_file", lambda *a, **k: called.append(True))
    lazy = app_desktop.MMMApp._read_dataset(app, source, 0, lambda *_: None)
    assert lazy.backend == "duckdb_csv"
    assert lazy.row_count() == 1
    assert not called


def test_excel_export_guard_rejects_excessive_cell_budget(monkeypatch):
    frame = pd.DataFrame({"a": range(100), "b": range(100)})
    monkeypatch.setattr(memory_budget, "pandas_limit_bytes", lambda: 10_000)
    with pytest.raises(MemoryError):
        memory_budget.ensure_excel_export_fits([frame])
