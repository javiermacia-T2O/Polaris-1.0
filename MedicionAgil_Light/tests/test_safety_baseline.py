import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from core import engine, events, loader
from core.atomic import atomic_output


def test_critical_modules_import():
    import app_desktop  # noqa: F401
    import analyses.regression  # noqa: F401
    import analyses.causal_impact  # noqa: F401


def test_load_small_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    source = tmp_path / "sample.csv"
    source.write_text("Canal,Importe\nWeb,10\nTienda,20\n", encoding="utf-8")
    frame = loader.load_file(source)
    assert len(frame) == 2
    assert frame["Importe"].tolist() == [10, 20]


def test_numeric_conversion_of_valid_values():
    frame = pd.DataFrame({"importe": pd.Series(["1", "2", "3"], dtype=object)})
    out = loader._force_numeric_coercion(frame)
    assert out["importe"].tolist() == [1, 2, 3]


def test_numeric_conversion_preserves_mixed_values():
    frame = pd.DataFrame({"codigo": pd.Series(["101", "102", "X103"], dtype=object)})
    out = loader._force_numeric_coercion(frame)
    assert out["codigo"].tolist() == ["101", "102", "X103"]


def test_numeric_conversion_with_pandas_string_dtype():
    frame = pd.DataFrame({"importe": ["1", "2", "3"]})
    out = loader._force_numeric_coercion(frame)
    assert out["importe"].tolist() == [1, 2, 3]


def test_numeric_conversion_checks_values_outside_sample():
    values = [str(n) for n in range(1000)] + ["unknown"]
    frame = pd.DataFrame({"value": pd.Series(values, dtype=object)})
    out = loader._force_numeric_coercion(frame)
    assert out["value"].iloc[-1] == "unknown"
    assert out["value"].iloc[0] == "0"


def test_numeric_conversion_preserves_leading_zero_codes():
    frame = pd.DataFrame({"code": ["0012", "0045"]})
    out = loader._force_numeric_coercion(frame)
    assert out["code"].tolist() == ["0012", "0045"]


def test_cache_distinguishes_source_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    left = tmp_path / "a" / "same.csv"
    right = tmp_path / "b" / "same.csv"
    assert engine.parquet_path_for(left) != engine.parquet_path_for(right)


