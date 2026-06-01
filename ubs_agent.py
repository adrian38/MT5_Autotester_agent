from __future__ import annotations

import argparse
import configparser
import csv
import json
import random
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from run_tests import TIMEFRAME_ENUM, infer_period_from_set, infer_symbol_from_set, load_set_params
from ubs_generate_sets import format_like, parse_numeric
from ubs_score import ScoreConfig, ScoreResult, score_report_file
from ubs_set_utils import force_fixed_lot_text, read_set_with_encoding, safe_part, write_set_text


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = BASE_DIR / "sets" / "ubs_ready"
DEFAULT_OUTPUT = BASE_DIR / "outputs" / "ubs_agent"
DEFAULT_MEMORY = BASE_DIR / "outputs" / "ubs_memory.sqlite"
DEFAULT_TEMPLATE = BASE_DIR / "tester_template.ini"
DEFAULT_ASSETS = BASE_DIR / "assets" / "roboforex_assets.ini"
DEFAULT_SYMBOL_MAP = "XTIUSD=WTI,USTEC=.USTECHCash,US100=.USTECHCash,US30=.US30Cash,US500=.US500Cash,DAX=.DE40Cash"
TIMEFRAME_TO_ENUM = {period: value for value, period in TIMEFRAME_ENUM.items()}
TIMEFRAME_UNIVERSE = ("M15", "M30", "H1", "H4", "D1")

CORE_MUTATION_KEYS = {
    "1": (
        "Exit_stop",
        "Exit_limit",
        "Exit_BE_start",
        "Exit_BE_extra_pips",
        "Exit_TrailSL_size",
        "Exit_TrailSL_Start",
        "Exit_TrailSL_step",
        "ST1_MinDist_to_HL",
        "ST1_countback",
        "ST1_HL_strength_L",
        "ST1_HL_strength_R",
        "MinDist_orders",
        "ST1_Expiration_hours",
    ),
    "2": (
        "Exit_stop",
        "Exit_limit",
        "Exit_BE_start",
        "Exit_BE_extra_pips",
        "Exit_TrailSL_size",
        "Exit_TrailSL_Start",
        "Exit_TrailSL_step",
        "VolCandles",
        "minSize",
        "AtrPeriod",
        "DevFactor",
        "VolMaxTrades",
    ),
    "": (
        "Exit_stop",
        "Exit_limit",
        "Exit_BE_start",
        "Exit_BE_extra_pips",
        "Exit_TrailSL_size",
        "Exit_TrailSL_Start",
        "Exit_TrailSL_step",
    ),
}
FROZEN_KEYS = {
    "PrintLogs",
    "PrintSetLoadingInfo",
    "ShowInfoPanel",
    "UpdateInfoTesting",
    "InfoPanelSizeAdjust",
    "SetFontSize",
    "EP",
    "RF",
    "TR",
    "MTR",
    "AdjustLotsizeToVariableValues",
    "Risk",
    "StartLots",
    "Lic_key",
    "URL",
    "LICURL",
    "LICURLB",
    "Sets_Folder",
    "UseAutoLoader",
    "UseCommonFolder",
    "Run_Strategy",
    "EA_MagicNumber",
    "ST1_MagicNumber",
    "ST2_MagicNumber",
    "EA_Comment",
    "ST1_Comment",
    "ST2_Comment",
}
FROZEN_PREFIXES = ("Grid", "PropFirm", "CloseAt", "Broker_GMT", "AutoGMT", "UseMQL5Calendar", "NFP_", "Manual", "MaxRisk")
ALLOWED_MUTATION_PREFIXES = ("ST1_", "Vol", "Exit_")
ALLOWED_MUTATION_KEYS = {
    "SpreadFilter",
    "MaxSpread",
    "DistForSpreadFilter",
    "MinDist_orders",
    "MaxTrades",
    "DevFactor",
    "AtrPeriod",
    "minSize",
    "ATRDefault",
    "ATR_Period",
    "DefaultValue",
}


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


class AgentMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        self.conn.close()

    def _init(self) -> None:
        self.conn.executescript(
            """
            create table if not exists runs (
                id integer primary key autoincrement,
                created_at text not null,
                source_dir text not null,
                output_dir text not null,
                generations integer not null,
                variants_per_seed integer not null,
                max_seeds integer not null,
                execute_backtests integer not null,
                dry_run integer not null,
                hidden integer not null default 0
            );
            create table if not exists candidates (
                id integer primary key autoincrement,
                run_id integer not null,
                generation integer not null,
                seed_path text not null,
                set_path text not null,
                symbol text not null,
                target_symbol text not null,
                period text not null,
                family text not null,
                run_strategy text not null,
                mutated_keys text not null,
                missing_lot_keys text not null,
                policy text not null,
                report_path text,
                score real,
                accepted integer,
                metrics_json text,
                status text not null,
                created_at text not null
            );
            """
        )
        self._ensure_column("runs", "hidden", "integer not null default 0")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in self.conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            self.conn.execute(f"alter table {table} add column {column} {definition}")

    def create_run(
        self,
        source_dir: Path,
        output_dir: Path,
        generations: int,
        variants_per_seed: int,
        max_seeds: int,
        execute_backtests: bool,
        dry_run: bool,
    ) -> int:
        cur = self.conn.execute(
            """
            insert into runs (
                created_at, source_dir, output_dir, generations, variants_per_seed,
                max_seeds, execute_backtests, dry_run
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                str(source_dir),
                str(output_dir),
                generations,
                variants_per_seed,
                max_seeds,
                int(execute_backtests),
                int(dry_run),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_variant(self, run_id: int, generation: int, variant: Variant, status: str = "generated") -> None:
        self.conn.execute(
            """
            insert into candidates (
                run_id, generation, seed_path, set_path, symbol, target_symbol, period,
                family, run_strategy, mutated_keys, missing_lot_keys, policy, status, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                generation,
                str(variant.seed.path),
                str(variant.path),
                variant.seed.symbol,
                variant.target_symbol,
                variant.target_period,
                variant.seed.family,
                variant.seed.run_strategy,
                ";".join(variant.mutated_keys),
                ";".join(variant.missing_lot_keys),
                variant.policy,
                status,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

    def record_score(self, set_path: Path, result: ScoreResult | None, status: str, report_path: Path | None = None) -> None:
        self.conn.execute(
            """
            update candidates
            set report_path=?, score=?, accepted=?, metrics_json=?, status=?
            where set_path=?
            """,
            (
                str(report_path) if report_path else (result.report_path if result else None),
                result.score if result else None,
                int(result.accepted) if result else None,
                result.to_json() if result else None,
                status,
                str(set_path),
            ),
        )
        self.conn.commit()

    def mutation_feedback(self) -> dict[str, float]:
        rows = self.conn.execute(
            """
            select mutated_keys, score, accepted
            from candidates
            where score is not null and mutated_keys != ''
            """
        ).fetchall()
        totals: dict[str, list[float]] = {}
        for row in rows:
            bonus = float(row["score"]) + (15.0 if row["accepted"] else 0.0)
            for key in str(row["mutated_keys"]).split(";"):
                if key:
                    totals.setdefault(key, []).append(bonus)
        return {key: sum(values) / len(values) for key, values in totals.items()}

    def asset_feedback(self) -> dict[str, float]:
        rows = self.conn.execute(
            """
            select target_symbol, score, accepted
            from candidates
            where score is not null
            """
        ).fetchall()
        totals: dict[str, list[float]] = {}
        for row in rows:
            value = float(row["score"]) + (20.0 if row["accepted"] else 0.0)
            totals.setdefault(str(row["target_symbol"]).upper(), []).append(value)
        return {symbol: sum(values) / len(values) for symbol, values in totals.items()}

    def timeframe_feedback(self) -> dict[str, float]:
        rows = self.conn.execute(
            """
            select period, score, accepted
            from candidates
            where score is not null
            """
        ).fetchall()
        totals: dict[str, list[float]] = {}
        for row in rows:
            value = float(row["score"]) + (15.0 if row["accepted"] else 0.0)
            totals.setdefault(str(row["period"]).upper(), []).append(value)
        return {period: sum(values) / len(values) for period, values in totals.items()}

    def continuation_seeds(self, limit: int = 0) -> tuple[int, int, list[Seed]]:
        run = self.conn.execute("select id from runs order by id desc limit 1").fetchone()
        if run is None:
            return 0, 0, []
        run_id = int(run["id"])
        generation = self.conn.execute(
            "select max(generation) as generation from candidates where run_id=?",
            (run_id,),
        ).fetchone()
        latest_generation = int(generation["generation"] or 0)
        if latest_generation <= 0:
            return run_id, 0, []
        rows = self.conn.execute(
            """
            select *
            from candidates
            where run_id=? and generation=?
            order by
                case
                    when status = 'accepted' then 0
                    when score is not null then 1
                    else 2
                end,
                score desc,
                id desc
            """,
            (run_id, latest_generation),
        ).fetchall()

        seeds: list[Seed] = []
        seen: set[str] = set()
        for row in rows:
            path = Path(row["set_path"])
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen or not path.exists():
                continue
            seen.add(key)
            seeds.append(
                Seed(
                    path=path,
                    symbol=(row["target_symbol"] or row["symbol"] or "UNKNOWN").upper(),
                    period=row["period"] or "UNKNOWN",
                    family=row["family"] or path.parent.name,
                    run_strategy=row["run_strategy"] or "",
                )
            )
            if limit > 0 and len(seeds) >= limit:
                break
        return run_id, latest_generation, seeds

    def latest_run(self) -> sqlite3.Row | None:
        return self.conn.execute("select * from runs order by id desc limit 1").fetchone()

    def max_generation(self, run_id: int) -> int:
        row = self.conn.execute(
            "select max(generation) as generation from candidates where run_id=?",
            (run_id,),
        ).fetchone()
        return int(row["generation"] or 0)

    def pending_generated_generation(self, run_id: int) -> int:
        row = self.conn.execute(
            """
            select min(generation) as generation
            from candidates
            where run_id=? and status='generated'
            """,
            (run_id,),
        ).fetchone()
        return int(row["generation"] or 0)

    def variants_for_generation(self, run_id: int, generation: int, *, status: str | None = None) -> list[Variant]:
        if status:
            rows = self.conn.execute(
                "select * from candidates where run_id=? and generation=? and status=? order by id",
                (run_id, generation, status),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "select * from candidates where run_id=? and generation=? order by id",
                (run_id, generation),
            ).fetchall()
        return [variant_from_candidate_row(row) for row in rows if Path(row["set_path"]).exists()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agente UBS con seleccion de assets, mutacion guiada y memoria.")
    score_defaults = ScoreConfig()
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--memory", default=str(DEFAULT_MEMORY))
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--assets", default=str(DEFAULT_ASSETS))
    parser.add_argument("--expert", help="Ruta .ex5 UBS para ejecutar backtests.")
    parser.add_argument("--mt5-path", help="Ruta terminal64.exe.")
    parser.add_argument("--data-dir", help="Carpeta de datos MT5.")
    parser.add_argument("--symbol-map", default=DEFAULT_SYMBOL_MAP)
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--variants-per-seed", type=int, default=3)
    parser.add_argument("--max-seeds", type=int, default=30)
    parser.add_argument("--mutations-per-variant", type=int, default=6)
    parser.add_argument("--top-percent", type=float, default=20.0)
    parser.add_argument("--continue-last-run", action="store_true", help="Usa la ultima generacion registrada como seeds.")
    parser.add_argument("--min-net-profit", type=float, default=score_defaults.min_net_profit)
    parser.add_argument("--min-profit-factor", type=float, default=score_defaults.min_profit_factor)
    parser.add_argument("--min-trades", type=int, default=score_defaults.min_trades)
    parser.add_argument("--max-drawdown-pct", type=float, default=score_defaults.max_drawdown_pct)
    parser.add_argument("--min-recovery-factor", type=float, default=score_defaults.min_recovery_factor)
    parser.add_argument("--min-positive-month-ratio", type=float, default=score_defaults.min_positive_month_ratio)
    parser.add_argument("--delay", type=int, default=1)
    parser.add_argument("--execute-backtests", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="No abre MT5; pasa --dry-run a run_tests.")
    parser.add_argument("--random-seed", type=int)
    return parser.parse_args()


def load_asset_universe(path: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8-sig")
    groups: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}
    for section in parser.sections():
        if section == "CommonAliases":
            aliases = {key.upper(): value for key, value in parser[section].items()}
            continue
        symbols = [item.strip().upper() for item in parser[section].get("symbols", "").split(",") if item.strip()]
        groups[section] = symbols
    return groups, aliases


def load_seeds(source_dir: Path) -> list[Seed]:
    manifest = source_dir / "_manifest.csv"
    seeds: list[Seed] = []
    if manifest.exists():
        with manifest.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                path = Path(row["target_path"])
                if path.exists():
                    seeds.append(
                        Seed(
                            path=path,
                            symbol=(row.get("symbol") or "UNKNOWN").upper(),
                            period=row.get("period") or "UNKNOWN",
                            family=row.get("source_family") or path.parent.name,
                            run_strategy=row.get("run_strategy") or "",
                        )
                    )
        return seeds

    for path in sorted(source_dir.rglob("*.set")):
        params = load_set_params(path)
        seeds.append(
            Seed(
                path=path,
                symbol=(infer_symbol_from_set(path, params) or "UNKNOWN").upper(),
                period=infer_period_from_set(path, params) or "UNKNOWN",
                family=path.parent.name,
                run_strategy=params.get("Run_Strategy", ""),
            )
        )
    return seeds


def variant_from_candidate_row(row: sqlite3.Row) -> Variant:
    seed = Seed(
        path=Path(row["seed_path"]),
        symbol=(row["symbol"] or "UNKNOWN").upper(),
        period=row["period"] or "UNKNOWN",
        family=row["family"] or Path(row["seed_path"]).parent.name,
        run_strategy=row["run_strategy"] or "",
    )
    return Variant(
        path=Path(row["set_path"]),
        seed=seed,
        target_symbol=(row["target_symbol"] or row["symbol"] or "UNKNOWN").upper(),
        target_period=(row["period"] or seed.period or "UNKNOWN").upper(),
        mutated_keys=tuple(key for key in str(row["mutated_keys"] or "").split(";") if key),
        missing_lot_keys=tuple(key for key in str(row["missing_lot_keys"] or "").split(";") if key),
        policy=row["policy"] or "",
    )


def seeds_from_variants(variants: list[Variant]) -> list[Seed]:
    return [
        Seed(
            path=variant.path,
            symbol=variant.target_symbol,
            period=variant.target_period,
            family=variant.seed.family,
            run_strategy=variant.seed.run_strategy,
        )
        for variant in variants
    ]


def seeds_from_survivors(survivors: list[tuple[Variant, ScoreResult]]) -> list[Seed]:
    return [
        Seed(
            path=variant.path,
            symbol=variant.target_symbol,
            period=variant.target_period,
            family=variant.seed.family,
            run_strategy=variant.seed.run_strategy,
        )
        for variant, _ in survivors
    ]


def choose_seeds(
    seeds: list[Seed],
    max_seeds: int,
    asset_feedback: dict[str, float],
    timeframe_feedback: dict[str, float],
    rng: random.Random,
) -> list[Seed]:
    valid = [seed for seed in seeds if seed.symbol != "UNKNOWN" and seed.period != "UNKNOWN"]
    if not valid:
        valid = seeds
    scored = []
    for seed in valid:
        prior = asset_feedback.get(seed.symbol.upper(), 0.0)
        prior += timeframe_feedback.get(seed.period.upper(), 0.0) * 0.50
        diversity = rng.random() * 5.0
        scored.append((prior + diversity, seed))
    scored.sort(key=lambda item: item[0], reverse=True)
    limit = len(scored) if max_seeds <= 0 else min(max_seeds, len(scored))
    return [seed for _, seed in scored[:limit]]


def related_assets(symbol: str) -> tuple[str, ...]:
    symbol = symbol.upper()
    if symbol in {"XAUUSD", "XAGUSD", "XAUEUR"}:
        return ("XAUUSD", "XAGUSD", "XAUEUR")
    if symbol in {"US30", ".US30CASH", "US500", ".US500CASH", "USTEC", "US100", ".USTECHCASH", "DAX", "DE40", ".DE40CASH"}:
        return ("US30", "US500", "USTEC", "DAX")
    if symbol in {"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "ADAUSD", "DOGEUSD"}:
        return ("BTCUSD", "ETHUSD", "SOLUSD")
    if symbol in {"XTIUSD", "WTI", "BRENT", "CRUDEOIL"}:
        return ("XTIUSD", "BRENT")
    if len(symbol) == 6:
        base = symbol[:3]
        quote = symbol[3:]
        major = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD")
        related = [item for item in major if base in item or quote in item]
        return tuple(dict.fromkeys([symbol, *related]))
    return (symbol,)


def choose_target_symbol(seed: Seed, asset_feedback: dict[str, float], rng: random.Random) -> tuple[str, str]:
    choices = related_assets(seed.symbol)
    if not choices or rng.random() < 0.70:
        return seed.symbol, "exploit"
    ranked = sorted(choices, key=lambda item: asset_feedback.get(item.upper(), 0.0), reverse=True)
    if ranked and rng.random() < 0.50:
        return ranked[0], "asset_feedback"
    return rng.choice(choices), "asset_explore"


def related_timeframes(period: str) -> tuple[str, ...]:
    period = period.upper()
    if period == "M15":
        return ("M15", "M30", "H1")
    if period == "M30":
        return ("M15", "M30", "H1", "H4")
    if period == "H1":
        return ("M30", "H1", "H4", "D1")
    if period == "H4":
        return ("H1", "H4", "D1")
    if period == "D1":
        return ("H4", "D1")
    return TIMEFRAME_UNIVERSE


def choose_target_period(seed: Seed, timeframe_feedback: dict[str, float], rng: random.Random) -> tuple[str, str]:
    current = seed.period.upper()
    choices = tuple(dict.fromkeys(related_timeframes(current)))
    if not choices:
        return current, "tf_exploit"
    if current in choices and rng.random() < 0.60:
        return current, "tf_exploit"
    ranked = sorted(choices, key=lambda item: timeframe_feedback.get(item.upper(), -999999.0), reverse=True)
    ranked_with_feedback = [period for period in ranked if period.upper() in timeframe_feedback]
    if ranked_with_feedback and rng.random() < 0.45:
        return ranked_with_feedback[0], "tf_feedback"
    unexplored = [period for period in choices if period.upper() not in timeframe_feedback]
    if unexplored and rng.random() < 0.70:
        return rng.choice(unexplored), "tf_explore_new"
    return rng.choice(choices), "tf_explore"


def line_candidates(text: str, run_strategy: str, mutation_feedback: dict[str, float]) -> dict[str, tuple[int, list[str], float]]:
    lines = text.splitlines()
    preferred = set(CORE_MUTATION_KEYS.get(run_strategy, CORE_MUTATION_KEYS[""]))
    candidates: dict[str, tuple[int, list[str], float]] = {}
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith(";"):
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key in FROZEN_KEYS or any(key.startswith(prefix) for prefix in FROZEN_PREFIXES):
            continue
        if key not in ALLOWED_MUTATION_KEYS and not any(key.startswith(prefix) for prefix in ALLOWED_MUTATION_PREFIXES):
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
        weight = 1.0
        if key in preferred:
            weight += 3.0
        weight += max(min(mutation_feedback.get(key, 0.0) / 25.0, 4.0), -0.5)
        candidates[key] = (index, parts, max(weight, 0.1))
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
    keys.extend(["Entry_Timing", "ATR_Timeframe"])
    changed: list[str] = []
    for key in dict.fromkeys(keys):
        if replace_existing_current_value(lines, key, enum_value):
            changed.append(key)
    return changed


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
    policy: str,
    rng: random.Random,
) -> Variant:
    text, encoding = read_set_with_encoding(seed.path)
    lines = text.splitlines()
    replace_existing_plain_key(lines, "ForceSymbol", target_symbol)
    timeframe_keys = replace_timeframe_keys(lines, seed.run_strategy, target_period)
    text = "\n".join(lines)
    candidates = line_candidates(text, seed.run_strategy, mutation_feedback)
    selected = weighted_sample(candidates, mutations_per_variant, rng)
    lines = text.splitlines()
    changed: list[str] = []
    for key in selected:
        line_index, parts, _ = candidates[key]
        current = float(parts[0])
        start = float(parts[1])
        step = float(parts[2])
        stop = float(parts[3])
        direction_bias = mutation_feedback.get(key, 0.0)
        if direction_bias > 0 and rng.random() < 0.60:
            direction = rng.choice([-1, 1])
        else:
            direction = rng.choice([-2, -1, 1, 2])
        value = current + direction * step
        if value < start or value > stop:
            slots = int((stop - start) / step)
            value = start + rng.randint(0, max(0, slots)) * step
        parts[0] = format_like(parts[0], max(start, min(stop, value)))
        lhs = lines[line_index].split("=", 1)[0]
        lines[line_index] = f"{lhs}={'||'.join(parts)}"
        changed.append(key)
    changed.extend(timeframe_keys)

    normalized, _, missing = force_fixed_lot_text("\n".join(lines))
    filename = (
        f"{safe_part(target_symbol)}__{safe_part(target_period)}__{safe_part(seed.family)}__"
        f"{safe_part(seed.path.stem)}__g{generation:03d}_s{seed_index:03d}_v{variant_index:03d}.set"
    )
    target = output_dir / safe_part(target_symbol) / safe_part(target_period) / filename
    write_set_text(target, normalized, encoding)
    return Variant(target, seed, target_symbol, target_period, tuple(changed), tuple(sorted(missing)), policy)


def run_backtests(args: argparse.Namespace, set_dir: Path) -> int:
    if not args.expert:
        print("AVISO: --expert no indicado; se omiten backtests.")
        return 0
    command = [
        sys.executable,
        str(BASE_DIR / "run_tests.py"),
        "--template",
        str(Path(args.template).expanduser()),
        "--expert",
        args.expert,
        "--set-dir",
        str(set_dir),
        "--recursive",
        "--infer-tester-from-set",
        "--delay",
        str(args.delay),
    ]
    if args.mt5_path:
        command.extend(["--mt5-path", args.mt5_path])
    if args.data_dir:
        command.extend(["--data-dir", args.data_dir])
    if args.symbol_map:
        command.extend(["--symbol-map", args.symbol_map])
    if args.dry_run:
        command.append("--dry-run")
    print("Ejecutando:", " ".join(f'"{part}"' if " " in part else part for part in command))
    process = subprocess.run(command, cwd=BASE_DIR, text=True)
    return process.returncode


def find_report_for_set(set_path: Path) -> Path | None:
    for suffix in (".htm", ".html", ".xml"):
        candidate = BASE_DIR / "reports" / f"{set_path.stem}{suffix}"
        if candidate.exists():
            return candidate
    candidates = sorted((BASE_DIR / "reports").glob(f"{set_path.stem}.*"))
    return candidates[0] if candidates else None


def evaluate_variants(memory: AgentMemory, variants: list[Variant], score_config: ScoreConfig) -> list[tuple[Variant, ScoreResult]]:
    scored: list[tuple[Variant, ScoreResult]] = []
    for variant in variants:
        report = find_report_for_set(variant.path)
        if not report:
            memory.record_score(variant.path, None, "no_report", None)
            continue
        try:
            result = score_report_file(report, config=score_config)
        except Exception as exc:
            print(f"AVISO: no pude parsear {report}: {exc}")
            memory.record_score(variant.path, None, "parse_error", report)
            continue
        memory.record_score(variant.path, result, "accepted" if result.accepted else "rejected", report)
        scored.append((variant, result))
    return scored


def select_survivors(scored: list[tuple[Variant, ScoreResult]], top_percent: float) -> list[tuple[Variant, ScoreResult]]:
    if not scored:
        return []
    scored = sorted(scored, key=lambda item: item[1].score, reverse=True)
    accepted = [item for item in scored if item[1].accepted]
    if not accepted:
        limit = max(1, int(len(scored) * max(top_percent, 1.0) / 100.0))
        accepted = scored[:limit]
    return accepted


def copy_accepted(survivors: list[tuple[Variant, ScoreResult]], accepted_dir: Path) -> list[Path]:
    if not survivors:
        return []
    accepted_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for variant, result in survivors:
        destination = accepted_dir / f"score_{result.score:07.2f}__{variant.path.name}"
        shutil.copy2(variant.path, destination)
        copied.append(destination)
    return copied


def evaluate_generation(
    args: argparse.Namespace,
    memory: AgentMemory,
    run_dir: Path,
    generation: int,
    variants: list[Variant],
    score_config: ScoreConfig,
) -> list[tuple[Variant, ScoreResult]]:
    scored: list[tuple[Variant, ScoreResult]] = []
    if not (args.execute_backtests or args.dry_run):
        return scored
    generation_dir = run_dir / f"gen_{generation:03d}"
    code = run_backtests(args, generation_dir)
    if code != 0:
        raise RuntimeError(f"run_tests.py termino con codigo {code}")
    if args.dry_run:
        return scored
    scored = evaluate_variants(memory, variants, score_config)
    survivors = select_survivors(scored, args.top_percent)
    copied = copy_accepted(survivors, run_dir / f"accepted_gen_{generation:03d}")
    print(f"Reportes puntuados gen {generation}: {len(scored)}; accepted/copied: {len(copied)}")
    return scored


def resume_last_run(args: argparse.Namespace, memory: AgentMemory, score_config: ScoreConfig) -> int:
    run = memory.latest_run()
    if run is None:
        print("ERROR: no hay runs guardados para continuar")
        return 1
    run_id = int(run["id"])
    run_dir = Path(run["output_dir"])
    planned_generations = int(run["generations"])
    args.variants_per_seed = int(run["variants_per_seed"])
    args.max_seeds = int(run["max_seeds"])
    args.execute_backtests = bool(run["execute_backtests"])
    args.dry_run = bool(run["dry_run"]) or args.dry_run
    if args.execute_backtests and not args.expert:
        print("ERROR: el run pendiente requiere backtests; indica --expert para continuar")
        return 1

    max_generation = memory.max_generation(run_id)
    pending_generation = memory.pending_generated_generation(run_id) if args.execute_backtests else 0
    current_seeds: list[Seed] = []
    did_work = False

    print(f"Continuando run #{run_id}: plan={planned_generations}, ultima_gen={max_generation}")

    if pending_generation:
        variants = memory.variants_for_generation(run_id, pending_generation, status="generated")
        if not variants:
            print(f"ERROR: gen {pending_generation} marcada pendiente, pero no hay .set disponibles")
            return 1
        print(f"Reanudando gen {pending_generation}: backtests pendientes={len(variants)}")
        try:
            scored = evaluate_generation(args, memory, run_dir, pending_generation, variants, score_config)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 1
        did_work = True
        if args.execute_backtests and not args.dry_run and not scored:
            print(f"ERROR: gen {pending_generation} no produjo reportes puntuables; no se genera la siguiente generacion")
            return 1
        if scored:
            current_seeds = seeds_from_survivors(select_survivors(scored, args.top_percent))
        else:
            current_seeds = seeds_from_variants(variants)
        next_generation = pending_generation + 1
    else:
        if max_generation >= planned_generations:
            print(f"Run #{run_id} ya esta completo: {max_generation}/{planned_generations}")
            return 0
        seed_limit = args.max_seeds if args.max_seeds > 0 else 0
        _, latest_generation, current_seeds = memory.continuation_seeds(seed_limit)
        if not current_seeds:
            print("ERROR: no hay seeds disponibles para continuar")
            return 1
        next_generation = latest_generation + 1

    all_generated = 0
    rng = random.Random(args.random_seed)
    for generation in range(next_generation, planned_generations + 1):
        generation_dir = run_dir / f"gen_{generation:03d}"
        mutation_feedback = memory.mutation_feedback()
        asset_feedback = memory.asset_feedback()
        timeframe_feedback = memory.timeframe_feedback()
        selected_seeds = choose_seeds(current_seeds, args.max_seeds, asset_feedback, timeframe_feedback, rng)
        variants: list[Variant] = []
        print(f"Generacion {generation}: seeds={len(selected_seeds)}")
        for seed_index, seed in enumerate(selected_seeds, start=1):
            for variant_index in range(1, args.variants_per_seed + 1):
                target_symbol, policy = choose_target_symbol(seed, asset_feedback, rng)
                target_period, period_policy = choose_target_period(seed, timeframe_feedback, rng)
                variant = create_variant(
                    seed,
                    target_symbol,
                    target_period,
                    generation_dir,
                    generation,
                    seed_index,
                    variant_index,
                    args.mutations_per_variant,
                    mutation_feedback,
                    f"{policy}+{period_policy}",
                    rng,
                )
                memory.record_variant(run_id, generation, variant)
                variants.append(variant)
        all_generated += len(variants)
        did_work = True
        print(f"Generados: {len(variants)} en {generation_dir}")
        try:
            scored = evaluate_generation(args, memory, run_dir, generation, variants, score_config)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 1
        if args.execute_backtests and not args.dry_run and not scored:
            print(f"ERROR: gen {generation} no produjo reportes puntuables; se detiene la continuacion")
            return 1
        if scored:
            current_seeds = seeds_from_survivors(select_survivors(scored, args.top_percent))
        else:
            current_seeds = seeds_from_variants(variants)

    print(f"Run dir: {run_dir}")
    print(f"Memoria: {memory.path}")
    print(f"Sets nuevos generados: {all_generated}")
    return 0 if did_work else 1


def run_agent(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).expanduser()
    output_root = Path(args.output_dir).expanduser()
    run_dir = output_root / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    memory = AgentMemory(Path(args.memory).expanduser())
    rng = random.Random(args.random_seed)
    score_config = ScoreConfig(
        min_net_profit=args.min_net_profit,
        min_profit_factor=args.min_profit_factor,
        min_trades=args.min_trades,
        max_drawdown_pct=args.max_drawdown_pct,
        min_recovery_factor=args.min_recovery_factor,
        min_positive_month_ratio=args.min_positive_month_ratio,
    )

    if args.continue_last_run:
        try:
            return resume_last_run(args, memory, score_config)
        finally:
            memory.close()

    seed_source = str(source_dir)
    seeds = load_seeds(source_dir)
    if not seeds:
        print(f"ERROR: no hay seeds .set en {source_dir}")
        return 1
    asset_groups, aliases = load_asset_universe(Path(args.assets).expanduser())
    print(f"Seeds disponibles: {len(seeds)} ({seed_source})")
    print(f"Universo RoboForex cargado: {sum(len(v) for v in asset_groups.values())} simbolos, {len(aliases)} aliases")
    monthly_pass = (
        f"meses+>={score_config.min_positive_month_ratio}"
        if score_config.min_positive_month_ratio > 0
        else "estabilidad mensual solo score"
    )
    print(
        "Pass config: "
        f"net>={score_config.min_net_profit}, "
        f"pf>={score_config.min_profit_factor}, "
        f"trades>={score_config.min_trades}, "
        f"dd%<={score_config.max_drawdown_pct}, "
        f"recovery>={score_config.min_recovery_factor}, "
        f"{monthly_pass}"
    )
    run_id = memory.create_run(
        source_dir,
        run_dir,
        args.generations,
        args.variants_per_seed,
        args.max_seeds,
        args.execute_backtests,
        args.dry_run,
    )

    current_seeds = seeds
    all_generated = 0
    try:
        for generation in range(1, args.generations + 1):
            generation_dir = run_dir / f"gen_{generation:03d}"
            accepted_dir = run_dir / f"accepted_gen_{generation:03d}"
            mutation_feedback = memory.mutation_feedback()
            asset_feedback = memory.asset_feedback()
            timeframe_feedback = memory.timeframe_feedback()
            selected_seeds = choose_seeds(current_seeds, args.max_seeds, asset_feedback, timeframe_feedback, rng)
            variants: list[Variant] = []
            print(f"Generacion {generation}: seeds={len(selected_seeds)}")
            for seed_index, seed in enumerate(selected_seeds, start=1):
                for variant_index in range(1, args.variants_per_seed + 1):
                    target_symbol, policy = choose_target_symbol(seed, asset_feedback, rng)
                    target_period, period_policy = choose_target_period(seed, timeframe_feedback, rng)
                    variant = create_variant(
                        seed,
                        target_symbol,
                        target_period,
                        generation_dir,
                        generation,
                        seed_index,
                        variant_index,
                        args.mutations_per_variant,
                        mutation_feedback,
                        f"{policy}+{period_policy}",
                        rng,
                    )
                    memory.record_variant(run_id, generation, variant)
                    variants.append(variant)
            all_generated += len(variants)
            print(f"Generados: {len(variants)} en {generation_dir}")

            scored: list[tuple[Variant, ScoreResult]] = []
            if args.execute_backtests or args.dry_run:
                code = run_backtests(args, generation_dir)
                if code != 0:
                    print(f"ERROR: run_tests.py termino con codigo {code}")
                    return code
                if not args.dry_run:
                    scored = evaluate_variants(memory, variants, score_config)
                    survivors = select_survivors(scored, args.top_percent)
                    copied = copy_accepted(survivors, accepted_dir)
                    print(f"Reportes puntuados: {len(scored)}; accepted/copied: {len(copied)}")

            if scored:
                survivors = select_survivors(scored, args.top_percent)
                current_seeds = [
                    Seed(
                        path=variant.path,
                        symbol=variant.target_symbol,
                        period=variant.seed.period,
                        family=variant.seed.family,
                        run_strategy=variant.seed.run_strategy,
                    )
                    for variant, _ in survivors
                ]
            else:
                current_seeds = [
                    Seed(
                        path=variant.path,
                        symbol=variant.target_symbol,
                        period=variant.seed.period,
                        family=variant.seed.family,
                        run_strategy=variant.seed.run_strategy,
                    )
                    for variant in variants
                ]

        print(f"Run dir: {run_dir}")
        print(f"Memoria: {memory.path}")
        print(f"Sets generados: {all_generated}")
        return 0 if all_generated else 1
    finally:
        memory.close()


def main() -> int:
    args = parse_args()
    if args.generations <= 0 or args.variants_per_seed <= 0:
        print("ERROR: generations y variants-per-seed deben ser mayores que 0")
        return 1
    if args.min_trades < 0:
        print("ERROR: --min-trades no puede ser negativo")
        return 1
    if args.min_profit_factor < 0 or args.max_drawdown_pct < 0:
        print("ERROR: profit factor y drawdown deben ser mayores o iguales a 0")
        return 1
    if not 0 <= args.min_positive_month_ratio <= 1:
        print("ERROR: --min-positive-month-ratio debe estar entre 0 y 1")
        return 1
    return run_agent(args)


if __name__ == "__main__":
    raise SystemExit(main())
