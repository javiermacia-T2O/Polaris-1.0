"""Bounded JSON serialization helpers for IPC."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd


def frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Serialize a bounded frame without leaking NaN/Infinity into JSON."""
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def scalar_value(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)
