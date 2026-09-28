import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from analyses import causal_impact
from ui.dialogs import causal_impact_dialog
from ui.dialogs.causal_impact_dialog import (
    CausalImpactDialog,
    _causal_config_path,
    _load_causal_config,
    _parse_task_filter,
)
from core.atomic import write_json_atomic


class _Var:
    def __init__(self, value=False):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_task_filter_supports_grouped_target_values():
    assert _parse_task_filter("region=DE|UK;channel=Paid") == {
        "region": ["DE", "UK"],
        "channel": ["Paid"],
    }


def test_causal_preferences_use_user_local_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    path = _causal_config_path("Ventas / España")
    write_json_atomic(path, {"date_col": "Fecha", "kpis": ["Ventas"]})

    assert path == (tmp_path / "MedicionAgil" / "causal_impact"
                    / "Ventas___España-a6fdf177c452.json")
    assert _causal_config_path("Ventas / España") != _causal_config_path(
        "Ventas ? España")
    assert len(path.name) <= 66
    assert _load_causal_config("Ventas / España") == {
        "date_col": "Fecha", "kpis": ["Ventas"]}


def test_rename_event_rolls_back_when_event_save_fails(monkeypatch):
    dialog = CausalImpactDialog.__new__(CausalImpactDialog)
    dialog.dataset_name = "dataset"
    dialog.events = {"groups": {}, "sub_events": [{
        "id": "event-1", "name": "Original", "start": "2025-01-01",
        "end": "2025-01-02", "intensity": 1.0, "group": "", "own": False,
    }]}
    dialog._refresh_events_list = lambda: None
    dialog._refresh_chart = lambda: None
    monkeypatch.setattr(causal_impact_dialog.simpledialog, "askstring",
                        lambda *args, **kwargs: "Nuevo")
    monkeypatch.setattr(causal_impact_dialog.messagebox, "showerror",
                        lambda *args, **kwargs: None)

    def fail_save(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(causal_impact_dialog, "save_events", fail_save)
    dialog._rename_event("event-1", "Original")

    assert dialog.events["sub_events"][0]["name"] == "Original"


def test_clear_session_resets_open_dialog_without_recreating_it():
    dialog = CausalImpactDialog.__new__(CausalImpactDialog)
    dialog.df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=2), "Revenue": [1, 2],
    })
    dialog.date_var = _Var("other_date")
    dialog.gran_var = _Var("mensual")
    dialog.series_sep_var = _Var(" | ")
    dialog.alpha_var = _Var("0.2")
    dialog.kpi_var = _Var("Revenue")
    dialog._kpi_vars = {"Revenue": _Var(True)}
    dialog._dim_vars = {"country": _Var(True)}
    dialog._sesgo_vars = {"Revenue": _Var(True)}
    dialog._series_vars = {"country=UK": _Var(True)}
    dialog._excluir_vars = {"country=UK": _Var(True)}
    dialog._series_selected = ["country=UK"]
    dialog._excluir_selected = ["country=UK"]
    dialog._target_groups = [{"task_str": "country=UK"}]
    dialog._last_series_signature = "old"
    dialog.search_series_var = _Var("UK")
    dialog.search_excluir_var = _Var("UK")
    dialog._series_data = [{}]
    dialog._excluir_data = [{}]
    dialog.pre_start_idx = dialog.pre_end_idx = 1
    dialog.post_start_idx = dialog.post_end_idx = 1
    dialog.pre_start_date_var = _Var("2024-01-01")
    dialog.pre_end_date_var = _Var("2024-01-02")
    dialog.post_start_date_var = _Var("2024-01-03")
    dialog.post_end_date_var = _Var("2024-01-04")
    dialog._agg_cache = object()
    dialog._agg_cache_key = "old"
    dialog._refresh_kpi_count = lambda: None
    dialog._render_series_list = lambda: None
    dialog._render_excluir_list = lambda: None
    dialog._refresh_excluir_chips = lambda: None
    dialog._render_targets_list = lambda: None
    dialog._refresh_chart = lambda: None
    dialog._initialize_defaults = lambda: None
    dialog._refresh_global_status = lambda: None

    dialog._reset_current_session()

    assert dialog.date_var.get() == "date"
    assert dialog.gran_var.get() == "semanal"
    assert dialog.alpha_var.get() == "0.05"
    assert dialog._target_groups == []
    assert dialog._series_selected == []
    assert dialog._excluir_selected == []
    assert dialog._agg_cache is None


