"""Descubre scripts de análisis sin importar sus dependencias pesadas."""

import ast
import importlib.util
from pathlib import Path
import threading
from types import ModuleType

ANALYSES_DIR = Path(__file__).resolve().parent.parent / "analyses"


class _LazyAnalysisModule:
    """Proxy compatible con los consumidores antiguos de ``module``."""

    def __init__(self, file: Path):
        self.file = file
        self._module = None
        self._lock = threading.Lock()

    def load(self) -> ModuleType:
        if self._module is None:
            with self._lock:
                if self._module is None:
                    self._module = _load_module(self.file)
        return self._module

    def __getattr__(self, name):
        return getattr(self.load(), name)


def discover_analyses() -> list[dict]:
    """Devuelve el catálogo leyendo solo metadatos estáticos del código."""
    analyses: list[dict] = []
    if not ANALYSES_DIR.exists():
        return analyses

    for file in sorted(ANALYSES_DIR.glob("*.py")):
        if file.name.startswith("_"):
            continue

        metadata = _read_metadata(file)
        if metadata is None:
            continue

        lazy_module = _LazyAnalysisModule(file)
        analyses.append({
            "name": metadata.get("NAME", file.stem),
            "description": metadata.get("DESCRIPTION", ""),
            "category": metadata.get("CATEGORY", "General"),
            "custom_dialog": metadata.get("CUSTOM_DIALOG"),
            "module": lazy_module,
            "file": file.name,
            "path": file,
        })
    return analyses


def _read_metadata(file: Path) -> dict | None:
    """Lee constantes literales y confirma que exista ``run`` a nivel módulo."""
    try:
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    except (OSError, SyntaxError, UnicodeError) as exc:
        print(f"[WARN] No se pudo catalogar {file.name}: {exc}")
        return None

    wanted = {"NAME", "DESCRIPTION", "CATEGORY", "CUSTOM_DIALOG"}
    metadata = {}
    has_run = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            has_run = has_run or node.name == "run"
            continue
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            if not isinstance(target, ast.Name) or target.id not in wanted:
                continue
            try:
                metadata[target.id] = ast.literal_eval(value)
            except (ValueError, TypeError):
                pass

    return metadata if has_run else None


def read_table_format(analysis: dict) -> dict | None:
    """Extrae ``get_table_format`` sin importar el módulo (evita cargar jax/geox)."""
    file = Path(analysis.get("path") or ANALYSES_DIR / analysis["file"])
    try:
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    except (OSError, SyntaxError, UnicodeError):
        return None

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "get_table_format":
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Return) and stmt.value is not None:
                try:
                    value = ast.literal_eval(stmt.value)
                except (ValueError, TypeError):
                    return None
                return value if isinstance(value, dict) else None
    return None


def load_analysis(analysis: dict) -> ModuleType:
    """Carga una entrada del catálogo una sola vez, al usarla."""
    cached = analysis.get("module")
    if isinstance(cached, _LazyAnalysisModule):
        module = cached.load()
        analysis["module"] = module
        return module
    if cached is not None:
        return cached

    file = Path(analysis.get("path") or ANALYSES_DIR / analysis["file"])
    module = _load_module(file)
    analysis["module"] = module
    return module


def is_analysis_loaded(analysis: dict) -> bool:
    """Indica si acceder al módulo ya no requiere ejecutar el plugin."""
    module = analysis.get("module")
    return module is not None and not isinstance(module, _LazyAnalysisModule)


def _load_module(file: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(file.stem, file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No se pudo preparar el análisis '{file.name}'")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise RuntimeError(
            f"No se pudo cargar el análisis '{file.name}': {exc}"
        ) from exc
    if not callable(getattr(module, "run", None)):
        raise RuntimeError(f"El análisis '{file.name}' no define run()")
    return module
