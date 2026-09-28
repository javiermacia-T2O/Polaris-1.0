"""Aplicación principal — CLI."""

import sys
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.loader import load_file, list_available_files, SUPPORTED_EXTENSIONS
from core.plugin_loader import discover_analyses
from core.atomic import atomic_output

DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"


def pick_file() -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    files = list_available_files(DATA_DIR)

    if files:
        print("\nArchivos en ./data:")
        for i, f in enumerate(files, 1):
            print(f"  {i}. {f.name}")
        print("  0. Introducir ruta manual")
        choice = input("Elige número: ").strip()

        if choice == "0":
            return Path(input("Ruta del archivo: ").strip().strip('"'))
        if choice.isdigit() and 1 <= int(choice) <= len(files):
            return files[int(choice) - 1]

    return Path(input("Ruta del archivo: ").strip().strip('"'))


def pick_analysis(analyses: list[dict]) -> dict:
    print("\nAnálisis disponibles:")
    by_cat: dict[str, list[tuple[int, dict]]] = {}
    for i, a in enumerate(analyses, 1):
        by_cat.setdefault(a["category"], []).append((i, a))

    for cat, items in by_cat.items():
        print(f"\n  [{cat}]")
        for i, a in items:
            print(f"    {i}. {a['name']}  —  {a['description']}")

    idx = int(input("\nElige número: ").strip()) - 1
    return analyses[idx]


def save_result(result: dict, base_name: str, analysis_name: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in analysis_name)
    out_path = OUTPUT_DIR / f"{base_name}__{safe}.txt"

    with atomic_output(out_path) as temporary:
        with temporary.open("w", encoding="utf-8") as f:
            f.write(f"Análisis: {analysis_name}\n")
            f.write("=" * 60 + "\n")
            for k, v in result.items():
                f.write(f"\n### {k}\n")
                if isinstance(v, pd.DataFrame):
                    f.write(v.to_string())
                elif isinstance(v, pd.Series):
                    f.write(v.to_string())
                else:
                    f.write(str(v))
                f.write("\n")
    return out_path


def main():
    print("=" * 60)
    print("   Analizador modular — MMM")
    print("=" * 60)

    try:
        path = pick_file()
        df = load_file(path)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        return

    print(f"\nCargado: {path.name}  →  {df.shape[0]} filas × {df.shape[1]} columnas")

    analyses = discover_analyses()
    if not analyses:
        print("\nNo hay análisis en ./analyses/")
        return

    chosen = pick_analysis(analyses)
    print(f"\nEjecutando: {chosen['name']} ...")

    try:
        result = chosen["module"].run(df)
    except Exception as e:
        print(f"\n[ERROR] durante la ejecución: {e}")
        return

    out_file = save_result(result, path.stem, chosen["name"])
    print(f"\nResultado guardado en: {out_file}")

    # Muestra por consola las primeras secciones
    print("\n" + "=" * 60)
    for i, (k, v) in enumerate(result.items()):
        print(f"\n--- {k} ---")
        if isinstance(v, (pd.DataFrame, pd.Series)):
            print(v.head(15).to_string())
        else:
            print(v)
        if i >= 2:
            print("\n[...] Ver archivo completo para el resto.")
            break


if __name__ == "__main__":
    main()
