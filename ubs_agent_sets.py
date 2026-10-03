"""Edicion de los ficheros .set que alimentan cada backtest."""
from __future__ import annotations

import argparse
import json
import random
import shutil
from collections.abc import Iterable
from pathlib import Path

from run_tests import (
    TIMEFRAME_ENUM,
    apply_symbol_map,
    apply_symbol_suffix,
    load_set_params,
    normalize_set_symbol,
    parse_symbol_map,
)
from ubs_generate_sets import parse_numeric
from ubs.account import DEFAULT_BROKER, normalize_broker
from ubs.memory import AgentMemory
from ubs.models import Seed
from ubs.set_utils import read_set_with_encoding, write_set_text, write_set_use_every_tick
from ubs.universe import load_asset_universe
from ubs.weights import score_aware_percentile_multipliers
from ubs_agent_config import (
    CORE_MUTATION_KEYS,
    LEGACY_TIMEFRAME_TO_ENUM,
    TIMEFRAME_TO_ENUM,
    is_agent_mutable_key,
)
from ubs_agent_universe import (
    current_set_symbol,
)


def line_candidates(
    text: str,
    run_strategy: str,
    mutation_feedback: dict[str, float],
    *,
    excluded_keys: Iterable[str] = (),
) -> dict[str, tuple[int, list[str], float]]:
    lines = text.splitlines()
    preferred = set(CORE_MUTATION_KEYS.get(run_strategy, CORE_MUTATION_KEYS[""]))
    excluded = {str(key) for key in excluded_keys}
    candidates: dict[str, tuple[int, list[str], float]] = {}
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key in excluded:
            continue
        if not is_agent_mutable_key(key):
            continue
        if "||" not in raw_value:
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
        if step <= 0 or stop <= start:
            continue
        if not any(start <= current + direction * step <= stop for direction in (-2, -1, 1, 2)):
            continue
        base_weight = 4.0 if key in preferred else 1.0
        candidates[key] = (index, parts, base_weight)
    multipliers = score_aware_percentile_multipliers(mutation_feedback, candidates)
    for key, (index, parts, base_weight) in tuple(candidates.items()):
        candidates[key] = (index, parts, base_weight * multipliers[key])
    return candidates


def weighted_sample(items: dict[str, tuple[int, list[str], float]], count: int, rng: random.Random) -> list[str]:
    selected: list[str] = []
    pool = dict(items)
    for _ in range(min(count, len(pool))):
        total = sum(value[2] for value in pool.values())
        cursor = rng.random() * total
        upto = 0.0
        chosen = next(iter(pool))
        for key, (_, _, weight) in pool.items():
            upto += weight
            if upto >= cursor:
                chosen = key
                break
        selected.append(chosen)
        pool.pop(chosen, None)
    return selected


def replace_existing_plain_key(lines: list[str], key: str, value: str) -> bool:
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        lhs, _ = line.split("=", 1)
        if lhs.strip() == key:
            lines[index] = f"{lhs}={value}"
            return True
    return False


def replace_or_add_plain_key(lines: list[str], key: str, value: str) -> None:
    if replace_existing_plain_key(lines, key, value):
        return
    insert_at = 0
    while insert_at < len(lines) and (not lines[insert_at].strip() or lines[insert_at].lstrip().startswith(";")):
        insert_at += 1
    lines.insert(insert_at, f"{key}={value}")


def replace_existing_current_value(lines: list[str], key: str, value: str) -> bool:
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        lhs, raw_value = line.split("=", 1)
        if lhs.strip() != key:
            continue
        if "||" in raw_value:
            parts = raw_value.split("||")
            parts[0] = value
            lines[index] = f"{lhs}={'||'.join(parts)}"
        else:
            lines[index] = f"{lhs}={value}"
        return True
    return False


def replace_timeframe_keys(lines: list[str], run_strategy: str, target_period: str) -> list[str]:
    enum_value = TIMEFRAME_TO_ENUM.get(target_period.upper())
    if not enum_value:
        return []
    keys: list[str] = []
    if run_strategy == "1":
        keys.append("ST1_Timeframe")
    elif run_strategy == "2":
        keys.append("VolTimeframe")
    elif run_strategy == "3":
        keys.extend(["RNG_Timeframe", "RNG_ATR_Timeframe"])
    keys.extend(["Entry_Timing", "ATR_Timeframe"])
    changed: list[str] = []
    for key in dict.fromkeys(keys):
        if replace_existing_current_value(lines, key, enum_value):
            changed.append(key)
    return changed


def write_set_force_symbol(source: Path, destination: Path, symbol: str) -> bool:
    text, encoding = read_set_with_encoding(source)
    lines = text.splitlines()
    normalized_symbol = str(symbol or "").strip()
    changed = not replace_existing_current_value(lines, "ForceSymbol", normalized_symbol)
    if changed:
        replace_or_add_plain_key(lines, "ForceSymbol", normalized_symbol)
    write_set_text(destination, "\n".join(lines), encoding)
    return changed


