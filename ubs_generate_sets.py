from __future__ import annotations

import argparse
import configparser
import csv
import random
import re
import sys
from datetime import datetime
from pathlib import Path

from ubs_set_utils import compact_safe_part, force_fixed_lot_text, read_set_with_encoding, write_set_text


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = BASE_DIR / "sets" / "ubs_ready"
DEFAULT_OUTPUT = BASE_DIR / "outputs" / "ubs_generations"
DEFAULT_TEMPLATE = BASE_DIR / "tester_template.ini"

FROZEN_EXACT = {
    "AdjustLotsizeToVariableValues",
    "Risk",
    "StartLots",
    "Lic_key",
    "URL",
    "LICURL",
    "LICURLB",
    "Sets_Folder",
    "ForceSymbol",
    "UseAutoLoader",
    "UseCommonFolder",
    "EA_MagicNumber",
    "ST1_MagicNumber",
    "ST2_MagicNumber",
    "EA_Comment",
    "ST1_Comment",
    "ST2_Comment",
}
FROZEN_PREFIXES = (
    "Grid",
    "PropFirm",
    "CloseAt",
    "Broker_GMT",
    "AutoGMT",
    "UseMQL5Calendar",
    "NFP_",
    "News",
    "Manual",
    "MaxRisk",
    "HistoricalMaxDD",
    "Lots",
)
MUTABLE_PREFIXES = (
    "ST1_",
    "Vol",
    "Exit_",
    "ATR",
    "Atr",
)
MUTABLE_EXACT = {
    "Entry_Timing",
    "Exit_Timing",
    "UseEveryTick",
    "DevFactor",
    "minSize",
    "MaxTrades",
    "MinDist_orders",
    "SpreadFilter",
    "MaxSpread",
    "DistForSpreadFilter",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genera variantes .set para UBS desde sets preparados.")
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE), help="Carpeta con seeds .set.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT), help="Carpeta base de salida.")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="tester_template.ini usado como contexto.")
    parser.add_argument("--generations", type=int, default=1, help="Cantidad de generaciones a crear.")
    parser.add_argument("--variants-per-seed", type=int, default=3, help="Variantes por seed en cada generacion.")
    parser.add_argument("--max-seeds", type=int, default=50, help="Maximo de seeds por generacion. 0 usa todos.")
    parser.add_argument("--mutations-per-variant", type=int, default=8, help="Parametros a mutar por variante.")
    parser.add_argument("--random-seed", type=int, help="Semilla aleatoria opcional para reproducibilidad.")
    return parser.parse_args()


