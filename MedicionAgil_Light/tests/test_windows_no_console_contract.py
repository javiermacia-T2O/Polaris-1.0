"""Source-level guardrails for the Windows portable distribution.

These checks are intentionally dependency-free. They prevent a future release
from reintroducing the alarming command window that used to appear behind the
splash while the Tauri host and the Python sidecar started.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_tauri_host_uses_the_windows_gui_subsystem():
    assert 'windows_subsystem = "windows"' in _read("src-tauri/src/main.rs")


def test_every_sidecar_path_uses_create_no_window():
    source = _read("src-tauri/src/lib.rs")
    assert "const CREATE_NO_WINDOW: u32 = 0x0800_0000" in source
    assert source.count("creation_flags(CREATE_NO_WINDOW)") == 4


def test_portable_package_has_no_command_shell_launcher():
    release = _read("scripts/build_portable_release.ps1")
    assert "Iniciar.cmd" not in release
    assert 'Haz doble clic en "$PackageName.exe"' in release
    assert "Iniciar.cmd" not in _read("MedicionAgil_Light/build_windows.ps1")
    assert not (ROOT / "MedicionAgil_Light" / "Iniciar.cmd").exists()
