"""Writable application location in source and frozen Windows builds."""

import sys
from pathlib import Path


def writable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]