def copy_seed_for_backtest(seed: Seed, destination: Path, symbol_map: dict[str, str], symbol_suffix: str = "") -> None:
    tester_symbol = apply_symbol_suffix(apply_symbol_map(seed.symbol, symbol_map), symbol_suffix).strip()
    if tester_symbol:
        write_set_force_symbol(seed.path, destination, tester_symbol)
    else:
        shutil.copy2(seed.path, destination)


def _set_param_active(params: dict[str, str], key: str) -> bool:
    value = str(params.get(key) or "").strip()
    return bool(value and value != "0")


def infer_missing_run_strategy_from_set(path: Path, params: dict[str, str]) -> str:
    current = str(params.get("Run_Strategy") or "").strip()
    if current in {"1", "2", "3"}:
        return current

    stem = path.stem.lower()
    has_st1 = "ST1_Timeframe" in params or "Entry_Timing" in params
    has_vol = "VolTimeframe" in params
    has_rng = "RNG_Timeframe" in params
    st1_active = _set_param_active(params, "ST1_Timeframe") or _set_param_active(params, "Entry_Timing")
    vol_active = _set_param_active(params, "VolTimeframe")
    rng_active = _set_param_active(params, "RNG_Timeframe")

    if rng_active and not st1_active and not vol_active:
        return "3"
    if st1_active and not vol_active and not rng_active:
        return "1"
    if vol_active and not st1_active and not rng_active:
        return "2"
    if any(token in stem for token in ("bitcoin_reaper", "scalp", "aggressive_sl")) and has_st1:
        return "1"
    if "volatility_breakout" in stem and has_vol:
        return "2"
    return ""


def replace_or_add_current_value(lines: list[str], key: str, value: str, default_value: str) -> bool:
    if replace_existing_current_value(lines, key, value):
        return True
    replace_or_add_plain_key(lines, key, default_value)
    return True


def _repair_strategy_timeframes(
    lines: list[str], params: dict[str, str], period: str, run_strategy: str | None,
    changes: list[str],
) -> None:
    enum_value = TIMEFRAME_TO_ENUM.get(str(period or "").strip().upper())
    if enum_value and run_strategy == "1" and str(params.get("ST1_Timeframe") or "").strip() in {"", "0"}:
        replace_or_add_current_value(
            lines,
            "ST1_Timeframe",
            enum_value,
            f"{enum_value}||0||0||49153||N",
        )
        changes.append("ST1_Timeframe")
    elif enum_value and run_strategy == "2" and str(params.get("VolTimeframe") or "").strip() in {"", "0"}:
        replace_or_add_current_value(
            lines,
            "VolTimeframe",
            enum_value,
            f"{enum_value}||0||0||49153||N",
        )
        changes.append("VolTimeframe")
    elif enum_value and run_strategy == "3" and str(params.get("RNG_Timeframe") or "").strip() in {"", "0"}:
        replace_or_add_current_value(
            lines,
            "RNG_Timeframe",
            enum_value,
            f"{enum_value}||0||0||49153||N",
        )
        changes.append("RNG_Timeframe")
    if enum_value and run_strategy == "3" and str(params.get("RNG_ATR_Timeframe") or "").strip() in {"", "0"}:
        replace_or_add_current_value(
            lines,
            "RNG_ATR_Timeframe",
            enum_value,
            f"{enum_value}||0||0||49153||N",
        )
        changes.append("RNG_ATR_Timeframe")


def _repair_legacy_timeframes(
    lines: list[str], params: dict[str, str], changes: list[str],
) -> None:
    valid_timeframe_values = set(TIMEFRAME_ENUM)
    for key in (
        "ST1_Timeframe",
        "VolTimeframe",
        "RNG_Timeframe",
        "RNG_ATR_Timeframe",
        "Entry_Timing",
        "ATR_Timeframe",
    ):
        raw = str(params.get(key) or "").strip()
        if raw in {"", "0"} or raw in valid_timeframe_values:
            continue
        replacement = LEGACY_TIMEFRAME_TO_ENUM.get(raw)
        if replacement:
            replace_existing_current_value(lines, key, replacement)
            changes.append(key)


def repair_seed_backtest_set(path: Path, symbol: str, period: str) -> dict[str, object]:
    text, encoding = read_set_with_encoding(path)
    lines = text.splitlines()
    params = load_set_params(path)
    changes: list[str] = []

    normalized_symbol = normalize_set_symbol(symbol)
    if normalized_symbol and normalize_set_symbol(params.get("ForceSymbol", "")) != normalized_symbol:
        if replace_existing_current_value(lines, "ForceSymbol", normalized_symbol):
            changes.append("ForceSymbol")
        else:
            replace_or_add_plain_key(lines, "ForceSymbol", normalized_symbol)
            changes.append("ForceSymbol")

    run_strategy = infer_missing_run_strategy_from_set(path, params)
    if run_strategy and str(params.get("Run_Strategy") or "").strip() != run_strategy:
        replace_or_add_current_value(
            lines, "Run_Strategy", run_strategy, f"{run_strategy}||1||0||2||N",
        )
        changes.append("Run_Strategy")

    _repair_strategy_timeframes(lines, params, period, run_strategy, changes)
    _repair_legacy_timeframes(lines, params, changes)
    if changes:
        write_set_text(path, "\n".join(lines), encoding)
    return {"changed": changes, "run_strategy": run_strategy}


