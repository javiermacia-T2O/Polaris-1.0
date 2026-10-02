"""Contract tests for the lightweight, portable analysis plug-in registry.

These tests deliberately use temporary plug-ins and patch the registry's
directory.  They document the startup contract without importing any real
analysis (which keeps the portable launcher fast and dependency tolerant).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def loader(tmp_path, monkeypatch):
    from mmm_app.core import plugin_loader

    monkeypatch.setattr(plugin_loader, "ANALYSES_DIR", tmp_path)
    return plugin_loader


def _plugin(path: Path, body: str = "") -> None:
    path.write_text(
        "NAME = 'Demo'\nDESCRIPTION = 'Fast demo'\nCATEGORY = 'Basic'\n"
        "LOADS = globals().get('LOADS', 0) + 1\n"
        "def run(*args, **kwargs):\n    return {'ok': True}\n"
        f"{body}\n",
        encoding="utf-8",
    )


def test_discovery_is_lazy_and_does_not_import_plugins(loader, tmp_path, monkeypatch):
    plugin = tmp_path / "demo.py"
    marker = tmp_path / "plugin_executed.txt"
    _plugin(plugin, f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')")
    sentinel = "demo"
    sys.modules.pop(sentinel, None)

    entries = loader.discover_analyses()

    assert len(entries) == 1
    entry = entries[0]
    assert entry["name"] == "Demo"
    assert entry["description"] == "Fast demo"
    assert entry["category"] == "Basic"
    assert entry["file"] == "demo.py"
    # The catalog may expose a proxy object, but importing/executing the
    # plugin itself must be deferred until ``load_analysis``.
    assert sentinel not in sys.modules
    assert not marker.exists()
    loader.load_analysis(entry)
    assert marker.read_text() == "yes"


def test_load_analysis_imports_once_and_caches_module(loader, tmp_path):
    plugin = tmp_path / "demo.py"
    _plugin(plugin)
    entry = loader.discover_analyses()[0]

    first = loader.load_analysis(entry)
    second = loader.load_analysis(entry)

    assert first is second
    assert first.LOADS == 1
    assert entry["module"] is first


def test_plugin_import_error_is_recoverable(loader, tmp_path):
    good = tmp_path / "good.py"
    bad = tmp_path / "bad.py"
    _plugin(good)
    _plugin(bad, "raise RuntimeError('optional dependency missing')")

    entries = loader.discover_analyses()
    assert {entry["file"] for entry in entries} == {"bad.py", "good.py"}

    with pytest.raises(RuntimeError, match="optional dependency missing"):
        loader.load_analysis(next(e for e in entries if e["file"] == "bad.py"))

    recovered = loader.load_analysis(next(e for e in entries if e["file"] == "good.py"))
    assert recovered.NAME == "Demo"
