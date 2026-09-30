"""Mide el techo de 600 líneas y sus excepciones heredadas congeladas."""
from __future__ import annotations

from pathlib import Path

from tools import source_files
from tools.source_files import ROOT, iter_python_files

BASELINE_PATH = ROOT / "tests" / "file_length_baseline.json"
MAX_LINES = 600


def file_lengths(root: Path | None = None) -> dict[str, int]:
    base = root or ROOT
    return {
        path.relative_to(base).as_posix(): len(
            path.read_text(encoding="utf-8").splitlines()
        )
        for path in iter_python_files(base)
    }


def load_baseline() -> dict[str, int]:
    return source_files.load_baseline(BASELINE_PATH)


def main() -> int:
    sizes = file_lengths()
    over = {key: size for key, size in sizes.items() if size > MAX_LINES}
    excess = sum(size - MAX_LINES for size in over.values())
    print(f"{len(over)}/{len(sizes)} ficheros por encima de {MAX_LINES} líneas")
    print(f"exceso heredado total: {excess} líneas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
