import datetime as dt
import threading

import pandas as pd
import pytest
from matplotlib.figure import Figure

from core.tasks import TaskCancelled
from services.result_bundle import (
    create_result_bundle,
    save_result_bundle,
)


def test_bundle_uses_date_client_analysis_time_and_manifest(tmp_path):
    moment = dt.datetime(2026, 9, 26, 12, 30)
    result = save_result_bundle(
        tmp_path,
        "Cliente 1",
        "Regresión",
        tables={"resumen": pd.DataFrame({"x": [1, 2]})},
        files={"datos.json": b"{}"},
        now=moment,
    )

    assert result.committed
    assert result.path == tmp_path / "26-09-2026" / "Cliente 1" / "Regresión 12-30"
    assert (result.path / "resumen.csv").exists()
    assert (result.path / "datos.json").read_bytes() == b"{}"
    manifest = (result.path / "manifest.json").read_text(encoding="utf-8")
    assert '"resumen.csv"' in manifest
    assert '"datos.json"' in manifest
    assert not list((result.path.parent).glob(".*"))


def test_existing_client_and_analysis_get_collision_suffix(tmp_path):
    moment = dt.datetime(2026, 9, 26, 12, 30)
    first = save_result_bundle(tmp_path, "Cliente 1", "Causal Impact", now=moment)
    second = save_result_bundle(tmp_path, "Cliente 1", "Causal Impact", now=moment)

    assert first.path.name == "Causal Impact 12-30"
    assert second.path.name == "Causal Impact 12-30 (2)"
    assert first.path.parent == second.path.parent


def test_windows_names_are_sanitized_and_existing_directory_can_be_selected(tmp_path):
    moment = dt.datetime(2026, 9, 26, 8, 5)
    selected = tmp_path / "26-09-2026" / "Cliente seleccionado"
    selected.mkdir(parents=True)

    result = save_result_bundle(
        tmp_path,
        "ignorado",
        'Análisis: /?*',
        client_directory=selected,
        now=moment,
    )

    assert result.path.parent == selected
    assert result.path.name.endswith(" 08-05")
    assert all(char not in result.path.name for char in '<>:"/\\|?*')


def test_long_unicode_figure_name_is_shortened_and_directory_is_created(tmp_path):
    # Reproduce the deeply nested output path used by the packaged Windows app
    # and the long, human-readable Causal Impact figure labels.
    root = tmp_path.joinpath(*(
        f"segmento_{index}_{'x' * 12}" for index in range(1)
    ))
    long_name = (
        ".01_CI - New_users · United Kingdom _ balearic islands _ "
        "canary islands " * 7
    ) + ".png"
    figure = Figure()
    figure.add_subplot().plot([1, 2, 3])

    result = save_result_bundle(
        root,
        "IBS",
        "Causal Impact (BSTS)",
        figures={long_name: figure},
        now=dt.datetime(2026, 9, 27, 10, 32),
    )

    assert result.committed
    assert result.path.is_dir()
    saved = list(result.path.glob("*.png"))
    assert len(saved) == 1
    assert saved[0].name.startswith(".01_CI - New_users")
    assert saved[0].suffix == ".png"
    assert len(str(saved[0]).encode("utf-16-le")) // 2 <= 240


def test_cancel_during_export_does_not_publish_partial_bundle(tmp_path):
    cancel = threading.Event()

    def progress(update):
        cancel.set()

    frame = pd.DataFrame({"x": range(10)})
    with pytest.raises(TaskCancelled):
        save_result_bundle(
            tmp_path,
            "Cliente",
            "Regresión",
            tables={"datos": frame},
            now=dt.datetime(2026, 9, 26, 12, 0),
            cancel=cancel,
            progress=progress,
        )

    client = tmp_path / "26-09-2026" / "Cliente"
    assert not (client / "Regresión 12-00").exists()
    assert not list(client.glob(".Regresión*"))


def test_context_manager_discards_staging_on_error(tmp_path):
    with pytest.raises(RuntimeError):
        with create_result_bundle(
            tmp_path, "Cliente", "Regresión", now=dt.datetime(2026, 9, 26, 12, 0)
        ) as bundle:
            bundle.save_text("ok.txt", "ok")
            raise RuntimeError("fallo de análisis")

    client = tmp_path / "26-09-2026" / "Cliente"
    assert not list(client.glob(".Regresión*"))
    assert not (client / "Regresión 12-00").exists()


def test_progress_is_global_and_monotonic(tmp_path):
    updates = []
    save_result_bundle(
        tmp_path,
        "Cliente",
        "Regresión",
        tables={
            "uno": pd.DataFrame({"x": [1]}),
            "dos": pd.DataFrame({"x": [2]}),
        },
        now=dt.datetime(2026, 9, 26, 12, 0),
        progress=updates.append,
    )

    fractions = [fraction for fraction, _detail in updates]
    assert fractions
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
