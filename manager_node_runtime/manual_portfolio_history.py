"""Preparacion del historico OHLC de un portafolio manual."""
from __future__ import annotations

import configparser
import contextlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _template_path(config: dict[str, Any], cfg: configparser.ConfigParser) -> Path:
    project = Path(str(config["project_dir"])).expanduser().resolve()
    value = str(config.get("template") or cfg.get("Paths", "template_path", fallback="")).strip()
    path = Path(value).expanduser() if value else project / "tester_template.ini"
    return path if path.is_absolute() else project / path


def history_defaults(config: dict[str, Any], cfg: configparser.ConfigParser) -> dict[str, str]:
    template = configparser.ConfigParser(interpolation=None)
    template.optionxform = str
    path = _template_path(config, cfg)
    if not template.read(path, encoding="utf-8") or not template.has_section("Tester"):
        raise ValueError(f"No se pudo leer la plantilla tester: {path}")
    return {
        "from_date": template.get("Tester", "FromDate", fallback="").strip(),
        "to_date": template.get("Tester", "ToDate", fallback="").strip(),
        "model": "1",
    }


def _date(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        datetime.strptime(text, "%Y.%m.%d")
    except ValueError as exc:
        raise ValueError(f"{label} debe tener formato YYYY.MM.DD") from exc
    return text


def _manual_sets(memory_path: Path, portfolio_id: int) -> list[Path]:
    with contextlib.closing(sqlite3.connect(memory_path, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select metrics_json from portfolios where id=?", (portfolio_id,)).fetchone()
        if row is None:
            raise ValueError(f"No existe el portafolio #{portfolio_id}")
        metrics = json.loads(row["metrics_json"] or "{}")
        inputs = metrics.get("inputs") if isinstance(metrics, dict) else {}
        if not isinstance(inputs, dict) or not inputs.get("manual_portfolio"):
            raise ValueError("El histórico directo sólo pertenece a portafolios manuales")
        rows = conn.execute(
            "select set_path from portfolio_allocations where portfolio_id=? order by id", (portfolio_id,),
        ).fetchall()
    paths = [Path(str(item["set_path"])).resolve() for item in rows]
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("El portafolio no conserva todos sus archivos .set")
    return paths


def build_history_command(
    config: dict[str, Any], cfg: configparser.ConfigParser, memory_path: Path,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str], Path]:
    portfolio_id = int(payload.get("portfolio_id") or 0)
    if portfolio_id <= 0:
        raise ValueError("Falta el portafolio")
    defaults = history_defaults(config, cfg)
    from_date = _date(payload.get("from_date") or defaults["from_date"], "La fecha inicial")
    to_date = _date(payload.get("to_date") or defaults["to_date"], "La fecha final")
    if from_date > to_date:
        raise ValueError("La fecha inicial no puede ser posterior a la final")
    project = Path(str(config["project_dir"])).expanduser().resolve()
    settings = Path(str(config.get("settings_file") or "ui_settings.ini"))
    settings = settings if settings.is_absolute() else project / settings
    command = [
        str(config.get("python_executable") or sys.executable), "-u", str(project / "run_tests.py"),
        "--template", str(_template_path(config, cfg)), "--multi-terminal",
        "--terminals-config", str(settings), "--max-workers", "1",
        "--infer-tester-from-set", "--prefer-set-path-timeframe", "--model", "1",
        "--from-date", from_date, "--to-date", to_date,
    ]
    paths = _manual_sets(memory_path, portfolio_id)
    for path in paths:
        command.extend(["--set-file", str(path)])
    normalized = {"portfolio_id": portfolio_id, "from_date": from_date, "to_date": to_date, "model": 1}
    return normalized, command, project
