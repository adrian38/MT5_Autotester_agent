"""Alcance común y trinquete de las guardas de código Python propio."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNED_ROOTS = (
    "manager_node_runtime", "parsers", "portfolio_manager", "tests", "tools",
    "ubs", "ui",
)
SKIP_PARTS = {
    ".git", "__pycache__", ".venv", "build", "build_installer", "configs",
    "dist", "dist_installer", "logs", "outputs", "reports", "runtime", "tmp",
}


def iter_python_files(root: Path | None = None):
    base = root or ROOT
    paths = list(base.glob("*.py"))
    for owned in OWNED_ROOTS:
        directory = base / owned
        if directory.exists():
            paths.extend(directory.rglob("*.py"))
    for path in sorted(set(paths)):
        if not any(part in SKIP_PARTS for part in path.relative_to(base).parts):
            yield path


def load_baseline(path: Path) -> dict[str, int]:
    return json.loads(path.read_text(encoding="utf-8"))["grandfathered"]


def ratchet_offenders(
    sizes: dict[str, int], baseline: dict[str, int], limit: int,
) -> dict[str, dict]:
    return {
        "new": {key: size for key, size in sizes.items()
                if size > limit and key not in baseline},
        "grown": {key: (baseline[key], sizes[key]) for key in baseline
                  if key in sizes and sizes[key] > baseline[key]},
        "stale": {key: baseline[key] for key in baseline
                  if sizes.get(key, 0) <= limit},
    }
