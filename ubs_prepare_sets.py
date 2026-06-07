from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

from run_tests import infer_period_from_set, infer_symbol_from_set, load_set_params
from ubs.set_utils import (
    file_sha256,
    force_fixed_lot_text,
    read_set_with_encoding,
    safe_part,
    write_set_text,
)


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = Path(
    r"C:\Users\Adrian\Adrian\TRADING\UBS_bot+settings\UBS_bot+settings\UBS_SETS_ORDERED\UBS_Sets"
)
DEFAULT_OUTPUT = BASE_DIR / "sets" / "ubs_ready"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copia los .set de UBS al proyecto, organizados y con lotaje fijo 0.01."
    )
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE), help="Carpeta original UBS_Sets.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT), help="Carpeta destino organizada.")
    parser.add_argument("--reset", action="store_true", help="Borra la carpeta destino antes de copiar.")
    return parser.parse_args()


def unique_target(base_dir: Path, relative_source: Path, symbol: str, period: str) -> Path:
    family = safe_part(relative_source.parts[0] if relative_source.parts else "UBS")
    name_parts = [safe_part(part) for part in relative_source.with_suffix("").parts[1:]]
    if not name_parts:
        name_parts = [safe_part(relative_source.stem)]
    filename = "__".join(name_parts) + ".set"
    target_dir = base_dir / safe_part(symbol) / safe_part(period) / family
    target = target_dir / filename

    if not target.exists():
        return target

    stem = target.stem
    counter = 2
    while True:
        candidate = target.with_name(f"{stem}_{counter}.set")
        if not candidate.exists():
            return candidate
        counter += 1


def prepare_sets(source_dir: Path, output_dir: Path, reset: bool) -> int:
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(f"No existe la carpeta fuente UBS: {source_dir}")

    if reset and output_dir.exists():
        resolved_output = output_dir.resolve()
        resolved_base = BASE_DIR.resolve()
        if resolved_output == resolved_base or resolved_base not in resolved_output.parents:
            raise ValueError(f"Destino peligroso para --reset: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output_dir / "_manifest.csv"
    rows: list[dict[str, str]] = []
    missing_count = 0

    set_files = sorted(path for path in source_dir.rglob("*.set") if path.is_file())
    for index, source in enumerate(set_files, start=1):
        relative = source.relative_to(source_dir)
        params = load_set_params(source)
        symbol = infer_symbol_from_set(source, params) or "UNKNOWN"
        period = infer_period_from_set(source, params) or "UNKNOWN"
        target = unique_target(output_dir, relative, symbol, period)

        text, encoding = read_set_with_encoding(source)
        normalized, found, missing = force_fixed_lot_text(text)
        if missing:
            missing_count += 1
        write_set_text(target, normalized, encoding)

        rows.append(
            {
                "index": str(index),
                "source_path": str(source),
                "target_path": str(target),
                "source_family": relative.parts[0] if relative.parts else "",
                "symbol": symbol,
                "period": period,
                "run_strategy": params.get("Run_Strategy", ""),
                "fixed_lot_keys_found": ";".join(sorted(found)),
                "fixed_lot_keys_missing": ";".join(sorted(missing)),
                "source_sha256": file_sha256(source),
            }
        )

    with manifest_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()) if rows else ["index"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Fuente UBS: {source_dir}")
    print(f"Destino preparado: {output_dir}")
    print(f"Sets copiados: {len(rows)}")
    print(f"Manifest: {manifest_path}")
    if missing_count:
        print(f"Sets con keys de lotaje faltantes registradas: {missing_count}")
    return len(rows)


def main() -> int:
    args = parse_args()
    try:
        total = prepare_sets(Path(args.source_dir).expanduser(), Path(args.output_dir).expanduser(), args.reset)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