def test_group_and_individual_targets_can_coexist():
    dialog = CausalImpactDialog.__new__(CausalImpactDialog)
    uk_baleares = "country=UK;island=Baleares"
    uk_canarias = "country=UK;island=Canarias"
    dialog._series_data = [
        {"display": "UK_Baleares", "task_str": uk_baleares,
         "dim_dict": {"country": "UK", "island": "Baleares"}},
        {"display": "UK_Canarias", "task_str": uk_canarias,
         "dim_dict": {"country": "UK", "island": "Canarias"}},
    ]
    dialog._series_vars = {uk_baleares: _Var(True), uk_canarias: _Var(True)}
    dialog._series_selected = [uk_baleares, uk_canarias]
    dialog._target_groups = []
    dialog.series_sep_var = _Var("_")
    dialog._refresh_tree_checkmarks = lambda: None
    dialog._on_series_toggle = lambda: None
    dialog._render_targets_list = lambda: None
    dialog._refresh_preview_selectors = lambda: None
    dialog._refresh_global_status = lambda: None

    dialog._add_selected_as_group()
    dialog._series_selected = [uk_baleares, uk_canarias]
    dialog._add_selected_as_individual()

    assert [group["task_str"] for group in dialog._target_groups] == [
        "country=UK;island=Baleares|Canarias",
        uk_baleares,
        uk_canarias,
    ]


def test_executive_summary_labels_each_control_with_pre_correlation():
    result = {
        "KPI": "Revenue",
        "Target": "Alemania",
        "Volumen": 100,
        "Num_Controles": 2,
        "Efecto_Absoluto": 12,
        "Efecto_Relativo": 12,
        "P_Valor": 0.02,
        "Inc_Prob": 0.9,
        "R_Pre": 0.70,
        "Controles": f"Spain{causal_impact.CONTROLS_SEP}France",
        "Correlaciones_Controles": {"Spain": 0.75, "France": 0.65},
    }

    summary = causal_impact._build_executive_table([result])

    assert summary.loc[0, "Controles Activos"] == (
        f"Spain (0.75){causal_impact.CONTROLS_SEP}France (0.65)")


def test_pre_backtest_is_deterministic_and_reports_out_of_sample_metrics():
    t = np.arange(60, dtype=float)
    target = 5 + 2 * t + np.sin(t / 2)
    controls = np.column_stack([t + np.cos(t / 3), np.sin(t)])
    first = causal_impact._pre_backtest_combo(target, controls)
    second = causal_impact._pre_backtest_combo(target, controls)

    assert first == second
    assert first["Folds"] >= 2
    assert first["RMSE_OOS"] >= 0
    assert np.isfinite(first["WAPE_OOS_pct"])


def test_pre_backtest_rejects_short_or_missing_series():
    assert causal_impact._pre_backtest_combo(
        np.arange(10.0), np.arange(10.0)) is None
    y = np.arange(20.0)
    x = np.arange(20.0)
    x[3] = np.nan
    assert causal_impact._pre_backtest_combo(y, x) is None


def test_seasonality_is_inferred_from_pre_data_and_requires_repeated_cycles():
    t = np.arange(156, dtype=float)
    seasonal = 20 + 0.1 * t + 5 * np.sin(2 * np.pi * t / 52)
    detected = causal_impact._infer_seasonality_pre(seasonal, "semanal")
    too_short = causal_impact._infer_seasonality_pre(seasonal[:100], "semanal")

    assert detected["period"] == 52
    assert detected["strength"] >= 0.15
    assert too_short["period"] is None


