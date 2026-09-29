"""Probe the table-builder preview logic with synthetic data only.

Never reads user datasets. Reproduces the role-assignment behaviour so the
preview can be verified without opening the app.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "MedicionAgil_Light" / "mmm_app"))
sys.path.insert(0, str(ROOT / "MedicionAgil_Light" / "python"))

import pandas as pd  # noqa: E402

from models.table_recipe import TableRecipe  # noqa: E402
from services.table_service import build_table_preview_pandas  # noqa: E402


def frame():
    return pd.DataFrame({
        "Semana": ["W1", "W1", "W2", "W2", "W3", "W3"],
        "Canal": ["A", "B", "A", "B", "A", "B"],
        "Soporte": ["TV", "Radio", "TV", "Radio", "TV", "Radio"],
        "Inversion": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        "KPI": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })


def show(label, **parts):
    recipe = TableRecipe.from_parts(**parts)
    try:
        out = build_table_preview_pandas(
            frame(), recipe.rows, recipe.columns, recipe.values,
            dict(recipe.filters), recipe=recipe)
        print(f"\n=== {label} ===")
        print("columns:", list(out.columns))
        print(out.head(8).to_string(index=False))
    except Exception as exc:  # noqa: BLE001
        print(f"\n=== {label} === ERROR: {type(exc).__name__}: {exc}")


show("row=Semana only", rows=["Semana"])
show("row=Canal only", rows=["Canal"])
show("column=Semana only", cols=["Semana"])
show("row=Semana, value=KPI sum", rows=["Semana"],
     val_specs=[{"col": "KPI", "agg": "sum"}])
show("row=Semana, column=Canal, value=KPI sum", rows=["Semana"],
     cols=["Canal"], val_specs=[{"col": "KPI", "agg": "sum"}])
show("value=KPI sum only", val_specs=[{"col": "KPI", "agg": "sum"}])
show("filter=Canal [A]", filters={"Canal": ["A"]})
show("row=Semana, filter=Canal [A]", rows=["Semana"], filters={"Canal": ["A"]})