def validate_seed_backtest_set(seed: Seed) -> list[str]:
    try:
        params = load_set_params(seed.path)
    except OSError as exc:
        return [f"no se pudo leer .set: {exc}"]
    if not params:
        return ["set vacio o no parseable"]

    issues: list[str] = []
    force_symbol = normalize_set_symbol(params.get("ForceSymbol", ""))
    expected_symbol = normalize_set_symbol(seed.symbol)
    strategy_keys = {
        "ST1_Timeframe",
        "VolTimeframe",
        "RNG_Timeframe",
        "RNG_ATR_Timeframe",
        "Entry_Timing",
        "ATR_Timeframe",
    }
    has_strategy_keys = any(key in params for key in strategy_keys)
    if has_strategy_keys and not force_symbol:
        issues.append("sin ForceSymbol")
    elif force_symbol and expected_symbol and force_symbol != expected_symbol:
        issues.append(f"ForceSymbol={force_symbol} != {expected_symbol}")

    run_strategy = str(params.get("Run_Strategy") or seed.run_strategy or "").strip()
    if has_strategy_keys and run_strategy not in {"1", "2", "3"}:
        issues.append("sin Run_Strategy valido")

    valid_timeframes = set(TIMEFRAME_ENUM)
    for key in (
        "ST1_Timeframe",
        "VolTimeframe",
        "RNG_Timeframe",
        "RNG_ATR_Timeframe",
        "Entry_Timing",
        "ATR_Timeframe",
    ):
        raw = str(params.get(key, "")).strip()
        if raw and raw != "0" and raw not in valid_timeframes:
            # El valor puede ser un timeframe MT5 real (p.ej. 16390 = H6) y aun
            # asi no estar soportado aqui: lo que se comprueba es TIMEFRAME_ENUM.
            issues.append(
                f"{key}={raw} fuera del universo soportado ({'/'.join(TIMEFRAME_ENUM.values())})"
            )
    return issues


def record_invalid_seed(memory: AgentMemory, seed: Seed, reasons: list[str]) -> None:
    memory.record_seed_score(seed, None, "invalid_seed", None)
    memory.conn.execute(
        "update seed_scores set metrics_json=? where seed_path=?",
        (json.dumps({"reasons": reasons}, ensure_ascii=False), str(seed.path)),
    )
    memory.conn.commit()


def write_retry_set(
    source: Path,
    destination: Path,
    enabled: bool,
    args: argparse.Namespace,
    target_symbol: str,
) -> str:
    """Create a stage copy and restore the broker's exact MT5 symbol spelling."""
    write_set_use_every_tick(source, destination, enabled)
    # Todos los brokers lo necesitan: el .ini es la unica fuente de la ortografia
    # MT5 y un ForceSymbol mal escrito cierra el terminal sin reporte. Solo
    # ICTrading es estricto; en el resto, un nombre que no se resuelve (por
    # ejemplo un alias) deja el set intacto en vez de abortar el reintento.
    strict = normalize_broker(getattr(args, "broker", DEFAULT_BROKER)) == "ICTRADING"
    assets = str(getattr(args, "assets", "") or "")
    groups, _ = load_asset_universe(Path(assets), include_disabled=True) if assets else ({}, {})
    actual_symbols = {
        str(symbol).strip()
        for group_symbols in groups.values()
        for symbol in group_symbols
        if str(symbol).strip()
    }
    if not actual_symbols:
        # Sin inventario no hay contra que resolver: es preferible dejar el set
        # como esta que abortar la etapa por un fichero ausente o ilegible.
        return target_symbol
    mapped = apply_symbol_map(target_symbol, parse_symbol_map(getattr(args, "symbol_map", "") or ""))
    matches = [symbol for symbol in actual_symbols if symbol.casefold() == mapped.strip().casefold()]
    if len(matches) != 1:
        if strict:
            raise ValueError(
                "No se puede resolver un nombre MT5 único en el universo del broker: " + str(mapped)
            )
        return target_symbol
    exact = matches[0]
    text, encoding = read_set_with_encoding(destination)
    lines = text.splitlines()
    # Reescribir es normalizar finales de linea, y eso rompe la comparacion
    # byte a byte con la que Final Tick decide si puede reutilizar los OHLC ya
    # ejecutados. Con la ortografia ya correcta -el caso normal- no se toca.
    if current_set_symbol(lines) == exact:
        return exact
    if not replace_existing_current_value(lines, "ForceSymbol", exact):
        replace_or_add_plain_key(lines, "ForceSymbol", exact)
    write_set_text(destination, "\n".join(lines), encoding)
    return exact
