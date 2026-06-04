from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Seed:
    path: Path
    symbol: str
    period: str
    family: str
    run_strategy: str


@dataclass(frozen=True)
class Variant:
    path: Path
    seed: Seed
    target_symbol: str
    target_period: str
    mutated_keys: tuple[str, ...]
    missing_lot_keys: tuple[str, ...]
    policy: str
