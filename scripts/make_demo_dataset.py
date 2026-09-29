"""Generate a realistic synthetic MMM dataset for E2E validation."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"C:\Users\javier.macia\Desktop\APP MEDICIÓN\MedicionAgil_Light\data\mmm_demo.csv")

rng = np.random.default_rng(42)
dates = pd.date_range("2023-01-02", periods=104, freq="W-MON")
regions = ["Norte", "Sur", "Este", "Oeste"]
channels = ["TV", "Radio", "Digital", "OOH"]

rows = []
for date in dates:
    trend = 1 + 0.004 * (date - dates[0]).days / 7
    season = 1 + 0.12 * np.sin(2 * np.pi * date.dayofyear / 365)
    for region in regions:
        base = {"Norte": 1.15, "Sur": 0.95, "Este": 1.05, "Oeste": 0.9}[region]
        for channel in channels:
            spend = max(0.0, rng.normal(1200, 350))
            rows.append({
                "fecha": date,
                "region": region,
                "canal": channel,
                "inversion": round(spend, 2),
                "impresiones": int(spend * rng.uniform(8, 14)),
                "clics": int(spend * rng.uniform(0.4, 1.1)),
            })

frame = pd.DataFrame(rows)
# Aggregate channel spend into KPI drivers per region-week.
pivot = (frame.groupby(["fecha", "region", "canal"])["inversion"]
         .sum().unstack("canal").reset_index())
pivot.columns.name = None
for channel in channels:
    pivot[f"inv_{channel}"] = pivot[channel].round(2)
    pivot = pivot.drop(columns=[channel])
pivot["leads"] = (pivot[[f"inv_{c}" for c in channels]].sum(axis=1)
                  * rng.uniform(0.02, 0.05, len(pivot))).round(0)
pivot["ventas"] = (pivot[[f"inv_{c}" for c in channels]].sum(axis=1)
                   * rng.uniform(2.5, 4.5, len(pivot))
                   * (1 + 0.1 * np.sin(np.arange(len(pivot)) / 6))).round(2)
pivot["ticket_medio"] = (pivot["ventas"] / pivot["leads"].replace(0, np.nan)).round(2)

OUT.parent.mkdir(parents=True, exist_ok=True)
pivot.to_csv(OUT, index=False, encoding="utf-8")
print(f"Escrito {OUT} · {len(pivot)} filas × {len(pivot.columns)} columnas")
print("Columnas:", list(pivot.columns))