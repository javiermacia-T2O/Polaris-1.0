"""Reproducible bounded-navigation benchmark for CSV/Parquet sources."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "MedicionAgil_Light" / "mmm_app"),
                str(ROOT / "MedicionAgil_Light" / "python")]
from services.active_dataset import ActiveDataset


def timed(call, repeats=3):
    values = []
    for _ in range(repeats):
        started = time.perf_counter(); call(); values.append(time.perf_counter() - started)
    return {"series_s": values, "median_s": statistics.median(values)}


def main(path: Path, repeats: int):
    dataset = ActiveDataset.open_file(path)
    report = {"path": str(path), "bytes": path.stat().st_size,
              "backend": dataset.backend, "repeats": repeats}
    report["first_page"] = timed(lambda: dataset.page(limit=100), repeats)
    report["next_page"] = timed(lambda: dataset.page(limit=100, offset=100), repeats)
    report["deep_page"] = timed(lambda: dataset.page(limit=100, offset=1_000_000), repeats)
    report["distinct"] = timed(lambda: dataset.search_distinct_values(
        dataset.columns[2], limit=100), repeats)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(); main(args.path, args.repeats)
