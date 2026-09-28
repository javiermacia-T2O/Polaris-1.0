from pathlib import Path
from core.runtime_paths import writable_root


def test_writable_root_uses_executable_directory_when_frozen(monkeypatch, tmp_path):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "AnalisisMedicion.exe"))
    assert writable_root() == tmp_path.resolve()