def parse_template_context(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8-sig")
    if not parser.has_section("Tester"):
        return {}
    keys = ["Symbol", "Period", "Model", "FromDate", "ToDate", "Deposit", "Currency", "Leverage"]
    return {key: parser["Tester"].get(key, "") for key in keys}


def is_mutable_key(key: str) -> bool:
    if key in FROZEN_EXACT:
        return False
    if any(key.startswith(prefix) for prefix in FROZEN_PREFIXES):
        return False
    return key in MUTABLE_EXACT or any(key.startswith(prefix) for prefix in MUTABLE_PREFIXES)


def parse_numeric(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def format_like(original: str, value: float) -> str:
    if re.fullmatch(r"-?\d+", original.strip()):
        return str(int(round(value)))
    decimals = 0
    if "." in original:
        decimals = min(8, max(1, len(original.rsplit(".", 1)[1])))
    return f"{value:.{decimals}f}" if decimals else f"{value:g}"


def candidate_keys(lines: list[str]) -> dict[str, tuple[int, list[str]]]:
    candidates: dict[str, tuple[int, list[str]]] = {}
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not is_mutable_key(key) or "||" not in raw_value:
            continue
        parts = raw_value.split("||")
        if len(parts) < 5:
            continue
        current = parse_numeric(parts[0])
        start = parse_numeric(parts[1])
        step = parse_numeric(parts[2])
        stop = parse_numeric(parts[3])
        if current is None or start is None or step is None or stop is None:
            continue
        if step <= 0 or stop < start:
            continue
        if start == stop:
            continue
        candidates[key] = (index, parts)
    return candidates


def mutate_text(text: str, rng: random.Random, mutations_per_variant: int) -> tuple[str, list[str], set[str]]:
    lines = text.splitlines()
    candidates = candidate_keys(lines)
    if not candidates:
        normalized, _, missing = force_fixed_lot_text(text)
        return normalized, [], missing

    keys = list(candidates)
    rng.shuffle(keys)
    selected = keys[: min(mutations_per_variant, len(keys))]
    changed: list[str] = []
    for key in selected:
        line_index, parts = candidates[key]
        current = float(parts[0])
        start = float(parts[1])
        step = float(parts[2])
        stop = float(parts[3])
        direction = rng.choice([-2, -1, 1, 2])
        value = current + direction * step
        if value < start or value > stop:
            slots = int((stop - start) / step)
            value = start + rng.randint(0, max(0, slots)) * step
        value = max(start, min(stop, value))
        parts[0] = format_like(parts[0], value)
        line = lines[line_index]
        lhs = line.split("=", 1)[0]
        lines[line_index] = f"{lhs}={'||'.join(parts)}"
        changed.append(key)

    normalized, _, missing = force_fixed_lot_text("\n".join(lines))
    return normalized, changed, missing


def choose_seeds(files: list[Path], max_seeds: int, rng: random.Random) -> list[Path]:
    if max_seeds <= 0 or len(files) <= max_seeds:
        return files
    return sorted(rng.sample(files, max_seeds))


def generate_sets(
    source_dir: Path,
    output_dir: Path,
    template_path: Path,
    generations: int,
    variants_per_seed: int,
    max_seeds: int,
    mutations_per_variant: int,
    random_seed: int | None,
) -> int:
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de seeds UBS: {source_dir}")
    if generations <= 0:
        raise ValueError("--generations debe ser mayor que 0")
    if variants_per_seed <= 0:
        raise ValueError("--variants-per-seed debe ser mayor que 0")

    rng = random.Random(random_seed)
    run_dir = output_dir / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = run_dir / "_manifest.csv"
    account_context = parse_template_context(template_path)

    seed_files = sorted(path for path in source_dir.rglob("*.set") if path.is_file())
    current_generation = choose_seeds(seed_files, max_seeds, rng)
    rows: list[dict[str, str]] = []

    for generation in range(1, generations + 1):
        if not current_generation:
            break
        generation_dir = run_dir / f"gen_{generation:03d}"
        next_generation: list[Path] = []
        seeds = choose_seeds(current_generation, max_seeds, rng)
        print(f"Generacion {generation}: seeds={len(seeds)} variants_per_seed={variants_per_seed}")

        for seed_index, seed in enumerate(seeds, start=1):
            text, encoding = read_set_with_encoding(seed)
            parent_name = compact_safe_part(seed.stem, 32)
            for variant_index in range(1, variants_per_seed + 1):
                mutated, changed, missing_lot_keys = mutate_text(text, rng, mutations_per_variant)
                output = generation_dir / f"{parent_name}__g{generation:03d}_s{seed_index:03d}_v{variant_index:03d}.set"
                write_set_text(output, mutated, encoding)
                next_generation.append(output)
                rows.append(
                    {
                        "generation": str(generation),
                        "seed": str(seed),
                        "output": str(output),
                        "mutated_keys": ";".join(changed),
                        "fixed_lot_keys_missing": ";".join(sorted(missing_lot_keys)),
                        "template_symbol": account_context.get("Symbol", ""),
                        "template_period": account_context.get("Period", ""),
                        "template_model": account_context.get("Model", ""),
                        "template_from": account_context.get("FromDate", ""),
                        "template_to": account_context.get("ToDate", ""),
                        "template_deposit": account_context.get("Deposit", ""),
                        "template_currency": account_context.get("Currency", ""),
                        "template_leverage": account_context.get("Leverage", ""),
                    }
                )
        current_generation = next_generation

    with manifest_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()) if rows else ["generation"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Salida generaciones: {run_dir}")
    print(f"Sets generados: {len(rows)}")
    print(f"Manifest: {manifest_path}")
    return len(rows)


def main() -> int:
    args = parse_args()
    try:
        total = generate_sets(
            Path(args.source_dir).expanduser(),
            Path(args.output_dir).expanduser(),
            Path(args.template).expanduser(),
            args.generations,
            args.variants_per_seed,
            args.max_seeds,
            args.mutations_per_variant,
            args.random_seed,
        )
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
