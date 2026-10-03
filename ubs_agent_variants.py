"""Creacion de variantes y lanzamiento de sus backtests."""
from __future__ import annotations

import argparse
import random
import subprocess
import sys
import time
from pathlib import Path

from ubs_generate_sets import format_like
from ubs.models import Seed, Variant
from ubs.set_utils import (
    compact_safe_part,
    force_fixed_lot_text,
    read_set_with_encoding,
    safe_part,
    set_use_every_tick_text,
    write_set_text,
)
from ubs.weights import MUTATION_SCORE_FULL_STRENGTH
from ubs_agent_config import (
    BASE_DIR,
    diag_log,
    load_global_params,
    load_mutation_overrides,
)
from ubs_agent_sets import (
    line_candidates,
    replace_existing_current_value,
    replace_or_add_plain_key,
    replace_timeframe_keys,
    weighted_sample,
)


def _apply_frozen_overrides(lines: list[str]) -> None:
    frozen_ov, _ = load_mutation_overrides()
    if not frozen_ov:
        return
    global_params = load_global_params()
    for fkey in frozen_ov:
        fvalue = global_params.get(fkey, frozen_ov.get(fkey, ""))
        if fvalue:
            replace_existing_current_value(lines, fkey, fvalue)


def _mutation_direction(
    current: float, start: float, step: float, stop: float,
    direction_bias: float, rng: random.Random,
) -> tuple[int, float]:
    valid = [direction for direction in (-2, -1, 1, 2) if start <= current + direction * step <= stop]
    up = [direction for direction in valid if direction > 0]
    down = [direction for direction in valid if direction < 0]
    strength = min(1.0, abs(float(direction_bias)) / MUTATION_SCORE_FULL_STRENGTH)
    if direction_bias and up and down:
        preferred = up if direction_bias > 0 else down
        alternative = down if direction_bias > 0 else up
        probability = 0.5 + 0.25 * strength
        return rng.choice(preferred if rng.random() < probability else alternative), strength
    return rng.choice(valid), strength


def _mutate_selected(
    lines: list[str], candidates: dict, selected: list[str],
    direction_feedback: dict[str, float], rng: random.Random,
) -> tuple[list[str], list[dict[str, object]]]:
    changed: list[str] = []
    details: list[dict[str, object]] = []
    for key in selected:
        line_index, parts, _ = candidates[key]
        current, start, step, stop = (float(parts[index]) for index in range(4))
        direction_bias = direction_feedback.get(key, 0.0)
        direction, bias_strength = _mutation_direction(
            current, start, step, stop, direction_bias, rng,
        )
        new_value = max(start, min(stop, current + direction * step))
        parts[0] = format_like(parts[0], new_value)
        lhs = lines[line_index].split("=", 1)[0]
        lines[line_index] = f"{lhs}={'||'.join(parts)}"
        changed.append(key)
        details.append(
            {
                "key": key, "old": current, "new": new_value,
                "delta": new_value - current, "step": step, "direction": direction,
                "direction_bias": round(float(direction_bias), 4),
                "direction_bias_strength": round(bias_strength, 4), "wrapped": False,
            }
        )
    return changed, details


def _variant_target(
    seed: Seed, target_symbol: str, target_period: str, output_dir: Path,
    generation: int, seed_index: int, variant_index: int,
) -> Path:
    seed_label = compact_safe_part(seed.path.stem, 24)
    family_label = compact_safe_part(seed.family, 24)
    filename = (
        f"{safe_part(target_symbol)}_{safe_part(target_period)}_{family_label}_{seed_label}_"
        f"g{generation:03d}_s{seed_index:03d}_v{variant_index:03d}.set"
    )
    return output_dir / safe_part(target_symbol) / safe_part(target_period) / filename


def create_variant(
    seed: Seed,
    target_symbol: str,
    target_period: str,
    output_dir: Path,
    generation: int,
    seed_index: int,
    variant_index: int,
    mutations_per_variant: int,
    mutation_feedback: dict[str, float],
    mutation_direction_feedback: dict[str, float],
    policy: str,
    rng: random.Random,
) -> Variant:
    text, encoding = read_set_with_encoding(seed.path)
    lines = text.splitlines()
    replace_or_add_plain_key(lines, "ForceSymbol", target_symbol)
    timeframe_keys = replace_timeframe_keys(lines, seed.run_strategy, target_period)
    # Apply user-defined frozen override values from the global params config
    _apply_frozen_overrides(lines)
    text = "\n".join(lines)
    candidates = line_candidates(text, seed.run_strategy, mutation_feedback, excluded_keys=timeframe_keys)
    selected = weighted_sample(candidates, mutations_per_variant, rng)
    lines = text.splitlines()
    changed, mutation_details = _mutate_selected(
        lines, candidates, selected, mutation_direction_feedback, rng,
    )

    normalized, _, missing = force_fixed_lot_text("\n".join(lines))
    normalized = set_use_every_tick_text(normalized, False)
    target = _variant_target(
        seed, target_symbol, target_period, output_dir,
        generation, seed_index, variant_index,
    )
    write_set_text(target, normalized, encoding)
    return Variant(
        target,
        seed,
        target_symbol,
        target_period,
        tuple(changed),
        tuple(sorted(missing)),
        policy,
        tuple(timeframe_keys),
        tuple(mutation_details),
    )


