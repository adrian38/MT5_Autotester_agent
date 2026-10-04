"""Construccion de las ordenes que el nodo lanza contra ubs_agent.py."""
from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from . import guided_batches
from .common import safe_int
from .node_settings import (
    _table_exists,
    declared_cli_options,
    memory_path,
    read_settings,
    resolve_generation_mode,
    setting,
    setting_bool,
)


SCORE_OPTIONS = {
    "ubs_pass_min_net_profit": "--min-net-profit",
    "ubs_pass_min_profit_factor": "--min-profit-factor",
    "ubs_pass_min_trades": "--min-trades",
    "ubs_pass_max_drawdown_pct": "--max-drawdown-pct",
    "ubs_pass_min_recovery_factor": "--min-recovery-factor",
    "ubs_long_tf_min_trades_w1": "--min-trades-w1",
    "ubs_long_tf_min_trades_mn": "--min-trades-mn",
}

VALUE_OPTIONS = {
    "--source-dir", "--output-dir", "--memory", "--broker", "--account-type", "--template",
    "--generations", "--variants-per-seed", "--max-seeds", "--delay", "--generation-mode", "--random-seed",
    "--from-date", "--to-date", "--min-net-profit", "--min-profit-factor", "--min-trades",
    "--max-drawdown-pct", "--min-recovery-factor", "--min-trades-w1", "--min-trades-mn",
    "--terminals-config", "--max-workers", "--expert", "--mt5-path", "--data-dir", "--symbol-map",
    "--symbol-suffix", "--symbol-futures-suffix", "--symbol-shares-suffix",
    "--robust-run-id", "--robust-positive-bonus", "--robust-negative-bonus",
    "--final-tick-run-id", "--final-tick-stage", "--final-tick-min-history-quality",
    "--final-tick-min-ohlc-trades", "--final-tick-min-trades-w1", "--final-tick-min-trades-mn",
    "--final-tick-max-net-delta-pct", "--final-tick-max-pf-delta-pct",
    "--final-tick-max-dd-delta-pct", "--final-tick-max-trades-delta-pct",
    "--final-tick-ohlc-from-date", "--final-tick-ohlc-to-date",
    "--regression-run-id", "--regression-from-date", "--regression-to-date",
    "--regression-min-net-profit", "--regression-min-profit-factor",
    "--regression-min-trades", "--regression-min-trades-w1", "--regression-min-trades-mn",
    "--regression-max-drawdown-pct", "--regression-min-recovery-factor",
    "--regression-min-positive-month-ratio", "--regression-positive-points",
    "--regression-negative-points",
}

def _add(args: list[str], option: str, value: Any) -> None:
    # None es «sin valor», no el texto "None": `--random-seed None` mataba
    # ubs_agent.py con `invalid int value` en cuanto la semilla quedaba vacía.
    if value is None:
        return
    text = str(value).strip()
    if text:
        args.extend([option, text])


def filter_supported_options(command: list[str], script: Path) -> list[str]:
    """Remove manager options that an older broker branch does not expose."""
    supported = declared_cli_options(script)
    # A custom wrapper may not define argparse options in its own source.
    if supported is None:
        return command
    prefix, options = command[:3], command[3:]
    filtered: list[str] = []
    index = 0
    while index < len(options):
        token = options[index]
        if token.startswith("--") and token not in supported:
            index += 2 if token in VALUE_OPTIONS and index + 1 < len(options) else 1
            continue
        filtered.append(token)
        index += 1
    return prefix + filtered


