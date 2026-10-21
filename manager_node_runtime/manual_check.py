"""Comprobacion manual: reportes nuevos de las estrategias de un portafolio.

Quien decide que estrategias son nuevas es el manager, que tiene la genealogia
del portafolio; quien ejecuta es este runtime, el unico proceso con MT5
delante. Cada comprobacion vive en `runtime/<nodo>/manual_checks/<check_id>/`:

    sets/               copia del .set de cada estrategia con su StartLots
    reports/<modelo>/   el .htm de MT5 y las imagenes de sus graficos
    configs/<modelo>/   los .ini del tester
    logs/<modelo>/      el log del runner

Son **dos ejecuciones** por comprobacion, no una: el tester acepta un solo
modelo de datos por corrida, asi que OHLC M1 (`--model 1`) y tick a tick
(`--model 4`) son etapas distintas del mismo trabajo y dejan dos reportes por
estrategia.

Las carpetas del runner se reapuntan con el mismo envoltorio que usa el
auditor real. Sin el, los reportes caen en `reports/` del proyecto, se mezclan
con los de la generacion y la limpieza historica se los lleva por delante.
"""
from __future__ import annotations

import configparser
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import safe_int, save_json, utc_now
from .live_audit_helpers import MT5_MAX_PATH, MT5_NAME_SUFFIX_BUDGET, _read_set_text
from .live_audit_sets import resolve_portfolio_set
from .node_commands import _add_symbol_options


MODEL_OPTIONS = {"ohlc": "1", "tick": "4"}
MODEL_LABELS = {"ohlc": "OHLC M1", "tick": "Tick a tick"}
MANUAL_CHECK_STAGES = ("manual_check_ohlc", "manual_check_tick")
STAGE_MODELS = {"manual_check_ohlc": "ohlc", "manual_check_tick": "tick"}
MODEL_STAGES = {model: stage for stage, model in STAGE_MODELS.items()}


