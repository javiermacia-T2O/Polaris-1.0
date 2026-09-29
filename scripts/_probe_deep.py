"""Deep probe: inspect the registered frame and compiled SQL."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "MedicionAgil_Light" / "mmm_app"))
sys.path.insert(0, str(ROOT / "MedicionAgil_Light" / "python"))

import pandas as pd  # noqa: E402

from medicion_core.application import MedicionApplication  # noqa: E402
from models.table_recipe import TableRecipe  # noqa: E402
from core import engine as db_engine  # noqa: E402
from services.table_service import compile_table  # noqa: E402
from services.active_dataset import ActiveDataset  # noqa: E402


def make_csv():
    df = pd.DataFrame({
        "Semana": ["W1", "W1", "W2", "W2", "W3", "W3"],
        "Canal": ["A", "B", "A", "B", "A", "B"],
        "KPI": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })
    path = Path(tempfile.gettempdir()) / "polaris_probe_deep.csv"
    df.to_csv(path, index=False)
    return str(path)


app = MedicionApplication()
meta = app.load_dataset(make_csv())
entry = app._dataset(meta.dataset_id)
frame = entry.active._frame()
print("frame columns:", list(frame.columns))
print("frame dtypes:", dict(frame.dtypes.astype(str)))
print(frame.head().to_string(index=False))

name = db_engine.register(frame)
registered = ActiveDataset(
    name, "registered", tuple(str(c) for c in frame.columns),
    tuple(str(d) for d in frame.dtypes),
    version_hint=f"{id(frame)}:{frame.shape}")
print("\nregistered columns:", registered.columns)
print("registered types:", registered.types)
print("source sql:", registered._source_sql())
print("where:", registered._where())

recipe = TableRecipe.from_parts(rows=["Semana"])
sql, params, approx = compile_table(registered, recipe, preview=True)
print("\nSQL:", sql)
print("params:", params)
out = db_engine.get_conn().execute(sql, params).fetchdf()
print(out.to_string(index=False))
db_engine.unregister(name)
