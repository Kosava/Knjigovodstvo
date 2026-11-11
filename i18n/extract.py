"""Pronalazi sve tekstove za prevod u kodu programa.

Ključ je svaki string koji je prvi argument poziva ``tr(...)`` ili ``tr_noop(...)``,
kao i naslov kolone ``Column("...")``. Koristi ga test prevoda, a može se pokrenuti i
ručno da se vidi šta fali u nekom jeziku:

    python -m i18n.extract en
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("core", "gui", "settings")
SOURCE_FILES = ("main.py",)
KEY_FUNCTIONS = {"tr", "tr_noop", "Column"}


def source_files() -> list[Path]:
    files = [ROOT / f for f in SOURCE_FILES]
    for folder in SOURCE_DIRS:
        files.extend(sorted((ROOT / folder).rglob("*.py")))
    return files


def extract(files: list[Path] | None = None) -> dict[str, list[str]]:
    """Ključ -> lista mesta (fajl:red) gde se koristi."""
    keys: dict[str, list[str]] = {}
    for path in files or source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in KEY_FUNCTIONS:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.setdefault(arg.value, []).append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return keys


def dynamic_tr_calls(files: list[Path] | None = None) -> list[str]:
    """Pozivi ``tr(x)`` sa promenljivom (dozvoljeno samo za vrednosti označene sa tr_noop)."""
    found = []
    for path in files or source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr"
                    and node.args and not isinstance(node.args[0], ast.Constant)):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


if __name__ == "__main__":
    import importlib

    keys = extract()
    if len(sys.argv) > 1:
        table = importlib.import_module(f"i18n.{sys.argv[1]}").TRANSLATIONS
        missing = [k for k in keys if k not in table]
        unused = [k for k in table if k not in keys]
        print(f"ključeva: {len(keys)}, nedostaje: {len(missing)}, višak: {len(unused)}")
        for k in missing:
            print("NEDOSTAJE:", repr(k))
        for k in unused:
            print("VIŠAK:", repr(k))
    else:
        for k, where in sorted(keys.items()):
            print(repr(k), "  #", where[0])