def create_history_probe_variant(
    seed: Seed,
    target_symbol: str,
    target_period: str,
    output_dir: Path,
    index: int,
) -> Variant:
    text, encoding = read_set_with_encoding(seed.path)
    lines = text.splitlines()
    replace_or_add_plain_key(lines, "ForceSymbol", target_symbol)
    timeframe_keys = replace_timeframe_keys(lines, seed.run_strategy, target_period)
    normalized, _, missing = force_fixed_lot_text("\n".join(lines))
    normalized = set_use_every_tick_text(normalized, False)
    filename = f"{safe_part(target_symbol)}_{safe_part(target_period)}_history_probe_{index:04d}.set"
    target = output_dir / safe_part(target_symbol) / safe_part(target_period) / filename
    write_set_text(target, normalized, encoding)
    return Variant(
        target,
        seed,
        target_symbol,
        target_period,
        (),
        tuple(sorted(missing)),
        "history_probe",
        tuple(timeframe_keys),
        (),
    )


def _backtest_command(
    args: argparse.Namespace,
    set_dir: Path,
    *,
    model: str = "",
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    command = [
        sys.executable,
        str(BASE_DIR / "run_tests.py"),
        "--template",
        str(Path(args.template).expanduser()),
        "--set-dir",
        str(set_dir),
        "--recursive",
        "--infer-tester-from-set",
        "--prefer-set-path-timeframe",
        "--delay",
        str(args.delay),
    ]
    if args.expert:
        command.extend(["--expert", args.expert])
    if args.mt5_path:
        command.extend(["--mt5-path", args.mt5_path])
    if args.data_dir:
        command.extend(["--data-dir", args.data_dir])
    if args.multi_terminal:
        command.append("--multi-terminal")
        command.extend(["--max-workers", str(args.max_workers)])
        if args.terminals_config:
            command.extend(["--terminals-config", args.terminals_config])
    run_tests_symbol_map = getattr(args, "run_tests_symbol_map", args.symbol_map)
    if run_tests_symbol_map:
        command.extend(["--symbol-map", run_tests_symbol_map])
    if getattr(args, "symbol_suffix", "").strip():
        command.extend(["--symbol-suffix", args.symbol_suffix.strip()])
    if getattr(args, "symbol_futures_suffix", "").strip():
        command.extend(["--symbol-futures-suffix", args.symbol_futures_suffix.strip()])
    if getattr(args, "symbol_shares_suffix", "").strip():
        command.extend(["--symbol-shares-suffix", args.symbol_shares_suffix.strip()])
    if getattr(args, "assets", "").strip():
        command.extend(["--symbol-universe", str(Path(args.assets).expanduser())])
    # ``--symbol-universe`` ya es la señal de que el broker ofrece el simbolo:
    # run_tests.py omite ahi los .set cuyo Symbol no este en el universo, sin
    # mirar la politica de deshabilitados (que incluye simbolos vigentes).
    if args.dry_run:
        command.append("--dry-run")
    effective_from_date = getattr(args, "from_date", "") if from_date is None else from_date
    effective_to_date = getattr(args, "to_date", "") if to_date is None else to_date
    if effective_from_date:
        command.extend(["--from-date", str(effective_from_date)])
    if effective_to_date:
        command.extend(["--to-date", str(effective_to_date)])
    if model:
        command.extend(["--model", str(model)])
    return command


def run_backtests(
    args: argparse.Namespace,
    set_dir: Path,
    *,
    model: str = "",
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    if not args.expert and not args.multi_terminal:
        print("AVISO: --expert no indicado; se omiten backtests.")
        return 0
    command = _backtest_command(
        args, set_dir, model=model, from_date=from_date, to_date=to_date
    )
    print("Ejecutando:", " ".join(f'"{part}"' if " " in part else part for part in command))
    diag_log(f"RUN_TESTS_START set_dir={set_dir} model={model or '(template)'} command={' '.join(command)}")
    started = time.time()
    process = subprocess.run(command, cwd=BASE_DIR, text=True)
    diag_log(
        f"RUN_TESTS_END set_dir={set_dir} model={model or '(template)'} "
        f"returncode={process.returncode} elapsed={time.time() - started:.1f}s"
    )
    return process.returncode
