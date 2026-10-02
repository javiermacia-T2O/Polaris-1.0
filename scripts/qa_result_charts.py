"""Render the four regression figures from a tiny synthetic dataset for visual QA."""

from pathlib import Path
import os
import sys

root = Path(__file__).resolve().parents[1] / "src-tauri" / "sidecar-source"
sys.path[:0] = [str(root / "mmm_app"), str(root / "python")]

import numpy as np
import pandas as pd
from analyses.regression import run

x = np.arange(60, dtype=float)
frame = pd.DataFrame({
    "Fecha": pd.date_range("2025-01-01", periods=len(x)),
    "Canal_A": x + 3,
    "Canal_B": np.sin(x / 5) * 6 + 12,
    "Objetivo": 25 + 2.4 * x + 1.8 * np.sin(x / 5),
})
result = run(frame, regression_type="OLS (mínimos cuadrados, con p-values)",
             date_col="Fecha", target_col="Objetivo",
             input_cols=["Canal_A", "Canal_B"])
out = Path(os.environ.get("POLARIS_QA_CHART_DIR", "qa-charts"))
out.mkdir(exist_ok=True)
for index, (name, item) in enumerate(result.items()):
    if isinstance(item, dict) and item.get("plot") is not None:
        path = out / f"{index:02d}.png"
        item["plot"].savefig(path)
        print(name, path)
