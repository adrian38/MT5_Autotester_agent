"""Detecta nombres sueltos por ámbito y prohíbe nuevas importaciones estrella.

El recuento es por ámbito, no por fichero: un local de otra función no cuenta
como definido. Mirarlo plano dejaba pasar justo lo que rompe un refactor que
parte una función en pasos —el paso extraído sigue leyendo el nombre que se
quedó en el original— porque el nombre sí existe en alguna parte del fichero.
"""
from __future__ import annotations

import ast
import builtins
import symtable
import sys
from pathlib import Path

from tools.source_files import ROOT, iter_python_files

ALLOWED_STAR_IMPORTS: frozenset[str] = frozenset()
ALWAYS_DEFINED = frozenset(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__package__", "__spec__", "__all__",
    "__builtins__", "__loader__", "__debug__", "__annotations__", "__class__",
    "__qualname__", "__module__", "WindowsError",
    # ámbitos internos que CPython 3.14 crea para las anotaciones diferidas
    "__classdict__", "__conditional_annotations__",
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


def _bound_in(table: symtable.SymbolTable) -> set[str]:
    return {symbol.get_name() for symbol in table.get_symbols()
            if symbol.is_assigned() or symbol.is_parameter()
            or symbol.is_imported() or symbol.is_declared_global()}


def _walk_scopes(table: symtable.SymbolTable, enclosing: set[str],
                 missing: set[str]) -> None:
    visible = enclosing | _bound_in(table)
    for symbol in table.get_symbols():
        name = symbol.get_name()
        if symbol.is_referenced() and name not in visible:
            missing.add(name)
    # El cuerpo de una clase no es ámbito de sus métodos: lo que se define ahí
    # se lee por el objeto, no por nombre suelto.
    inherited = enclosing if table.get_type() == "class" else visible
    for child in table.get_children():
        _walk_scopes(child, inherited, missing)


def unresolved(source: str, filename: str = "<fuente>") -> list[str] | None:
    tree = ast.parse(source)
    if any(isinstance(node, ast.ImportFrom)
           and any(alias.name == "*" for alias in node.names)
           for node in ast.walk(tree)):
        return None
    missing: set[str] = set()
    _walk_scopes(symtable.symtable(source, filename, "exec"),
                 set(ALWAYS_DEFINED), missing)
    return sorted(missing)


def relative_path(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def main(argv: list[str]) -> int:
    paths = [Path(name) for name in argv] or list(iter_python_files())
    found = 0
    star_paths = set()
    for path in paths:
        missing = unresolved(path.read_text(encoding="utf-8"), str(path))
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
