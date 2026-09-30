"""Generate deterministic, streaming Phase-3 benchmark data."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path
import random
import shutil


def generate(path: Path, target_bytes: int, seed: int) -> tuple[int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(path.parent).free < target_bytes * 2:
        raise OSError("Se requiere al menos el doble del tamaño objetivo libre")
    rng = random.Random(seed)
    rows = 0
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["row", "day", "region", "channel", "campaign",
                         *[f"metric_{i}" for i in range(24)], "note"])
        while stream.tell() < target_bytes:
            batch = []
            for _ in range(10_000):
                rows += 1
                day = date(2018, 1, 1) + timedelta(days=rows % 2922)
                region = "" if rows % 97 == 0 else f"R{rows % 24:02d}"
                note = None if rows % 113 == 0 else (
                    "(vacío)" if rows % 127 == 0 else
                    f"texto irregular | {rng.randrange(100000):05d}")
                batch.append([rows, day.isoformat(), region,
                              f"C{rows % 8}", f"K{rows % 100000}",
                              *[(rows * (i + 3)) % 1_000_003 for i in range(24)],
                              note])
            writer.writerows(batch)
    return path.stat().st_size, rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--gb", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=20260930)
    args = parser.parse_args()
    size, count = generate(args.output, int(args.gb * 1024**3), args.seed)
    print({"path": str(args.output), "bytes": size, "rows": count,
           "seed": args.seed})