def _add_generation_execution_args(
    args: list[str], config: dict[str, Any], payload: dict[str, Any], cfg,
    settings_path: Path, broker: str,
) -> None:
    args.append("--execute-backtests")
    if setting_bool(cfg, "Multiterminal", "enabled"):
        args.extend(["--multi-terminal", "--terminals-config", str(settings_path)])
        workers = safe_int(
            payload.get("max_workers", setting(cfg, "Multiterminal", "workers", "1")),
            1,
            minimum=1,
            maximum=64,
        )
        _add(args, "--max-workers", workers)
    else:
        expert = str(config.get("expert") or setting(cfg, "Paths", "ubs_ex5_file"))
        if not expert:
            raise ValueError("Falta Paths.ubs_ex5_file y no hay multiterminal habilitado")
        _add(args, "--expert", expert)
        _add(args, "--mt5-path", setting(cfg, "Paths", "mt5_path"))
        _add(args, "--data-dir", setting(cfg, "Paths", "mt5_data_root"))
    broker_key = broker.lower().replace(" ", "")
    if setting_bool(cfg, "General", "symbol_map_enabled"):
        symbol_map = setting(cfg, "General", f"symbol_map_{broker_key}") or setting(cfg, "General", "symbol_map")
        _add(args, "--symbol-map", symbol_map)
    if setting_bool(cfg, "General", "symbol_suffix_enabled"):
        _add(args, "--symbol-suffix", setting(cfg, "General", "symbol_suffix"))
        _add(args, "--symbol-futures-suffix", setting(cfg, "General", "symbol_futures_suffix"))
        _add(args, "--symbol-shares-suffix", setting(cfg, "General", "symbol_shares_suffix"))


def _add_generation_range_args(args, project, payload, defaults, cfg) -> None:
    if payload.get("guided_batch_id"):
        prepared = guided_batches.batch_dir(project, payload["guided_batch_id"]) / "batch.json"
        _add(args, "--prepared-manifest", prepared)
    _add(args, "--from-date", payload.get(
        "from_date", defaults.get("from_date", setting(cfg, "General", "ubs_agent_from_date")),
    ))
    _add(args, "--to-date", payload.get(
        "to_date", defaults.get("to_date", setting(cfg, "General", "ubs_agent_to_date")),
    ))


