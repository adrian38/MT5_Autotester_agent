"""Aplica las reglas de generacion inmutables a los .set ya existentes.

La regla vive en `ubs/set_rules.py` y la aplica sola cada camino que crea un
.set. Este script es la otra mitad: pasa esa misma regla por el historico que
se genero antes de que existiera, con copia de seguridad y sin tocar nada que
ya cumpla.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ubs.set_rules import NFP_RULE_KEYS, apply_nfp_rules_text
from ubs.set_utils import read_set_with_encoding, write_set_text


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ROOTS = ("sets", "live_sets", "sets_1.7", "MQL5/Profiles/Tester", "outputs")
BACKUP_ROOT = BASE_DIR / "outputs" / "backups"
# Las copias de seguridad son el estado anterior a la regla: barrerlas las
# destruiria como copia.
EXCLUDED_DIRS = (BACKUP_ROOT,)


@dataclass
class RuleSweepTally:
    """Recuento de un barrido, tanto en simulacion como en firme."""

    scanned: int = 0
    rewritten: int = 0
    unknown_strategy: int = 0
    without_nfp_block: int = 0
    by_key: dict[str, int] = field(default_factory=dict)
    by_root: dict[str, int] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.scanned} .set revisados, {self.rewritten} con la regla aplicada, "
            f"{self.unknown_strategy} sin estrategia deducible, "
            f"{self.without_nfp_block} sin bloque NFP, {len(self.failed)} con error"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fija las claves de regla (filtro NFP) en todos los .set del proyecto."
    )
    parser.add_argument(
        "--root", action="append", default=None,
        help=f"Carpeta a barrer; repetible. Por defecto: {', '.join(DEFAULT_ROOTS)}.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo informa; no escribe ningun .set.")
    parser.add_argument("--no-backup", action="store_true", help="No guarda copia previa de los .set reescritos.")
    parser.add_argument("--backup-dir", default="", help="Carpeta de copias; por defecto outputs/backups/set_rules_<fecha>.")
    parser.add_argument("--report", default="", help="Ruta del informe JSON; por defecto junto a la copia.")
    parser.add_argument("--progress-every", type=int, default=2000, help="Cada cuantos ficheros informa del avance.")
    return parser.parse_args()


def resolve_roots(raw_roots: list[str] | None) -> list[Path]:
    roots = [Path(root) for root in (raw_roots or list(DEFAULT_ROOTS))]
    resolved: list[Path] = []
    for root in roots:
        path = root if root.is_absolute() else BASE_DIR / root
        if path.is_dir():
            resolved.append(path)
    return resolved


def is_excluded(path: Path, backup_dir: Path | None) -> bool:
    excluded = [*EXCLUDED_DIRS, backup_dir] if backup_dir else list(EXCLUDED_DIRS)
    for directory in excluded:
        try:
            path.relative_to(directory)
        except ValueError:
            continue
        return True
    return False


def collect_set_files(roots: list[Path], backup_dir: Path | None) -> list[tuple[Path, Path]]:
    """Pares (raiz, fichero) de todos los .set a revisar, sin duplicados."""
    seen: set[Path] = set()
    found: list[tuple[Path, Path]] = []
    for root in roots:
        for path in sorted(root.rglob("*.set")):
            if not path.is_file() or is_excluded(path, backup_dir):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append((root, path))
    return found


def backup_file(path: Path, backup_dir: Path) -> None:
    try:
        relative = path.resolve().relative_to(BASE_DIR)
    except ValueError:
        relative = Path(path.name)
    target = backup_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def process_set_file(
    root: Path, path: Path, tally: RuleSweepTally, backup_dir: Path | None, dry_run: bool,
) -> None:
    try:
        text, encoding = read_set_with_encoding(path)
    except OSError as exc:
        tally.failed.append(f"{path}: {exc}")
        return
    result = apply_nfp_rules_text(text)
    tally.scanned += 1
    if not result.run_strategy:
        tally.unknown_strategy += 1
    if len(result.absent) == len(NFP_RULE_KEYS):
        tally.without_nfp_block += 1
    if not result.changed:
        return
    if not dry_run:
        try:
            if backup_dir is not None:
                backup_file(path, backup_dir)
            write_set_text(path, result.text, encoding)
        except OSError as exc:
            tally.failed.append(f"{path}: {exc}")
            return
    tally.rewritten += 1
    tally.by_root[root.name] = tally.by_root.get(root.name, 0) + 1
    for key in result.changed:
        tally.by_key[key] = tally.by_key.get(key, 0) + 1


def sweep(
    files: list[tuple[Path, Path]], backup_dir: Path | None, dry_run: bool, progress_every: int,
) -> RuleSweepTally:
    tally = RuleSweepTally()
    total = len(files)
    for index, (root, path) in enumerate(files, start=1):
        process_set_file(root, path, tally, backup_dir, dry_run)
        if progress_every > 0 and index % progress_every == 0:
            print(f"Revisados {index}/{total} .set — aplicados {tally.rewritten}", flush=True)
    return tally


def write_report(report_path: Path, tally: RuleSweepTally, roots: list[Path], dry_run: bool) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "dry_run": dry_run,
                "roots": [str(root) for root in roots],
                "scanned": tally.scanned,
                "rewritten": tally.rewritten,
                "unknown_strategy": tally.unknown_strategy,
                "without_nfp_block": tally.without_nfp_block,
                "by_key": tally.by_key,
                "by_root": tally.by_root,
                "failed": tally.failed,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    roots = resolve_roots(args.root)
    if not roots:
        print("ERROR: ninguna de las carpetas indicadas existe")
        return 1
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir: Path | None = None
    if not args.dry_run and not args.no_backup:
        backup_dir = Path(args.backup_dir).expanduser() if args.backup_dir else BACKUP_ROOT / f"set_rules_{stamp}"
    files = collect_set_files(roots, backup_dir)
    print(f"Carpetas: {', '.join(str(root) for root in roots)}")
    print(f"Sets en cola: {len(files)}" + (" (simulacion)" if args.dry_run else ""))
    tally = sweep(files, backup_dir, args.dry_run, args.progress_every)
    report_path = Path(args.report).expanduser() if args.report else BACKUP_ROOT / f"set_rules_{stamp}.json"
    write_report(report_path, tally, roots, args.dry_run)
    print(tally.summary())
    for key, count in sorted(tally.by_key.items()):
        print(f"  {key}: {count}")
    if backup_dir is not None and tally.rewritten:
        print(f"Copia previa: {backup_dir}")
    print(f"Informe: {report_path}")
    for failure in tally.failed[:20]:
        print(f"ERROR {failure}")
    return 1 if tally.failed else 0


if __name__ == "__main__":
    sys.exit(main())
