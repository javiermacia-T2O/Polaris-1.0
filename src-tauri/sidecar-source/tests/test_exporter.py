import threading

import pandas as pd
import pytest

from core.exporter import export_dataframe, save_figure_isolated
from core.tasks import TaskCancelled


@pytest.mark.parametrize("fmt", ["csv", "tsv", "xlsx", "parquet"])
def test_export_formats(tmp_path, fmt):
    frame = pd.DataFrame({"name": ["a", "b"], "value": [1, 2]})
    target = tmp_path / f"result.{fmt}"
    result = export_dataframe(frame, target, fmt)
    if fmt == "xlsx":
        read = pd.read_excel(target)
    elif fmt == "parquet":
        read = pd.read_parquet(target)
    else:
        read = pd.read_csv(target, sep="\t" if fmt == "tsv" else ",")
    assert read.equals(frame)
    assert result["meta"]["size_mb"] > 0


def test_cancelled_export_preserves_previous_file(tmp_path):
    target = tmp_path / "result.csv"
    target.write_text("previous", encoding="utf-8")
    cancel = threading.Event()

    def progress(update):
        cancel.set()

    with pytest.raises(TaskCancelled):
        export_dataframe(pd.DataFrame({"v": [1, 2, 3]}), target, "csv",
                         cancel=cancel, progress=progress, chunksize=1)
    assert target.read_text(encoding="utf-8") == "previous"


def test_figure_export_uses_clone_and_preserves_previous_on_cancel(tmp_path):
    from matplotlib.figure import Figure

    figure = Figure()
    figure.add_subplot().plot([1, 2, 3])
    original_canvas = figure.canvas
    target = tmp_path / "plot.png"
    save_figure_isolated(figure, target)
    assert target.read_bytes().startswith(b"\x89PNG")
    assert figure.canvas is original_canvas

    target.write_bytes(b"previous")
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(TaskCancelled):
        save_figure_isolated(figure, target, cancel=cancelled)
    assert target.read_bytes() == b"previous"