def build_generation_command(config: dict[str, Any], payload: dict[str, Any]) -> tuple[list[str], Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    script = project / "ubs_agent.py"
    settings_path = Path(str(config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    if not script.is_file():
        raise ValueError(f"No existe {script}")
    if not settings_path.is_file():
        raise ValueError(f"No existe {settings_path}")
    cfg = read_settings(settings_path)
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}

    def pick(name: str, settings_key: str, fallback: Any) -> Any:
        if name in payload:
            return payload[name]
        if name in defaults:
            return defaults[name]
        return setting(cfg, "General", settings_key, str(fallback))

    broker = str(config.get("broker") or setting(cfg, "General", "ubs_broker", "ROBOFOREX")).upper()
    account = str(config.get("account_type") or setting(cfg, "General", "ubs_account_type", "ECN")).upper()
    source = str(config.get("source_dir") or setting(cfg, "Paths", "set_files_root"))
    output = str(config.get("output_dir") or setting(cfg, "Paths", "ubs_generation_output"))
    template = str(config.get("template") or setting(cfg, "Paths", "template_path", str(project / "tester_template.ini")))
    python = str(config.get("python_executable") or sys.executable)
    generations = safe_int(pick("generations", "ubs_generation_count", 1), 1, minimum=1, maximum=1000)
    variants = safe_int(pick("variants_per_seed", "ubs_variants_per_seed", 10), 10, minimum=1, maximum=10000)
    max_seeds = safe_int(pick("max_seeds", "ubs_max_seeds", 30), 30, minimum=0, maximum=100000)
    generation_mode = resolve_generation_mode(config, payload, cfg)
    execute = payload.get("execute_backtests", defaults.get("execute_backtests", setting_bool(cfg, "General", "ubs_agent_execute", True)))

    args = [python, "-u", str(script)]
    _add(args, "--source-dir", source)
    _add(args, "--output-dir", output)
    _add(args, "--memory", memory_path(config, cfg))
    _add(args, "--broker", broker)
    _add(args, "--account-type", account)
    _add(args, "--template", template)
    _add(args, "--generations", generations)
    _add(args, "--variants-per-seed", variants)
    _add(args, "--max-seeds", max_seeds)
    _add(args, "--delay", pick("delay", "delay", 5))
    _add(args, "--generation-mode", generation_mode)
    _add(args, "--random-seed", payload.get("random_seed", defaults.get("random_seed")))
    _add_generation_range_args(args, project, payload, defaults, cfg)

    for key, option in SCORE_OPTIONS.items():
        _add(args, option, setting(cfg, "General", key))
    if setting_bool(cfg, "General", "ubs_experimental_long_timeframes"):
        args.append("--experimental-long-timeframes")
    if bool(payload.get("continue_last", False)):
        args.append("--continue-last-run")
    if bool(payload.get("dry_run", False)):
        args.append("--dry-run")
    if execute:
        _add_generation_execution_args(
            args, config, payload, cfg, settings_path, broker,
        )
    return filter_supported_options(args, script), project


ROBUST_SCORE_OPTIONS = {
    "ubs_robust_pass_min_net_profit": "--min-net-profit",
    "ubs_robust_pass_min_profit_factor": "--min-profit-factor",
    "ubs_robust_pass_min_trades": "--min-trades",
    "ubs_robust_pass_max_drawdown_pct": "--max-drawdown-pct",
    "ubs_robust_pass_min_recovery_factor": "--min-recovery-factor",
    "ubs_robust_min_net_retention": "--robust-min-net-retention",
    "ubs_robust_min_pf_edge_retention": "--robust-min-pf-edge-retention",
    "ubs_robust_min_recovery_retention": "--robust-min-recovery-retention",
    "ubs_robust_max_dd_inflation": "--robust-max-dd-inflation",
    "ubs_long_tf_min_trades_w1": "--min-trades-w1",
    "ubs_long_tf_min_trades_mn": "--min-trades-mn",
}
FINAL_TICK_OPTIONS = {
    "ubs_final_tick_min_history_quality": "--final-tick-min-history-quality",
    "ubs_final_tick_min_ohlc_trades": "--final-tick-min-ohlc-trades",
    "ubs_final_tick_min_trades_w1": "--final-tick-min-trades-w1",
    "ubs_final_tick_min_trades_mn": "--final-tick-min-trades-mn",
    "ubs_final_tick_max_net_delta_pct": "--final-tick-max-net-delta-pct",
    "ubs_final_tick_max_pf_delta_pct": "--final-tick-max-pf-delta-pct",
    "ubs_final_tick_max_dd_delta_pct": "--final-tick-max-dd-delta-pct",
    "ubs_final_tick_max_trades_delta_pct": "--final-tick-max-trades-delta-pct",
}
REGRESSION_OPTIONS = {
    "ubs_regression_from_date": "--regression-from-date",
    "ubs_regression_to_date": "--regression-to-date",
    "ubs_regression_min_net_profit": "--regression-min-net-profit",
    "ubs_regression_min_profit_factor": "--regression-min-profit-factor",
    "ubs_regression_min_trades": "--regression-min-trades",
    "ubs_regression_min_trades_w1": "--regression-min-trades-w1",
    "ubs_regression_min_trades_mn": "--regression-min-trades-mn",
    "ubs_regression_max_drawdown_pct": "--regression-max-drawdown-pct",
    "ubs_regression_min_recovery_factor": "--regression-min-recovery-factor",
    "ubs_regression_min_positive_month_ratio": "--regression-min-positive-month-ratio",
    "ubs_regression_min_pf_efficiency": "--regression-min-pf-efficiency",
    "ubs_regression_max_dd_ratio": "--regression-max-dd-ratio",
    "ubs_regression_positive_points": "--regression-positive-points",
    "ubs_regression_negative_points": "--regression-negative-points",
}
FINAL_TICK_STAGES = {"final_tick", "final_tick_quality", "final_tick_6m", "final_tick_6m_quality"}


def _add_options(args: list[str], cfg, options: dict[str, str]) -> None:
    """Anade cada opcion del mapa con el valor que tenga en ui_settings."""
    for key, option in options.items():
        _add(args, option, setting(cfg, "General", key))


def _run_base_dates(config: dict[str, Any], cfg, run_id: int) -> tuple[str, str]:
    """Fechas con las que nacio el run; sin ellas no se puede reintentar."""
    db_path = memory_path(config, cfg)
    if not db_path.is_file():
        raise ValueError(f"No existe la memoria SQLite: {db_path}")
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "runs"):
            raise ValueError("La memoria SQLite no contiene la tabla runs")
        run = conn.execute("select config_json from runs where id=?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"No existe el run #{run_id} en memoria")
    try:
        run_config = json.loads(str(run["config_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"El run #{run_id} tiene config_json invalido") from exc
    run_config = run_config if isinstance(run_config, dict) else {}
    execution = run_config.get("execution") if isinstance(run_config.get("execution"), dict) else {}
    run_args = run_config.get("args") if isinstance(run_config.get("args"), dict) else {}
    from_date = str(execution.get("from_date") or run_args.get("from_date") or "").strip()
    to_date = str(execution.get("to_date") or run_args.get("to_date") or "").strip()
    if not from_date or not to_date:
        raise ValueError(
            f"El run #{run_id} no guarda sus fechas base; se cancela para no usar fechas actuales"
        )
    return from_date, to_date


def _add_result_stage(args: list[str], config: dict[str, Any], cfg, run_id: int) -> None:
    """Reintento de los resultados del run con sus fechas originales."""
    from_date, to_date = _run_base_dates(config, cfg, run_id)
    args.append("--retry-mismatch-run")
    _add(args, "--retry-run-id", run_id)
    _add(args, "--from-date", from_date)
    _add(args, "--to-date", to_date)
    _add_options(args, cfg, SCORE_OPTIONS)


def _add_robustness_stage(args: list[str], cfg, run_id: int) -> None:
    """Evaluacion OOS de los candidatos aceptados del run."""
    args.extend(["--evaluate-robustness", "--robust-pending-only"])
    _add(args, "--robust-run-id", run_id)
    _add(args, "--robust-positive-bonus", setting(cfg, "General", "ubs_robust_positive_bonus", "70"))
    _add(args, "--robust-negative-bonus", setting(cfg, "General", "ubs_robust_negative_bonus", "-70"))
    _add(args, "--from-date", setting(cfg, "General", "ubs_robust_from_date"))
    _add(args, "--to-date", setting(cfg, "General", "ubs_robust_to_date"))
    _add_options(args, cfg, ROBUST_SCORE_OPTIONS)


def _add_final_tick_stage(args: list[str], cfg, run_id: int, stage: str) -> None:
    """Final tick corto o de seis meses, con o sin reintento de calidad."""
    six_month = stage in {"final_tick_6m", "final_tick_6m_quality"}
    retry_quality = stage in {"final_tick_quality", "final_tick_6m_quality"}
    prefix = "ubs_final_tick_6m" if six_month else "ubs_final_tick"
    args.extend(["--evaluate-final-tick", "--final-tick-pending-only"])
    if retry_quality:
        args.extend(["--final-tick-retry-pending-quality", "--final-tick-skip-ohlc"])
    _add(args, "--final-tick-run-id", run_id)
    _add(args, "--final-tick-stage", "six_month" if six_month else "probe")
    _add(args, "--from-date", setting(cfg, "General", f"{prefix}_from_date"))
    _add(args, "--to-date", setting(cfg, "General", f"{prefix}_to_date"))
    _add(args, "--final-tick-ohlc-from-date", setting(cfg, "General", f"{prefix}_ohlc_from_date"))
    _add(args, "--final-tick-ohlc-to-date", setting(cfg, "General", f"{prefix}_ohlc_to_date"))
    _add_options(args, cfg, FINAL_TICK_OPTIONS)


def _add_regression_stage(args: list[str], cfg, run_id: int) -> None:
    """Prueba regresiva sobre los aceptados de seis meses."""
    args.extend(["--evaluate-regression", "--regression-pending-only"])
    _add(args, "--regression-run-id", run_id)
    _add_options(args, cfg, REGRESSION_OPTIONS)


def _add_stage_options(
    args: list[str], config: dict[str, Any], cfg, stage: str, run_id: int
) -> None:
    """Opciones propias de la etapa pedida del pipeline."""
    if stage == "result":
        _add_result_stage(args, config, cfg, run_id)
    elif stage == "robustness":
        _add_robustness_stage(args, cfg, run_id)
    elif stage in FINAL_TICK_STAGES:
        _add_final_tick_stage(args, cfg, run_id, stage)
    elif stage == "regression":
        _add_regression_stage(args, cfg, run_id)
    else:
        raise ValueError(f"Etapa de pipeline desconocida: {stage}")


def _add_terminal_options(
    args: list[str], config: dict[str, Any], cfg, payload: dict[str, Any], settings_path: Path
) -> None:
    """Terminal o pool multiterminal con el que se ejecutara la etapa."""
    if setting_bool(cfg, "Multiterminal", "enabled"):
        args.extend(["--multi-terminal", "--terminals-config", str(settings_path)])
        workers = safe_int(
            payload.get("max_workers", setting(cfg, "Multiterminal", "workers", "1")),
            1, minimum=1, maximum=64,
        )
        _add(args, "--max-workers", workers)
        return
    expert = str(config.get("expert") or setting(cfg, "Paths", "ubs_ex5_file"))
    if not expert:
        raise ValueError("Falta Paths.ubs_ex5_file y no hay multiterminal habilitado")
    _add(args, "--expert", expert)
    _add(args, "--mt5-path", setting(cfg, "Paths", "mt5_path"))
    _add(args, "--data-dir", setting(cfg, "Paths", "mt5_data_root"))


def _add_symbol_options(args: list[str], cfg, broker: str) -> None:
    """Mapa de simbolos y sufijos del broker, si estan habilitados."""
    broker_key = broker.lower().replace(" ", "")
    if setting_bool(cfg, "General", "symbol_map_enabled"):
        _add(
            args, "--symbol-map",
            setting(cfg, "General", f"symbol_map_{broker_key}") or setting(cfg, "General", "symbol_map"),
        )
    if setting_bool(cfg, "General", "symbol_suffix_enabled"):
        _add(args, "--symbol-suffix", setting(cfg, "General", "symbol_suffix"))
        _add(args, "--symbol-futures-suffix", setting(cfg, "General", "symbol_futures_suffix"))
        _add(args, "--symbol-shares-suffix", setting(cfg, "General", "symbol_shares_suffix"))


def build_pipeline_stage_command(
    config: dict[str, Any],
    payload: dict[str, Any],
    stage: str,
    run_id: int,
) -> tuple[list[str], Path]:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    script = project / "ubs_agent.py"
    settings_path = Path(str(config.get("settings_file") or "ui_settings.ini"))
    if not settings_path.is_absolute():
        settings_path = project / settings_path
    cfg = read_settings(settings_path)
    broker = str(config.get("broker") or setting(cfg, "General", "ubs_broker", "ROBOFOREX")).upper()
    account = str(config.get("account_type") or setting(cfg, "General", "ubs_account_type", "ECN")).upper()
    python = str(config.get("python_executable") or sys.executable)
    args = [python, "-u", str(script)]
    _add(args, "--source-dir", config.get("source_dir") or setting(cfg, "Paths", "set_files_root"))
    _add(args, "--output-dir", config.get("output_dir") or setting(cfg, "Paths", "ubs_generation_output"))
    _add(args, "--memory", memory_path(config, cfg))
    _add(args, "--broker", broker)
    _add(args, "--account-type", account)
    _add(args, "--template", config.get("template") or setting(cfg, "Paths", "template_path", project / "tester_template.ini"))
    _add(args, "--delay", payload.get("delay", setting(cfg, "General", "delay", "5")))
    _add_stage_options(args, config, cfg, stage, run_id)
    if bool(payload.get("dry_run", False)):
        args.append("--dry-run")
    _add_terminal_options(args, config, cfg, payload, settings_path)
    _add_symbol_options(args, cfg, broker)
    return filter_supported_options(args, script), project
