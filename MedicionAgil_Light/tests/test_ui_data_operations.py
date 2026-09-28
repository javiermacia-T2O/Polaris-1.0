import pandas as pd
from types import SimpleNamespace
from threading import Event

from app_desktop import MMMApp, RegressionDialog, CausalImpactDialog
from ui.dialogs.regression_dialog import ordered_temporal_chart_frame
from services.active_dataset import ActiveDataset


def test_worker_type_conversion_preserves_source_and_values():
    source = pd.DataFrame({"code": ["001", "002"], "amount": ["1", "2"]})
    result = MMMApp._convert_types(source, {"amount": "numero"})
    assert source["amount"].tolist() == ["1", "2"]
    assert result["amount"].tolist() == [1, 2]
    assert result["code"].tolist() == ["001", "002"]


def test_worker_value_filter_preserves_source():
    source = pd.DataFrame({"key": ["a", "b", "a"], "value": [1, 2, 3]})
    result = MMMApp._filter_values(source, {"key": {"a"}})
    assert result["value"].tolist() == [1, 3]
    assert len(source) == 3


def test_type_detection_uses_sample_and_does_not_modify_frame():
    source = pd.DataFrame({"value": [1, 2], "date": pd.to_datetime(
        ["2024-01-01", "2024-02-01"])})
    assert MMMApp._detect_types(source) == {"value": "numero", "date": "fecha"}
    assert source["value"].tolist() == [1, 2]


def test_regression_preview_samples_without_copying_full_dataset():
    source = pd.DataFrame({"value": range(120_000)})
    sample = RegressionDialog._sample_for_preview(source)
    assert len(sample) <= 50_000
    assert sample.iloc[0, 0] == 0
    assert sample.iloc[-1, 0] >= 100_000
    assert len(source) == 120_000


def test_regression_chart_is_bounded_on_tk_thread():
    source = pd.DataFrame({"value": range(50_000)})
    sample = RegressionDialog._sample_for_chart(source)
    assert len(sample) <= 2_000
    assert sample.iloc[0, 0] == 0
    assert sample.iloc[-1, 0] >= 49_000


def test_regression_chart_dates_are_sorted_with_series_aligned():
    source = pd.DataFrame({"date": ["2025-10-02", "bad", "2025-01-03",
                                    "2025-01-03", "2025-02-01"],
                           "target": [20, 99, 3, 4, None],
                           "driver": [200, 990, 30, 40, 50]},
                          index=[10, 11, 12, 13, 14])
    ordered, dates = ordered_temporal_chart_frame(source, "date")
    assert ordered["target"].iloc[:2].tolist() == [3, 4]
    assert pd.isna(ordered["target"].iloc[2])
    assert ordered["driver"].tolist() == [30, 40, 50, 200]
    assert dates.is_monotonic_increasing
    assert len(source) == 5


def test_regression_status_uses_its_own_configuration():
    class Label:
        def config(self, **kwargs):
            self.text = kwargs["text"]

    class Button:
        def state(self, value):
            self.last_state = value

    dialog = SimpleNamespace(
        var_cfg={"x": {"visible": True}},
        df_full=pd.DataFrame({"x": range(40), "y": range(40)}),
        target_var=SimpleNamespace(get=lambda: "y"),
        lbl_global_status=Label(), lbl_footer_status=Label(),
        btn_run=Button())
    RegressionDialog._refresh_global_status(dialog)
    assert "40 observaciones" in dialog.lbl_global_status.text
    assert dialog.btn_run.last_state == ["!disabled"]


def test_causal_preview_conversion_does_not_drop_mixed_values():
    frame = pd.DataFrame({"metric": ["10", "unknown", "20"]})
    dialog = SimpleNamespace(df=frame.copy())
    CausalImpactDialog._coerce_numeric_columns(dialog)
    assert dialog.df["metric"].tolist() == ["10", "unknown", "20"]


def test_causal_preview_conversion_does_not_change_source():
    source = pd.DataFrame({"metric": ["10", "20", "30"]})
    dialog = SimpleNamespace(df=source.iloc[::2])
    CausalImpactDialog._coerce_numeric_columns(dialog)
    assert dialog.df["metric"].tolist() == [10, 30]
    assert source["metric"].tolist() == ["10", "20", "30"]


def test_analysis_launch_uses_filtered_active_lazy_dataset(tmp_path):
    source = tmp_path / "analysis.parquet"
    pd.DataFrame({"group": ["a", "b"], "value": [1, 2]}).to_parquet(source)
    active = ActiveDataset.open_file(source).with_filters({"group": {"b"}})
    received = []
    state = SimpleNamespace(
        session=SimpleNamespace(active_dataset=active),
        chosen_analysis={"name": "test"},
        tasks=SimpleNamespace(busy=False),
        _run_analysis_with_frame=received.append,
        _start_task=lambda work, done, label: done(
            work(Event(), lambda *_: None)),
    )
    MMMApp.on_run_analysis(state)
    assert received[0]["value"].tolist() == [2]
