import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from matplotlib.figure import Figure

from ui.dialogs.geox_dialog import clean_geo_values, geox_column_options


ANALYSES = Path(__file__).resolve().parents[1] / "mmm_app" / "analyses"


def _load_geox():
    spec = importlib.util.spec_from_file_location(
        "geox_dialog_plugin", ANALYSES / "Geo Test Google (GeoX).py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_geox_dialog_helpers_detect_columns_and_clean_geo_values():
    frame = pd.DataFrame({
        "Semana": pd.date_range("2026-01-01", periods=3),
        "region": [" Baleares ", "Canarias", "Baleares"],
        "totalRevenue": [1.0, 2.0, 3.0],
        "Country": ["ES", "ES", "ES"],
    })

    choices = geox_column_options(frame)

    assert choices["date"] == ["Semana"]
    assert choices["region"] == ["region"]
    assert choices["kpi"] == ["totalRevenue"]
    assert choices["market"] == ["Country"]
    assert clean_geo_values(frame["region"]) == ["Baleares", "Canarias"]


def test_geox_plugin_declares_custom_dialog_and_current_meridian_options():
    module = _load_geox()
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3),
        "region": ["A", "B", "C"], "sales": [1, 2, 3],
    })

    schema = module.get_config_schema(frame)
    by_key = {field["key"]: field for field in schema["fields"]}

    assert module.CUSTOM_DIALOG == "GeoXDialog"
    assert by_key["experiment_type"]["options"] == ["HOLDBACK", "GO_DARK", "HEAVY_UP"]
    assert by_key["methodology"]["options"] == ["TBR"]
    assert by_key["geo_assignment_rule"]["options"] == ["STRATIFIED_SAMPLING", "RANDOM"]
    assert "top_n" not in by_key


def test_geox_normalises_geo_selection_without_duplicates():
    module = _load_geox()

    assert module._normalise_geo_list([" A ", "B", "A", "", None]) == ["A", "B"]
    assert module._normalise_geo_list("A\n B\nA") == ["A", "B"]


def test_geox_rejects_invalid_kpi_instead_of_silently_using_zero(monkeypatch):
    module = _load_geox()
    monkeypatch.setitem(__import__("sys").modules, "meridian_geox",
                        SimpleNamespace(__version__="test"))
    frame = pd.DataFrame([
        {"date": date, "region": region,
         "sales": "incorrecto" if index == 0 and region == "A" else "10"}
        for index, date in enumerate(pd.date_range("2026-01-01", periods=8))
        for region in ("A", "B", "C", "D")
    ])

    result = module.run(frame, date_col="date", region_col="region",
                        kpi_col="sales")

    assert result["Estado"] == "ERROR · KPI no numérico"
    assert "1 valor" in result["Detalle"]


def test_geox_forwards_manual_matching_and_supported_constraints(monkeypatch):
    module = _load_geox()
    captured = {}
    design = SimpleNamespace(control_geos=["A"], treatment_geos=["B", "C", "D"])
    fake_geox = SimpleNamespace(
        __version__="test",
        ExperimentType=SimpleNamespace(HOLDBACK="holdback"),
        Methodology=SimpleNamespace(TBR="tbr"),
        GeoAssignmentRule=SimpleNamespace(STRATIFIED_SAMPLING="stratified"),
        TestType=SimpleNamespace(TWO_SIDED="two-sided"),
        DesignConfig=lambda **kwargs: captured.setdefault("config", kwargs),
        Budget=lambda **kwargs: kwargs,
        Constraints=lambda **kwargs: captured.setdefault("constraints", kwargs),
        run_design=lambda **kwargs: SimpleNamespace(designs=[design]),
    )
    monkeypatch.setitem(__import__("sys").modules, "meridian_geox", fake_geox)
    monkeypatch.setattr(module, "_plot_geo_split", lambda *_args: Figure())
    monkeypatch.setattr(module, "_plot_balance", lambda *_args: Figure())
    frame = pd.DataFrame([
        {"date": date, "region": region, "sessions": 10 + index}
        for index, date in enumerate(pd.date_range("2024-01-01", periods=8))
        for region in ("A", "B", "C", "D", "E")
    ])

    result = module.run(
        frame, date_col="date", region_col="region", kpi_col="sessions",
        included_control_geos=["A"], excluded_geos=["E"],
        alpha=0.05, power=0.9, n_candidates=500, n_ranked_candidates=20,
    )

    assert result["Estado"] == "OK"
    assert captured["constraints"]["budget_constraint"] == {"cell_1": {"budget": 50000.0}}
    assert captured["constraints"]["included_control_geos"] == {"A"}
    assert captured["constraints"]["excluded_geos"] == {"E"}
    assert captured["config"]["alpha"] == 0.05
    assert captured["config"]["n_candidates"] == 500


