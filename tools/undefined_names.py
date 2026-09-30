"""Detecta nombres globales sueltos y prohíbe nuevas importaciones estrella."""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

from tools.source_files import ROOT, iter_python_files

ALLOWED_STAR_IMPORTS: frozenset[str] = frozenset()
ALWAYS_DEFINED = frozenset(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__all__",
}


def _argument_names(args: ast.arguments | None) -> set[str]:
    if args is None:
        return set()
    names = {arg.arg for kind in ("posonlyargs", "args", "kwonlyargs")
             for arg in getattr(args, kind, None) or []}
    return names | {arg.arg for arg in (args.vararg, args.kwarg) if arg}


def _bound_by(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name} | _argument_names(getattr(node, "args", None))
    if isinstance(node, ast.Lambda):
        return _argument_names(node.args)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return {node.id}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {(alias.asname or alias.name).split(".")[0] for alias in node.names}
    if isinstance(node, ast.ExceptHandler) and node.name:
        return {node.name}
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return set(node.names)
    return set()


def _annotations(tree: ast.AST) -> list[ast.AST]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for kind in ("posonlyargs", "args", "kwonlyargs"):
                found.extend(arg.annotation for arg in getattr(args, kind, [])
                             if arg.annotation)
            found.extend(arg.annotation for arg in (args.vararg, args.kwarg)
                         if arg and arg.annotation)
            found.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            found.append(node.annotation)
    return [node for node in found if node is not None]


def unresolved(source: str) -> list[str] | None:
    tree = ast.parse(source)
    if any(isinstance(node, ast.ImportFrom)
           and any(alias.name == "*" for alias in node.names)
           for node in ast.walk(tree)):
        return None
    defined = set(ALWAYS_DEFINED)
    for node in ast.walk(tree):
        defined |= _bound_by(node)
    deferred = any(isinstance(node, ast.ImportFrom) and node.module == "__future__"
                   and any(alias.name == "annotations" for alias in node.names)
                   for node in ast.walk(tree))
    skipped = {id(item) for annotation in _annotations(tree)
               for item in ast.walk(annotation)} if deferred else set()
    return sorted({node.id for node in ast.walk(tree)
                   if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                   and node.id not in defined and id(node) not in skipped})


def relative_path(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def main(argv: list[str]) -> int:
    paths = [Path(name) for name in argv] or list(iter_python_files())
    found = 0
    star_paths = set()
    for path in paths:
        missing = unresolved(path.read_text(encoding="utf-8"))
        if missing is None:
            star_paths.add(relative_path(path))
        elif missing:
            found += len(missing)
            print(f"{path}: {', '.join(missing)}")
    unexpected = star_paths - set(ALLOWED_STAR_IMPORTS)
    stale = set(ALLOWED_STAR_IMPORTS) - star_paths if not argv else set()
    for name in sorted(unexpected):
        print(f"{name}: `import *` no autorizado")
    for name in sorted(stale):
        print(f"{name}: excepción de `import *` obsoleta")
    print(f"{len(paths)} ficheros, {found} nombres sueltos, "
          f"{len(star_paths)} `import *`")
    return 1 if found or unexpected or stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
