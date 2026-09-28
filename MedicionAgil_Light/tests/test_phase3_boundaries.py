"""Guard the one-way dependency from desktop UI into services."""

import ast
from pathlib import Path


SERVICES = Path(__file__).resolve().parents[1] / "mmm_app" / "services"


def test_services_do_not_import_ui_or_tkinter():
    forbidden = {"app_desktop", "tkinter", "ui"}
    for path in SERVICES.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        for name in imports:
            assert name.split(".")[0] not in forbidden, (path.name, name)