def test_geox_does_not_retry_with_relaxed_r2(monkeypatch):
    module = _load_geox()
    thresholds = []
    design = SimpleNamespace(control_geos=["A", "B"],
                             treatment_geos=["C", "D"], score=0.9)

    def run_design(**kwargs):
        threshold = kwargs["design_config"]["min_r2"]
        thresholds.append(threshold)
        if len(thresholds) == 1:
            raise ValueError(
                "No designs passed the min R2 check (r2 >= 0.8) "
                "(cell_1: 0 passed).")
        return SimpleNamespace(designs=[design])

    fake_geox = SimpleNamespace(
        __version__="test",
        ExperimentType=SimpleNamespace(HOLDBACK="holdback"),
        Methodology=SimpleNamespace(TBR="tbr"),
        GeoAssignmentRule=SimpleNamespace(STRATIFIED_SAMPLING="stratified"),
        TestType=SimpleNamespace(TWO_SIDED="two-sided"),
        DesignConfig=lambda **kwargs: kwargs,
        Budget=lambda **kwargs: kwargs,
        Constraints=lambda **kwargs: kwargs,
        run_design=run_design,
    )
    monkeypatch.setitem(__import__("sys").modules, "meridian_geox", fake_geox)
    monkeypatch.setattr(module, "_plot_geo_split", lambda *args: Figure())
    monkeypatch.setattr(module, "_plot_balance", lambda *args: Figure())

    dates = pd.date_range("2024-01-01", periods=8)
    frame = pd.DataFrame([
        {"date": date, "region": region, "sessions": 10 + index}
        for index, date in enumerate(dates)
        for region in ("A", "B", "C", "D")
    ])

    result = module.run(
        frame, date_col="date", region_col="region", kpi_col="sessions",
        min_r2=0.8,
    )

    assert result["Estado"] == "ERROR · sin diseños válidos"
    assert thresholds == [0.8]
    assert result["R² mínimo solicitado"] == 0.8


def test_geox_rejects_non_daily_observations_without_prorating(monkeypatch):
    module = _load_geox()
    monkeypatch.setitem(__import__("sys").modules, "meridian_geox",
                        SimpleNamespace(__version__="test"))
    dates = pd.date_range("2026-01-04", periods=8, freq="W-SUN")
    frame = pd.DataFrame([
        {"date": date, "region": region, "sessions": 10}
        for date in dates for region in ("A", "B", "C", "D")
    ])

    result = module.run(frame, date_col="date", region_col="region",
                        kpi_col="sessions")

    assert result["Estado"] == "ERROR · GeoX requiere datos diarios reales"
    assert "No se prorratearán" in result["Detalle"]


def test_geox_rejects_methodology_not_implemented_by_installed_api():
    module = _load_geox()
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=8),
        "region": ["A"] * 8, "sessions": [1] * 8,
    })
    result = module.run(frame, date_col="date", region_col="region",
                        kpi_col="sessions", methodology="SDID")

    assert result["Estado"] == "ERROR · metodología no soportada"
    assert "solo implementa TBR" in result["Detalle"]


def test_geox_rejects_missing_kpi_instead_of_turning_it_into_zero(monkeypatch):
    module = _load_geox()
    monkeypatch.setitem(__import__("sys").modules, "meridian_geox",
                        SimpleNamespace(__version__="test"))
    dates = pd.date_range("2026-01-01", periods=8)
    frame = pd.DataFrame([
        {"date": date, "region": region,
         "sessions": None if date == dates[2] and region == "A" else 10}
        for date in dates for region in ("A", "B", "C", "D")
    ])

    result = module.run(frame, date_col="date", region_col="region",
                        kpi_col="sessions")

    assert result["Estado"] == "ERROR · KPI con valores ausentes"
    assert "cero observado" in result["Detalle"]
