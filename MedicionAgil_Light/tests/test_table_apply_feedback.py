from threading import Event
from types import SimpleNamespace

import pandas as pd

import app_desktop
from app_desktop import MMMApp
from core import engine, memory_budget
from models.table_recipe import TableRecipe
from services.active_dataset import ActiveDataset
from services.table_service import build_table_active_snapshot


def test_lazy_table_snapshot_counts_and_loads_before_activation(tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(memory_budget, "system_memory", lambda: (
        memory_budget.MemoryStatus(16 * memory_budget.GIB,
                                   8 * memory_budget.GIB)))
    path = tmp_path / "datos.parquet"
    pd.DataFrame({"Semana": ["S1", "S1", "S2"],
                  "Importe": [2, 3, 4]}).to_parquet(path)
    source = ActiveDataset.open_file(path)
    recipe = TableRecipe.from_parts(
        rows=["Semana"], cols=[],
        val_specs=[{"col": "Importe", "agg": "sum"}])
    updates = []

    view, stats, preview = build_table_active_snapshot(
        source, recipe, {}, None, cancel=Event(), progress=updates.append)

    assert view.backend == "parquet"
    assert stats["rows"] == 2
    assert stats["columns"] == 2
    assert len(preview) == 2
    assert set(preview["Semana"]) == {"S1", "S2"}
    percentages = [percent for percent, _ in updates]
    assert percentages == sorted(percentages)
    assert percentages[0] == 5 and percentages[-1] == 100


def test_simple_projection_stays_lazy_without_copying_entire_source(tmp_path,
                                                                     monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    path = tmp_path / "datos.parquet"
    pd.DataFrame({"Semana": ["S1", "S1", "S2"]}).to_parquet(path)
    source = ActiveDataset.open_file(path)
    recipe = TableRecipe.from_parts(rows=["Semana"], cols=[], val_specs=[])

    view, stats, _preview = build_table_active_snapshot(
        source, recipe, {}, None, cancel=Event())

    assert view.backend == "query"
    assert stats["rows"] == 3
    assert not list((tmp_path / "cache").rglob("table-*.parquet"))


def test_lazy_apply_announces_success_only_after_snapshot_and_render(monkeypatch):
    events = []
    source = SimpleNamespace(backend="query")
    view = SimpleNamespace(columns=("Semana",),
                           version_token=lambda: "new-version")
    preview = pd.DataFrame({"Semana": ["S1"]})

    def build_snapshot(*args, **kwargs):
        events.append("snapshot-ready")
        return view, {"rows": 1, "columns": 1, "nulls": {}}, preview

    monkeypatch.setattr(app_desktop, "build_table_active_snapshot",
                        build_snapshot)
    app = SimpleNamespace(
        tasks=SimpleNamespace(busy=False, closing=False),
        session=SimpleNamespace(active_dataset=source, active_stats=None,
                                base_view=None),
        _tb_builder_dataset=None,
        _tb_recipe=lambda: object(),
        _tb_preview_revision=2,
        value_filters={},
        combo_date=SimpleNamespace(get=lambda: ""),
        _refresh_preview=lambda: events.append("rendered"),
        _tb_flash=lambda *args, **kwargs: None,
        _log=lambda *args: None,
        _toast=lambda message, *args: events.append(message),
    )

    def start_task(work, done, label):
        events.append("task-started")
        app.work, app.done = work, done
        return True

    app._start_task = start_task
    MMMApp._tb_apply(app)
    assert events == ["task-started"]

    snapshot = app.work(Event(), lambda update: None)
    assert events == ["task-started", "snapshot-ready"]
    app.done(snapshot)

    assert events[-2:] == ["rendered", "Tabla aplicada al dataset activo"]
    assert app.session.active_dataset is view
    assert app.session.active_stats["rows"] == 1
    assert app.df_view is preview


def test_apply_waits_for_running_preview_without_duplicate_task():
    scheduled = []
    started = []
    app = SimpleNamespace(
        tasks=SimpleNamespace(busy=True, closing=False),
        _tb_preview_revision=4,
        active_dataset_name="actual",
        _tb_apply_pending=False,
        _tb_flash=lambda *args, **kwargs: None,
        _log=lambda *args: None,
        after=lambda _ms, callback: scheduled.append(callback),
        session=SimpleNamespace(active_dataset=SimpleNamespace(
            backend="query")),
        _tb_builder_dataset=None,
        _tb_recipe=lambda: object(),
        value_filters={},
        combo_date=SimpleNamespace(get=lambda: ""),
        _start_task=lambda *args: started.append(args),
    )
    app._tb_apply = lambda: MMMApp._tb_apply(app)

    app._tb_apply()
    app._tb_apply()
    assert len(scheduled) == 1
    assert not started

    app.tasks.busy = False
    scheduled.pop()()
    assert len(started) == 1
    assert not app._tb_apply_pending