def test_executive_summary_shows_pre_quality_backend_and_discarded_reason():
    result = {
        "KPI": "Revenue", "Target": "DE", "Volumen": 100,
        "Num_Controles": 1, "Efecto_Absoluto": 2,
        "Efecto_Relativo": 2, "P_Valor": 0.2, "Inc_Prob": 0.6,
        "R_Pre": 0.4, "Controles": "ES",
        "Metricas_Backtest_PRE": {"RMSE_OOS": 3, "MAE_OOS": 2},
        "Backend": "statsmodels", "quality_status": "Media",
        "Controles_Descartados": [
            {"Control": "FR", "Motivo": "Serie constante en PRE"}],
    }

    summary = causal_impact._build_executive_table([result])

    assert summary.loc[0, "RMSE PRE OOS"] == 3
    assert summary.loc[0, "Backend"] == "statsmodels"
    assert summary.loc[0, "Controles descartados"] == (
        "FR: Serie constante en PRE")


def test_executive_summary_marks_unavailable_values_without_nan_artifacts():
    summary = causal_impact._build_executive_table([{
        "KPI": "Revenue", "Target": "DE", "Volumen": np.nan,
        "Num_Controles": 1, "Efecto_Absoluto": np.nan,
        "Efecto_Relativo": np.nan, "P_Valor": np.nan,
        "Inc_Prob": np.nan, "R_Pre": np.nan, "Controles": "ES",
        "Metricas_Backtest_PRE": {"RMSE_OOS": np.nan},
    }])

    row = summary.iloc[0]
    assert row["P-Valor"] == "—"
    assert row["Significancia"] == "No disponible"
    assert row["PIP"] == "—"
    assert row["RMSE PRE OOS"] == "—"
    assert row["Efecto Relativo"] == "—"


def test_run_accepts_multiple_kpis_through_compat_field_and_reports_progress(
        monkeypatch):
    dates = pd.date_range("2024-01-01", periods=40, freq="D")
    frame = pd.DataFrame({
        "date": np.repeat(dates, 2),
        "region": ["DE", "ES"] * len(dates),
        "Revenue": np.tile([10.0, 9.0], len(dates)),
        "Users": np.tile([5.0, 4.0], len(dates)),
    })

    def fake_analyze(wide, dim_map, task, target_name, *args, **kwargs):
        winner = {
            "Target": target_name,
            "Controles": "ES",
            "Num_Controles": 1,
            "Efecto_Absoluto": 1.0,
            "Efecto_Relativo": 2.0,
            "P_Valor": 0.01,
            "Inc_Prob": 0.8,
            "R_Pre": 0.75,
            "Correlaciones_Controles": {"ES": 0.75},
            "Volumen": 100.0,
        }
        return {
            "winner": winner,
            "distribution": [],
            "wide_target": pd.DataFrame({"Fecha_Analisis": dates}),
        }

    monkeypatch.setattr(causal_impact, "_analyze_target", fake_analyze)
    for name in (
        "plot_causal_impact_classic", "plot_ci_panel_series",
        "plot_ci_panel_effect", "plot_ci_panel_cumulative",
        "plot_distribution_boxplot",
    ):
        monkeypatch.setattr(causal_impact, name, lambda *a, **k: Figure())

    progress = []
    output = causal_impact.run(
        frame, date_col="date", kpi_col=["Revenue", "Users"],
        dim_cols=["region"], target_tasks="region=DE",
        fecha_campana="2024-01-25", fecha_fin_datos="2024-02-09",
        min_controles=1, max_controles=1,
        progress_callback=lambda percent, message: progress.append(
            (percent, message)),
    )

    summary = output["Resumen ejecutivo"]
    assert set(summary["KPI"]) == {"Revenue", "Users"}
    assert progress[0] == (2, "Preparando targets...")
    assert progress[-1] == (100, "Causal Impact completado")
    assert any("KPI: Revenue" in message for _, message in progress)
    assert any("KPI: Users" in message for _, message in progress)
