from types import SimpleNamespace
from threading import Event

import pandas as pd

from app_desktop import MMMApp
from services.active_dataset import ActiveDataset


def test_metric_pivot_is_independent_of_default_for_new_values():
    frame = pd.DataFrame({"region": ["a", "a", "b"],
                          "channel": ["x", "y", "x"],
                          "value": [1, 2, 3]})
    state = SimpleNamespace(
        tb_pivot_enabled=True,
        tb_rows=["region"], tb_cols=["channel"],
        tb_vals=[{"col": "value", "agg": "sum", "pivot": False}],
        tb_filters={}, tb_col_types={},
        _tb_source_df=lambda: frame,
    )
    for enabled in [True, False, True, False]:
        state.tb_pivot_enabled = enabled
        result = MMMApp._tb_build_df(state, preview=False)
        assert tuple(result.columns) == ("region", "channel", "value")
    state.tb_vals[0]["pivot"] = True
    pivoted = MMMApp._tb_build_df(state, preview=False)
    assert tuple(pivoted.columns) == ("region", "x", "y")


def test_pandas_active_filters_combine_table_and_value_filters():
    base = pd.DataFrame({"region": ["a", "a", "b"],
                         "channel": ["x", "y", "x"],
                         "value": [1, 2, 3]})
    refreshed = []
    state = SimpleNamespace(
        session=SimpleNamespace(active_dataset=ActiveDataset.from_frame(base),
                                base_view=base),
        value_filters={"region": {"a", "b"}},
        tb_filters={"region": {"a"}, "channel": {"x"}},
        _pandas_filter_key=None, _kpi_cache_key=None, _preview_page=3,
        tasks=SimpleNamespace(busy=False, closing=False),
        _combined_active_filters=lambda: MMMApp._combined_active_filters(state),
        _refresh_preview=lambda: refreshed.append(True),
    )

    def run_task(work, done, label):
        done(work(Event(), lambda *_: None))

    state._start_task = run_task
    assert MMMApp._schedule_pandas_filters(state)
    assert state.df_view["value"].tolist() == [1]
    assert state.session.active_dataset.stats()["rows"] == 1
    assert state._preview_page == 0
    assert refreshed == [True]

    state.tb_filters.clear()
    state.value_filters.clear()
    assert not MMMApp._schedule_pandas_filters(state)
    assert state.df_view is base
    assert state.session.active_dataset.stats()["rows"] == 3
