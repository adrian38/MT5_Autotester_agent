from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from run_tests import infer_period_from_set, infer_symbol_from_set, load_set_params
from ubs.models import Seed
from ubs.set_utils import compact_safe_part, safe_part


def load_seeds(source_dir: Path, *, base_dir: Path) -> list[Seed]:
    manifest = source_dir / "_manifest.csv"
    seeds: list[Seed] = []
    seen_paths: set[str] = set()
    if manifest.exists():
        with manifest.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                path = Path(row["target_path"]).expanduser()
                if not path.exists() and not path.is_absolute():
                    path = (base_dir / path).resolve()
                if path.exists():
                    seen_paths.add(str(path.resolve()))
                    seeds.append(
                        Seed(
                            path=path,
                            symbol=row.get("symbol") or "UNKNOWN",
                            period=row.get("period") or "UNKNOWN",
                            family=row.get("source_family") or path.parent.name,
                            run_strategy=row.get("run_strategy") or "",
                        )
                    )

    for path in sorted(source_dir.rglob("*.set")):
        path_key = str(path.resolve())
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        seeds.append(seed_from_path(path))
    return seeds


def seed_from_path(path: Path) -> Seed:
    params = load_set_params(path)
    symbol = infer_symbol_from_set(path, params) or "UNKNOWN"
    period = infer_period_from_set(path, params) or "UNKNOWN"
    return Seed(
        path=path,
        symbol=symbol,
        period=period,
        family=path.parent.name,
        run_strategy=params.get("Run_Strategy", "").strip(),
    )


def seed_eval_filename(index: int, seed: Seed, used: set[str]) -> str:
    symbol = "" if seed.symbol == "UNKNOWN" else safe_part(seed.symbol)
    period = "" if seed.period == "UNKNOWN" else safe_part(seed.period)
    prefix = "_".join(part for part in (symbol, period) if part)
    label = compact_safe_part(seed.path.stem, 40)
    stem = f"seed_{index:04d}_{prefix}_{label}" if prefix else f"seed_{index:04d}_{label}"
    name = f"{stem}.set"
    suffix = 2
    while name.lower() in used:
        name = f"{stem}_{suffix}.set"
        suffix += 1
    used.add(name.lower())
    return name


def file_digest(path: Path) -> str | None:
    try:
        digest = hashlib.sha1()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None
