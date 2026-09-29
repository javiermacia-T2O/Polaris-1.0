"""Probe MedicionApplication.preview_table end-to-end with synthetic data.

Never reads user datasets. Mirrors exactly what the frontend sends.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "MedicionAgil_Light" / "mmm_app"))
sys.path.insert(0, str(ROOT / "MedicionAgil_Light" / "python"))

import pandas as pd  # noqa: E402

from medicion_core.application import MedicionApplication  # noqa: E402


def make_csv():
    df = pd.DataFrame({
        "Grupo": ["Sem A", "Sem A", "Sem B", "Sem B", "Sem C", "Sem C"],
        "Canal": ["A", "B", "A", "B", "A", "B"],
        "Soporte": ["TV", "Radio", "TV", "Radio", "TV", "Radio"],
        "Inversion": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        "KPI": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })
    path = Path(tempfile.gettempdir()) / "polaris_probe_roles.csv"
    df.to_csv(path, index=False)
    return str(path)


def show(app, dataset_id, label, recipe):
    try:
        page = app.preview_table(dataset_id, recipe, limit=20)
        print(f"\n=== {label} ===")
        print("columns:", page.columns)
        for row in page.rows[:8]:
            print("  ", row)
    except Exception as exc:  # noqa: BLE001
        print(f"\n=== {label} === ERROR: {type(exc).__name__}: {exc}")


app = MedicionApplication()
meta = app.load_dataset(make_csv())
did = meta.dataset_id
print("dataset:", did, "backend:", meta.backend, "rows:", meta.rows)

show(app, did, "row=Grupo only", {"rows": ["Grupo"]})
show(app, did, "column=Grupo only", {"columns": ["Grupo"]})
show(app, did, "row=Grupo, value=KPI sum",
     {"rows": ["Grupo"], "values": [{"col": "KPI", "agg": "sum"}]})
show(app, did, "row=Grupo, column=Canal, value=KPI sum",
     {"rows": ["Grupo"], "columns": ["Canal"],
      "values": [{"col": "KPI", "agg": "sum"}]})
show(app, did, "filter=Canal [A] only", {"filters": {"Canal": ["A"]}})
show(app, did, "row=Grupo, filter=Canal [A]",
     {"rows": ["Grupo"], "filters": {"Canal": ["A"]}})
show(app, did, "row=Grupo, filter=Canal [A], value=KPI sum",
     {"rows": ["Grupo"], "filters": {"Canal": ["A"]},
      "values": [{"col": "KPI", "agg": "sum"}]})