def _date(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        datetime.strptime(text, "%Y.%m.%d")
    except ValueError as exc:
        raise ValueError(f"{label} debe tener formato YYYY.MM.DD") from exc
    return text


def _lot(value: Any) -> float:
    """El lote con el que corre cada set, 0.01 por defecto y editable.

    Es el lote del backtest, no el del portafolio: el reporte sale comparable
    con los que el portafolio ya guarda, medidos todos a lote minimo.
    """
    if value is None or str(value).strip() == "":
        return 0.01
    try:
        lot = round(float(value), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError("El lote debe ser un numero") from exc
    if not 0.01 <= lot <= 100:
        raise ValueError("El lote debe estar entre 0.01 y 100")
    return lot


def _models(payload: dict[str, Any]) -> list[str]:
    requested = payload.get("models")
    if not isinstance(requested, list) or not requested:
        raise ValueError("Marca al menos un tipo de datos: ohlc o tick")
    models = [str(value).strip().lower() for value in requested]
    unknown = [value for value in models if value not in MODEL_OPTIONS]
    if unknown:
        raise ValueError(f"Tipos de datos desconocidos: {', '.join(sorted(set(unknown)))}")
    return [model for model in MODEL_OPTIONS if model in models]


def _strategies(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("strategies")
    if not isinstance(rows, list) or not rows:
        raise ValueError("No hay estrategias nuevas que comprobar")
    strategies = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Cada estrategia debe ser un objeto")
        set_path = str(row.get("set_path") or row.get("set_id") or "").strip()
        if not set_path:
            raise ValueError("Una estrategia llego sin la ruta de su .set")
        strategies.append({
            "set_path": set_path,
            "set_name": str(row.get("set_name") or Path(set_path).name),
            "symbol": str(row.get("symbol") or ""),
            "timeframe": str(row.get("timeframe") or ""),
            "candidate_id": str(row.get("candidate_id") or ""),
        })
    return strategies


def normalize_manual_check(payload: dict[str, Any]) -> dict[str, Any]:
    """Valida la peticion del manager sin tocar el disco."""
    portfolio_id = safe_int(payload.get("portfolio_id"), 0, minimum=0)
    if portfolio_id <= 0:
        raise ValueError("Falta el portafolio")
    from_date = _date(payload.get("from_date"), "La fecha inicial")
    to_date = _date(payload.get("to_date"), "La fecha final")
    if from_date > to_date:
        raise ValueError("La fecha inicial no puede ser posterior a la final")
    use_multiterminal = payload.get("use_multiterminal", True)
    if not isinstance(use_multiterminal, bool):
        raise ValueError("use_multiterminal debe ser verdadero o falso")
    return {
        "portfolio_id": portfolio_id,
        "scope": "monthly" if str(payload.get("scope") or "").strip().lower() == "monthly" else "full_history",
        "portfolio_label": str(payload.get("portfolio_label") or "").strip(),
        "from_date": from_date,
        "to_date": to_date,
        "models": _models(payload),
        "use_multiterminal": use_multiterminal,
        "lot": _lot(payload.get("lot")),
        "strategies": _strategies(payload),
    }


def _check_set_name(index: int, source_name: str, work: Path) -> str:
    """El nombre del set dentro del area de la comprobacion.

    MT5 trunca a 259 caracteres la ruta que recibe y se queda parado sin
    decirlo, asi que el recorte se decide aqui, igual que en el auditor.
    """
    prefix = f"check_{index:03d}_"
    budget = max(MT5_MAX_PATH - len(str(work)) - 1 - len(prefix) - MT5_NAME_SUFFIX_BUDGET, 8)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(source_name).stem)[:budget]
    return f"{prefix}{stem.rstrip('._-') or stem}.set"


def _write_check_sets(project: Path, request: dict[str, Any], sets_dir: Path) -> None:
    """Copia cada .set con su StartLots puesto y anota el nombre resultante."""
    lot = f"{request['lot']:.8f}".rstrip("0").rstrip(".")
    for index, strategy in enumerate(request["strategies"], 1):
        source = resolve_portfolio_set(project, strategy["set_path"])
        text, encoding = _read_set_text(source)
        pattern = re.compile(r"(?mi)^StartLots=([^|\r\n]*)(.*)$")
        if pattern.search(text):
            text = pattern.sub(lambda match: f"StartLots={lot}{match.group(2)}", text, count=1)
        else:
            text = f"{text}\nStartLots={lot}||{lot}||0||0||N\n"
        target = sets_dir / _check_set_name(index, source.name, sets_dir)
        target.write_text(text, encoding=encoding, newline="\n")
        strategy["source_set"] = str(source)
        strategy["runtime_set"] = target.name
        strategy["report_stem"] = target.stem


def check_root(runtime_dir: Path) -> Path:
    return runtime_dir / "manual_checks"


def prepare_manual_check(
    config: dict[str, Any], runtime_dir: Path, payload: dict[str, Any],
) -> dict[str, Any]:
    """Deja el area de trabajo lista y devuelve la peticion ya resuelta.

    Se hace antes de encolar y no al lanzar: una ruta de set que ya no existe
    tiene que fallar delante del usuario, no dentro de una cola que correra
    dentro de tres horas.
    """
    request = normalize_manual_check(payload)
    project = Path(str(config["project_dir"])).expanduser().resolve()
    check_id = "check_" + time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000:06d}"
    work = check_root(runtime_dir) / check_id
    sets_dir = work / "sets"
    sets_dir.mkdir(parents=True, exist_ok=True)
    _write_check_sets(project, request, sets_dir)
    request["check_id"] = check_id
    request["work_dir"] = str(work)
    request["created_at"] = utc_now()
    save_json(work / "check.json", request)
    return request


def _report_dirs(work: Path, model: str) -> tuple[Path, Path, Path]:
    reports = work / "reports" / model
    configs = work / "configs" / model
    logs = work / "logs" / model
    for directory in (reports, configs, logs):
        directory.mkdir(parents=True, exist_ok=True)
    return reports, configs, logs


def _wrapper(reports_dir: Path, configs_dir: Path, logs_dir: Path) -> str:
    """El runner con sus carpetas apuntando al area de la comprobacion.

    `run_tests` esta partido en modulos y cada uno guarda su propia referencia
    a REPORT_DIR/CONFIG_DIR/LOG_DIR: reapuntar solo la fachada deja los
    reportes en las carpetas del proyecto.
    """
    return (
        "import sys, run_tests; from pathlib import Path; "
        f"_dirs = (('REPORT_DIR', Path({str(reports_dir)!r})), "
        f"('CONFIG_DIR', Path({str(configs_dir)!r})), "
        f"('LOG_DIR', Path({str(logs_dir)!r}))); "
        "[setattr(module, name, value) "
        "for key, module in list(sys.modules.items()) "
        "if key == 'run_tests' or key.startswith('run_tests_') "
        "for name, value in _dirs if hasattr(module, name)]; "
        "sys.argv = ['run_tests.py'] + sys.argv[1:]; "
        "raise SystemExit(run_tests.main())"
    )


def _template_path(config: dict[str, Any], cfg: configparser.ConfigParser) -> Path:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    value = str(config.get("template") or cfg.get("Paths", "template_path", fallback="")).strip()
    path = Path(value).expanduser() if value else project / "tester_template.ini"
    return path if path.is_absolute() else project / path


def build_manual_check_command(
    config: dict[str, Any], cfg: configparser.ConfigParser, request: dict[str, Any], model: str,
) -> tuple[list[str], Path]:
    """La orden de una de las dos ejecuciones, la del modelo pedido."""
    if model not in MODEL_OPTIONS:
        raise ValueError(f"Tipo de datos desconocido: {model}")
    work = Path(str(request.get("work_dir") or ""))
    sets_dir = work / "sets"
    set_files = sorted(sets_dir.glob("*.set"))
    if not set_files:
        raise ValueError("La comprobacion no conserva sus archivos .set")
    reports, configs, logs = _report_dirs(work, model)
    project = Path(str(config["project_dir"])).expanduser().resolve()
    settings = Path(str(config.get("settings_file") or "ui_settings.ini"))
    settings = settings if settings.is_absolute() else project / settings
    workers = 1
    if bool(request.get("use_multiterminal", True)):
        workers = safe_int(cfg.get("Multiterminal", "workers", fallback="1"), 1, minimum=1, maximum=64)
    command = [
        str(config.get("python_executable") or sys.executable), "-u", "-c",
        _wrapper(reports, configs, logs),
        "--template", str(_template_path(config, cfg)), "--multi-terminal",
        "--terminals-config", str(settings), "--max-workers", str(workers),
        "--infer-tester-from-set", "--prefer-set-path-timeframe",
        "--model", MODEL_OPTIONS[model],
        "--from-date", str(request["from_date"]), "--to-date", str(request["to_date"]),
    ]
    broker = str(config.get("broker") or cfg.get("General", "ubs_broker", fallback="ROBOFOREX")).upper()
    _add_symbol_options(command, cfg, broker)
    for set_file in set_files:
        command.extend(["--set-file", str(set_file)])
    return command, project


def manual_check_summary(work: Path) -> dict[str, Any]:
    """Lo que hay en disco de una comprobacion, para el manager.

    Se lee del disco y no del estado del trabajo: el manager pregunta tambien
    por comprobaciones viejas, cuyo trabajo ya no es el actual del nodo.
    """
    request: dict[str, Any] = {}
    try:
        request = json.loads((work / "check.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        request = {}
    reports: dict[str, list[str]] = {}
    for model in MODEL_OPTIONS:
        directory = work / "reports" / model
        if directory.is_dir():
            reports[model] = sorted(path.name for path in directory.iterdir() if path.is_file())
    return {
        "check_id": work.name,
        "work_dir": str(work),
        "request": request,
        "reports": reports,
    }