def test_cache_rejects_stale_source(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    source = tmp_path / "data.csv"
    source.write_text("v\n1\n", encoding="utf-8")
    cache = engine.parquet_path_for(source)
    cache.parent.mkdir()
    cache.write_bytes(b"old")
    os.utime(cache, (100, 100))
    os.utime(source, (200, 200))
    assert not engine.is_cached(source)


def test_cache_roundtrip_and_corrupt_file_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    source = tmp_path / "source.csv"
    source.write_text("v\n1\n", encoding="utf-8")
    cache = engine.parquet_path_for(source)
    engine._write_source_cache(pd.DataFrame({"v": [1]}), source, cache)
    assert engine.is_cached(source)
    assert engine.read_parquet(cache)["v"].tolist() == [1]
    cache.write_bytes(b"corrupt")
    assert engine.load_dataframe(source)["v"].tolist() == [1]


def test_cache_invalidates_changed_source(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CACHE_DIR", tmp_path / "cache")
    source = tmp_path / "source.csv"
    source.write_text("v\n1\n", encoding="utf-8")
    cache = engine.parquet_path_for(source)
    engine._write_source_cache(pd.DataFrame({"v": [1]}), source, cache)
    source.write_text("v\n222\n", encoding="utf-8")
    assert not engine.is_cached(source)


def test_events_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(events, "EVENTS_DIR", tmp_path)
    payload = {"groups": {"A": {"color": "#fff"}}, "sub_events": []}
    events.save_events("dataset", payload)
    assert events.load_events("dataset") == payload


def test_events_keep_previous_file_on_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(events, "EVENTS_DIR", tmp_path)
    target = events.events_path("dataset")
    target.write_text('{"groups": {}, "sub_events": []}', encoding="utf-8")
    original = target.read_bytes()

    def broken_dump(value, stream, **kwargs):
        stream.write("partial")
        raise OSError("disk full")

    monkeypatch.setattr(events.json, "dump", broken_dump)
    with pytest.raises(OSError):
        events.save_events("dataset", {"groups": {}, "sub_events": []})
    assert target.read_bytes() == original


def test_events_refuse_to_overwrite_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(events, "EVENTS_DIR", tmp_path)
    target = events.events_path("dataset")
    target.write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError):
        events.save_events("dataset", {"groups": {}, "sub_events": []})
    assert target.read_text(encoding="utf-8") == "corrupt"


def test_regression_config_keeps_previous_file_on_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(events, "EVENTS_DIR", tmp_path)
    target = tmp_path / "regression" / "dataset__last.json"
    target.parent.mkdir()
    target.write_text('{"target": "old"}', encoding="utf-8")

    def broken_dump(value, stream, **kwargs):
        stream.write("partial")
        raise OSError("disk full")

    monkeypatch.setattr(events.json, "dump", broken_dump)
    events.save_last_regression_config("dataset", {"target": "new"})
    assert target.read_text(encoding="utf-8") == '{"target": "old"}'


def test_atomic_parquet_output_uses_duckdb(tmp_path):
    target = tmp_path / "table.parquet"
    with atomic_output(target) as temporary:
        assert not temporary.exists()
        result = engine.write_parquet_fast(pd.DataFrame({"v": [1, 2]}), temporary)
    assert result["engine"] == "duckdb"
    assert pd.read_parquet(target)["v"].tolist() == [1, 2]


def _merge_dialog(datasets):
    from app_desktop import MergeDialog

    return SimpleNamespace(
        datasets=datasets,
        _merge_key_vars={"key": SimpleNamespace(get=lambda: True)},
        var_how=SimpleNamespace(get=lambda: "inner"),
    ), MergeDialog


def test_merge_small_tables():
    left = pd.DataFrame({"key": [1, 2], "left": [10, 20]})
    right = pd.DataFrame({"key": [2, 3], "right": [30, 40]})
    dialog, cls = _merge_dialog({"a": left, "b": right})
    result = cls._do_merge(dialog, ["a", "b"])
    assert result.to_dict("list") == {"key": [2], "left": [20], "right": [30]}


def test_merge_rejects_exploding_join(tmp_path, monkeypatch):
    import app_desktop

    left = pd.DataFrame({"key": [1] * 2300, "left": range(2300)})
    right = pd.DataFrame({"key": [1] * 2300, "right": range(2300)})
    dialog, cls = _merge_dialog({"a": left, "b": right})
    monkeypatch.setattr(app_desktop.messagebox, "askyesno", lambda *a, **k: True)
    called = []
    monkeypatch.setattr(pd, "merge", lambda *a, **k: called.append(True) or pd.DataFrame())
    with pytest.raises(ValueError):
        cls._do_merge(dialog, ["a", "b"])
    assert not called


def test_merge_rejects_exploding_intermediate_join(monkeypatch):
    left = pd.DataFrame({"key": [1] * 2300})
    right = pd.DataFrame({"key": [1] * 2300})
    final = pd.DataFrame({"key": [2]})
    dialog, cls = _merge_dialog({"a": left, "b": right, "c": final})
    called = []
    monkeypatch.setattr(pd, "merge", lambda *a, **k: called.append(True))
    with pytest.raises(ValueError):
        cls._do_merge(dialog, ["a", "b", "c"])
    assert not called


def test_outer_merge_preserves_unmatched_rows():
    left = pd.DataFrame({"key": [1, 1]})
    right = pd.DataFrame({"key": [2, 2, 2]})
    dialog, cls = _merge_dialog({"a": left, "b": right})
    dialog.var_how = SimpleNamespace(get=lambda: "outer")
    result = cls._do_merge(dialog, ["a", "b"])
    assert len(result) == 5


def test_csv_export_small(tmp_path):
    from app_desktop import ExportDialog

    target = tmp_path / "result.csv"
    dummy = _export_stub()
    ExportDialog._write_csv_chunked(dummy, pd.DataFrame({"v": [1, 2]}), target)
    assert pd.read_csv(target)["v"].tolist() == [1, 2]


def test_csv_export_keeps_previous_file_on_failure(tmp_path, monkeypatch):
    from app_desktop import ExportDialog

    target = tmp_path / "result.csv"
    target.write_text("previous", encoding="utf-8")
    original = target.read_bytes()
    dummy = _export_stub()
    original_writer = pd.DataFrame.to_csv

    def fail_after_header(self, path_or_buf=None, *args, **kwargs):
        if kwargs.get("header") is False:
            path_or_buf.write("partial")
            raise OSError("disk full")
        return original_writer(self, path_or_buf, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", fail_after_header)
    with pytest.raises(OSError):
        ExportDialog._write_csv_chunked(dummy, pd.DataFrame({"v": [1]}), target)
    assert target.read_bytes() == original


def _export_stub():
    from app_desktop import ExportDialog

    dummy = SimpleNamespace(_update_progress=lambda *a, **k: None)
    dummy._write_csv_chunked_direct = lambda *a, **k: (
        ExportDialog._write_csv_chunked_direct(dummy, *a, **k))
    return dummy
