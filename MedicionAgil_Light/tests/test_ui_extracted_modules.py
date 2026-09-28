from matplotlib.figure import Figure
import pandas as pd

from app_desktop import (
    AnalysisConfigDialog,
    PreflightDialog,
    extract_figures,
)
from ui.dialogs.analysis_config_dialog import (
    AnalysisConfigDialog as AnalysisConfigDialogModule,
)
from ui.dialogs.preflight_dialog import PreflightDialog as PreflightDialogModule
from ui.dialogs.export_dialog import ExportDialog as ExportDialogModule
from ui.dialogs.merge_dialog import MergeDialog as MergeDialogModule
from ui.dialogs.regression_dialog import (
    CHART_PALETTE as CHART_PALETTEModule,
    CHART_TYPES as CHART_TYPESModule,
    REGRESSION_TYPES as REGRESSION_TYPESModule,
    RegressionDialog as RegressionDialogModule,
)
from ui.dialogs.causal_impact_dialog import (
    CausalImpactDialog as CausalImpactDialogModule,
    _dims_fingerprint as dims_fingerprint_module,
)


def test_extract_figures_walks_nested_dicts_and_sequences():
    first = Figure()
    second = Figure()

    result = extract_figures({"left": [first], "right": (None, {"chart": second})})

    assert result == [first, second]
    assert result[0] is first
    assert result[1] is second


def test_preflight_dialog_is_reexported_without_creating_window():
    assert PreflightDialog is PreflightDialogModule


def test_export_dialog_is_reexported_without_creating_window():
    from app_desktop import ExportDialog

    assert ExportDialog is ExportDialogModule


def test_analysis_config_dialog_is_reexported_without_creating_window():
    assert AnalysisConfigDialog is AnalysisConfigDialogModule
    assert issubclass(AnalysisConfigDialogModule, __import__("tkinter").Toplevel)


def test_merge_dialog_is_reexported_without_creating_window():
    from app_desktop import MergeDialog

    assert MergeDialog is MergeDialogModule


def test_regression_dialog_is_reexported_without_creating_window():
    from app_desktop import (
        CHART_PALETTE, CHART_TYPES, REGRESSION_TYPES, RegressionDialog,
    )

    assert RegressionDialog is RegressionDialogModule
    assert REGRESSION_TYPES is REGRESSION_TYPESModule
    assert CHART_TYPES is CHART_TYPESModule
    assert CHART_PALETTE is CHART_PALETTEModule


def test_causal_impact_dialog_and_helper_are_reexported_without_window():
    from app_desktop import CausalImpactDialog, _dims_fingerprint

    assert CausalImpactDialog is CausalImpactDialogModule
    assert _dims_fingerprint is dims_fingerprint_module


def test_analysis_config_dialog_unique_values_helper():
    dialog = AnalysisConfigDialogModule.__new__(AnalysisConfigDialogModule)
    dialog._df = pd.DataFrame({"canal": [" B ", "A", "B", None, ""]})

    assert dialog._get_unique_values("canal") == ["A", "B"]
    assert dialog._get_unique_values("missing") == []
