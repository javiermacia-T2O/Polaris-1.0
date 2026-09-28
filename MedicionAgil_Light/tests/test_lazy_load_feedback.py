from threading import Event
from types import SimpleNamespace

import pandas as pd

from app_desktop import MMMApp
from services.active_dataset import ActiveDataset, LazyLoaded


def test_file_load_uses_bounded_preview_and_row_count(tmp_path, monkeypatch):
    source = tmp_path / "datos.parquet"
    pd.DataFrame({"Fecha": ["2025-01-01"], "valor": [2]}).to_parquet(source)
    dataset = ActiveDataset.open_file(source)
    monkeypatch.setattr(ActiveDataset, "stats", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(AssertionError("No se necesita perfil de nulos"))))
    result = []
    app = SimpleNamespace(
        loaded_datasets={},
        _dataset_name_for=lambda path: path.stem,
        _read_dataset=lambda path, retained, progress: dataset,
        _start_task=lambda work, done, label: result.append(
            work(Event(), lambda update: None)),
    )

    MMMApp._start_file_load(app, source)

    assert isinstance(result[0], LazyLoaded)
    assert result[0].stats == {"rows": 1, "columns": 2, "nulls": {}}
    assert len(result[0].preview) == 1


class _Label:
    def __init__(self):
        self.values = {}

    def config(self, **kwargs):
        self.values.update(kwargs)


def test_lazy_activation_renders_loaded_page_before_deferred_work(tmp_path):
    source = tmp_path / "datos.parquet"
    pd.DataFrame({"Fecha": ["2025-01-01"], "valor": [2]}).to_parquet(source)
    dataset = ActiveDataset.open_file(source)
    preview = dataset.preview()
    events = []
    app = SimpleNamespace(
        loaded_datasets={"datos": LazyLoaded(dataset, {
            "rows": 1, "columns": 2, "nulls": {}}, preview)},
        session=SimpleNamespace(active_dataset=None, active_stats=None,
                                base_view=None),
        tb_rows=[], tb_cols=[], tb_vals=[], tb_filters={}, tb_col_types={},
        visible_columns=[], value_filters={}, lbl_file=_Label(),
        _refresh_dataset_combo=lambda: None,
        _refresh_header=lambda: None,
        _refresh_preview=lambda filtered_df=None: events.append(
            ("render", filtered_df)),
        after=lambda delay, callback: events.append(("deferred", delay)),
    )

    MMMApp._activate_dataset(app, "datos")

    assert events[0][0] == "render"
    assert events[0][1] is preview
    assert events[1][0] == "deferred"
    assert app.session.active_dataset.backend == "parquet"


def test_lazy_date_range_does_not_repeat_all_column_stats():
    dates = (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-02-01"))
    calls = []

    def no_stats(*args, **kwargs):
        raise AssertionError("No se debe repetir stats de todas las columnas")

    active = SimpleNamespace(
        backend="parquet", stats=no_stats,
        date_range=lambda col, cancel: (calls.append(col) or dates))

    class Entry:
        def delete(self, *_args):
            pass

        def insert(self, *_args):
            pass

    app = SimpleNamespace(
        df_raw=pd.DataFrame({"Fecha": ["2025-01-01"]}),
        session=SimpleNamespace(active_dataset=active,
                                active_stats={"rows": 1, "columns": 1}),
        lbl_range=_Label(), entry_start=Entry(), entry_end=Entry(),
    )

    def start_task(work, done, _label):
        done(work(Event(), lambda _update: None))
        return True

    app._start_task = start_task
    MMMApp._populate_date_range_lazy(app, "Fecha")

    assert calls == ["Fecha"]
    assert app.session.active_stats["date_range"] == dates
