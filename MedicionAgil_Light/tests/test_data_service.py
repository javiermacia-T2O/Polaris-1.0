from types import SimpleNamespace

import pandas as pd
import pytest

from app_desktop import MMMApp
from core.tasks import TaskCancelled
from services.data_service import (
    convert_types,
    detect_types,
    ensure_date_sorted,
    filter_values,
    prepare_df_for_analysis,
    read_dataset,
    limited_unique_strings,
    preview_page,
)


def test_preview_page_bounds_rows_and_clamps_after_dataset_shrinks():
    source = pd.DataFrame({"value": range(405)})
    first, page, count = preview_page(source, 0)
    assert (len(first), page, count) == (200, 0, 3)
    last, page, count = preview_page(source, 99)
    assert (last["value"].tolist(), page, count) == ([400, 401, 402, 403, 404], 2, 3)
    empty, page, count = preview_page(source.iloc[:0], 2)
    assert (len(empty), page, count) == (0, 0, 1)


def test_limited_unique_strings_bounds_high_cardinality_and_keeps_empty():
    assert limited_unique_strings(pd.Series([None, "b", "a", "a"])) == ["a", "b"]
    assert limited_unique_strings(pd.Series([None])) == []
    assert limited_unique_strings(pd.Series(range(501))) is None


def test_limited_unique_strings_honours_cancel():
    from threading import Event

    cancel = Event()
    cancel.set()
    with pytest.raises(TaskCancelled):
        limited_unique_strings(pd.Series(range(100)), cancel=cancel)


def test_read_dataset_falls_back_to_lazy_before_full_loader(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    calls = []

    lazy = object()
    result = read_dataset(
            source, 0, lambda *_: None,
            ensure_load_fits=lambda *_: (_ for _ in ()).throw(MemoryError()),
            loader=lambda *_args, **_kwargs: calls.append(True),
            lazy_opener=lambda _path: lazy,
        )

    assert result is lazy
    assert calls == []


def test_filter_values_and_wrapper_are_equivalent_without_source_mutation():
    source = pd.DataFrame({"segment": ["a", "b", "a"], "value": [1, 2, 3]})
    filters = {"segment": {"a"}, "value": {1, "3"}}

    service_result = filter_values(source, filters)
    wrapper_result = MMMApp._filter_values(source, filters)

    pd.testing.assert_frame_equal(service_result, wrapper_result)
    assert service_result["value"].tolist() == [1, 3]
    assert source["value"].tolist() == [1, 2, 3]


def test_detect_and_convert_types_match_wrappers_and_keep_lossless_contract():
    source = pd.DataFrame({
        "fecha": ["2024-01-01", "2024-02-01"],
        "amount": ["1", "2"],
        "code": ["001", "002"],
    })
    types = {"fecha": "fecha", "amount": "numero"}

    assert detect_types(source) == MMMApp._detect_types(source)
    service_result = convert_types(source, types)
    wrapper_result = MMMApp._convert_types(source, types)
    pd.testing.assert_frame_equal(service_result, wrapper_result)
    assert source["amount"].tolist() == ["1", "2"]

    with pytest.raises(ValueError, match="no numéricos"):
        convert_types(source, {"code": "numero"})
    assert source["code"].tolist() == ["001", "002"]


def test_convert_types_honours_cancellation_before_mutation():
    source = pd.DataFrame({"amount": ["1", "2"]})
    cancelled = SimpleNamespace(is_set=lambda: True)

    with pytest.raises(TaskCancelled):
        convert_types(source, {"amount": "numero"}, cancelled)
    assert source["amount"].tolist() == ["1", "2"]


def test_sort_and_prepare_match_wrappers():
    source = pd.DataFrame({
        "fecha": pd.to_datetime(["2024-02-01", "2024-01-01"]),
        "amount": ["2", "1"],
    })
    service_sorted = ensure_date_sorted(source.copy(), "sample", log=lambda _: None)
    wrapper_sorted = MMMApp._ensure_date_sorted(
        SimpleNamespace(), source.copy(), "sample")
    pd.testing.assert_frame_equal(service_sorted, wrapper_sorted)
    assert service_sorted["fecha"].tolist() == sorted(service_sorted["fecha"].tolist())

    app = SimpleNamespace(column_types={"amount": "numero"})
    service_prepared = prepare_df_for_analysis(source, app.column_types)
    wrapper_prepared = MMMApp._prepare_df_for_analysis(app, source)
    pd.testing.assert_frame_equal(service_prepared, wrapper_prepared)
    assert source["amount"].tolist() == ["2", "1"]
