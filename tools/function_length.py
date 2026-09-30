"""Mide el techo de 60 líneas y sus excepciones heredadas congeladas."""
from __future__ import annotations

import ast
from pathlib import Path

from tools import source_files
from tools.source_files import ROOT, iter_python_files

BASELINE_PATH = ROOT / "tests" / "function_length_baseline.json"
MAX_LINES = 60


def _walk_functions(tree: ast.AST, prefix: str = ""):
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}{node.name}"
            yield node, qualname
            yield from _walk_functions(node, f"{qualname}.")
        elif isinstance(node, ast.ClassDef):
            yield from _walk_functions(node, f"{prefix}{node.name}.")
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            yield from _walk_functions(node, prefix)


def function_lengths(root: Path | None = None) -> dict[str, int]:
    base = root or ROOT
    found: dict[str, int] = {}
    for path in iter_python_files(base):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(base).as_posix()
        for node, qualname in _walk_functions(tree):
            start = min([node.lineno] + [item.lineno for item in node.decorator_list])
            found[f"{relative}::{qualname}"] = node.end_lineno - start + 1
    return found


def load_baseline() -> dict[str, int]:
    return source_files.load_baseline(BASELINE_PATH)


def main() -> int:
    sizes = function_lengths()
    over = {key: size for key, size in sizes.items() if size > MAX_LINES}
    print(f"{len(over)}/{len(sizes)} funciones por encima de {MAX_LINES} líneas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
