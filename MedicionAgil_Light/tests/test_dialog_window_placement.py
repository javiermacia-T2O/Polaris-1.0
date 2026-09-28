import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIALOGS = (
    "causal_impact_dialog.py",
    "analysis_config_dialog.py",
    "export_dialog.py",
    "preflight_dialog.py",
    "merge_dialog.py",
)


def _dialog_tree(filename):
    source = (PROJECT_ROOT / "mmm_app" / "ui" / "dialogs" / filename)
    return ast.parse(source.read_text(encoding="utf-8"))


def test_target_dialogs_use_shared_monitor_aware_placement():
    for filename in DIALOGS:
        tree = _dialog_tree(filename)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        assert any(
            isinstance(call.func, ast.Name)
            and call.func.id == "center_popup"
            for call in calls
        ), filename


def test_target_dialog_messageboxes_are_parented_to_the_dialog():
    for filename in DIALOGS:
        tree = _dialog_tree(filename)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        for call in calls:
            if (isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "messagebox"):
                assert any(
                    keyword.arg == "parent"
                    and isinstance(keyword.value, ast.Name)
                    and keyword.value.id == "self"
                    for keyword in call.keywords
                ), f"{filename}:{call.lineno}"


def test_file_dialogs_keep_the_application_as_parent():
    paths = [PROJECT_ROOT / "mmm_app" / "app_desktop.py",
             PROJECT_ROOT / "mmm_app" / "ui" / "dialogs" / "export_dialog.py"]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            if not (isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "filedialog"):
                continue
            assert any(key.arg == "parent" for key in call.keywords), (
                f"{path.name}:{call.lineno}")
