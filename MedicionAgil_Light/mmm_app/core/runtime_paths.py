"""Writable application location in source and frozen Windows builds."""

import sys
import os
from pathlib import Path


def writable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def user_cache_root() -> Path:
    """Return an OS-appropriate writable cache, never the install folder."""
    override = os.environ.get("POLARIS_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Polaris" / "Cache"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "polaris"
