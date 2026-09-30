import pandas as pd
import pytest
from threading import Event
from types import SimpleNamespace

from core import memory_budget
from services.merge_service import (concat_active, concat_datasets,
                                    join_active, merge_datasets)
from services.active_dataset import ActiveDataset, LazyLoaded
from ui.dialogs.merge_dialog import (
    _dataset_columns, _dataset_shape, prepare_selected_datasets,
)


def test_concat_rejects_before_pandas_concat(monkeypatch):
    datasets = {
        "left": pd.DataFrame({"value": ["x"]}),
        "right": pd.DataFrame({"value": ["y"]}),
    }
    monkeypatch.setattr(memory_budget, "pandas_limit_bytes", lambda: 1)
    called = []
    monkeypatch.setattr(pd, "concat", lambda *args, **kwargs: called.append(True))

    with pytest.raises(MemoryError):
        concat_datasets(datasets, ["left", "right"], "all")
    assert not called


def test_merge_rejects_before_pandas_merge(monkeypatch):
    datasets = {
        "left": pd.DataFrame({"key": [1] * 2300}),
        "right": pd.DataFrame({"key": [1] * 2300}),
    }
    called = []
    monkeypatch.setattr(pd, "merge", lambda *args, **kwargs: called.append(True))

    with pytest.raises(ValueError):
        merge_datasets(datasets, ["left", "right"], ["key"], "inner")
    assert not called


def test_merge_keeps_numbered_suffixes():
    datasets = {
        "left": pd.DataFrame({"key": [1], "value": [10]}),
        "middle": pd.DataFrame({"key": [1], "value": [20]}),
        "right": pd.DataFrame({"key": [1], "value": [30]}),
    }

    result = merge_datasets(
        datasets, ["left", "middle", "right"], ["key"], "inner")

    assert result.to_dict("list") == {
        "key": [1], "value": [10], "value_1": [20], "value_2": [30],
    }


def test_lazy_concat_and_join_preserve_duplicates_and_null_keys(tmp_path):
    left_path, right_path = tmp_path / "left.parquet", tmp_path / "right.parquet"
    pd.DataFrame({"key": [1, 1, None], "left": ["a", "b", "n"]}).to_parquet(left_path)
    pd.DataFrame({"key": [1, None], "right": ["x", "z"]}).to_parquet(right_path)
    left, right = ActiveDataset.open_file(left_path), ActiveDataset.open_file(right_path)
    concatenated = concat_active([left, right], "all")
    assert concatenated.row_count() == 5
    joined = join_active([left, right], ["key"], "inner")
    result = joined.page(limit=20)
    assert len(result) == 3  # 2x key=1 plus the null-safe match
    assert result["right"].tolist().count("x") == 2
    assert "z" in result["right"].tolist()
    assert len(joined.dependencies) == 2


def test_merge_prepares_only_chosen_datasets():
    class LazySource:
        columns = ("key", "value")

        def __init__(self, frame):
            self.frame = frame
            self.calls = []

        def analysis_frame(self, cancel, reserved_bytes=0):
            self.calls.append(reserved_bytes)
            return self.frame

    chosen = LazySource(pd.DataFrame({"key": [1], "value": [2]}))
    unwanted = LazySource(pd.DataFrame({"key": [1], "value": [99]}))
    pool = {
        "first": pd.DataFrame({"key": [1], "value": [1]}),
        "chosen": LazyLoaded(chosen, {"rows": 1}, pd.DataFrame()),
        "unwanted": LazyLoaded(unwanted, {"rows": 2_000_000}, pd.DataFrame()),
    }
    assert _dataset_columns(pool["chosen"]) == ("key", "value")
    assert _dataset_shape(pool["unwanted"]) == (2_000_000, 2)

    prepared = prepare_selected_datasets(pool, ["first", "chosen"], Event())

    assert list(prepared) == ["first", "chosen"]
    assert chosen.calls and chosen.calls[0] > 0
    assert not unwanted.calls


def test_merge_selected_lazy_rejects_when_its_own_budget_is_exceeded():
    class TooLarge:
        columns = ("key",)

        def analysis_frame(self, cancel, reserved_bytes=0):
            raise MemoryError("dataset seleccionado demasiado grande")

    pool = {"small": pd.DataFrame({"key": [1]}),
            "large": LazyLoaded(TooLarge(), {"rows": 10_000_000},
                                pd.DataFrame())}
    with pytest.raises(MemoryError, match="seleccionado"):
        prepare_selected_datasets(pool, ["small", "large"], Event())


def test_merge_selector_opens_before_rejecting_a_lazy_pool_member(monkeypatch):
    import app_desktop

    opened = []

    class DummyDialog:
        result = None

        def __init__(self, parent, datasets, default_a=None):
            opened.append((list(datasets), default_a))

    monkeypatch.setattr(app_desktop, "MergeDialog", DummyDialog)
    app = SimpleNamespace(
        loaded_datasets={"small": pd.DataFrame({"key": [1]}),
                         "huge": LazyLoaded(
                             SimpleNamespace(columns=("key",)),
                             {"rows": 22_000_000}, pd.DataFrame())},
        active_dataset_name="small",
        tasks=SimpleNamespace(busy=False),
        wait_window=lambda _dialog: None,
        winfo_exists=lambda: True,
        _toast=lambda *args: pytest.fail("selector blocked before selection"),
    )
    app_desktop.MMMApp.on_open_merge_dialog(app)
    assert opened == [(["small", "huge"], "small")]
