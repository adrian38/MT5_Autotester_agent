"""Preparacion del historico OHLC de un portafolio manual.

El informe **no** se llama como el `.set`. `run_tests.py` nombra cada informe
con el nombre del set y los deja todos en la unica carpeta `reports/`, que es
tambien donde vive la evidencia del pool; como las estrategias de un portafolio
manual suelen ser candidatos aceptados —son usos en real—, correrlo con el
nombre de origen sustituia el informe base del candidato. Paso el 2026-11-08
con cuatro candidatos de AXI: su tramo 2020-2024 se convirtio en uno 2020-2026
que solapa con el de robustez, y las tres carteras que los usaban se quedaron
sin base reconstruible.

La salida es `manual_<portafolio:06d>_<set>`, igual que la comprobacion manual
usa `check_<n:03d>_<set>`. El manager busca ese nombre en
`portfolio_manual_reports.report_for_set`, que sigue siendo la unica regla de
emparejado entre un set manual y su historico.
"""
from __future__ import annotations

import configparser
import contextlib
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .manual_portfolio import normalize_manual_set, pool_report_names, report_stem
from .node_commands import _add_symbol_options, _add_symbol_universe_option
from .common import safe_int


#: Hermana de `sets/` dentro de `<runtime>/manual_portfolios/<uuid>/`, para que
#: borrar el portafolio se la lleve con el resto de la carpeta.
HISTORY_SETS_DIRNAME = "history_sets"


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


def _manual_sets(memory_path: Path, portfolio_id: int) -> tuple[list[Path], set[str]]:
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
        protected = pool_report_names(conn)
    paths = [Path(str(item["set_path"])).resolve() for item in rows]
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("El portafolio no conserva todos sus archivos .set")
    return paths, protected


def manual_report_stem(portfolio_id: int, set_name: str) -> str:
    """El nombre del informe del histórico de un portafolio manual.

    Misma forma que `check_<n:03d>_` de la comprobación manual, con el número
    del portafolio: dos carteras manuales pueden traer el mismo `.set` y sus
    históricos no tienen por qué coincidir, porque cada una elige sus fechas.
    """
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(set_name).stem)
    return f"manual_{int(portfolio_id):06d}_{stem}"


def _history_sets(folder: Path, portfolio_id: int, paths: list[Path]) -> list[Path]:
    """Copia cada `.set` con el nombre que debe llevar su informe.

    MT5 trunca la ruta a 259 caracteres y se queda parado sin decirlo, así que
    la copia vive junto a los sets del portafolio y no más hondo. La carpeta se
    rehace en cada ejecución: lo que sobrevive es el informe, no la copia, y un
    miembro retirado no debe seguir corriendo.
    """
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    copies = []
    for path in paths:
        target = folder / f"{manual_report_stem(portfolio_id, path.name)}.set"
        shutil.copy2(path, target)
        copies.append(target)
    return copies


def _refuse_pool_report_overwrite(paths: list[Path], protected: set[str]) -> None:
    """Última red: ningún informe de esta ejecución pisa evidencia del pool.

    Con el prefijo no debería ocurrir nunca, y por eso se comprueba sobre las
    copias y no sobre los sets de origen: lo que importa no es cómo se llama lo
    que subió el usuario —subir un set del pool es legítimo—, sino qué nombre
    se va a escribir en `reports/`.
    """
    clashing = sorted({path.name for path in paths if report_stem(path.name) in protected})
    if clashing:
        raise ValueError(
            "El histórico OHLC sobrescribiría el informe de un candidato del pool, que "
            "dejaría de poder reconstruir la base de sus carteras: "
            + ", ".join(clashing)
        )


def _repair_manual_lots(memory_path: Path, portfolio_id: int, paths: list[Path]) -> None:
    """Normaliza sets heredados y sincroniza su asignacion fija en SQLite."""
    for path in paths:
        normalize_manual_set(path)
    count = len(paths)
    with contextlib.closing(sqlite3.connect(memory_path, timeout=10)) as conn:
        conn.execute("begin immediate")
        conn.execute(
            "update portfolio_allocations set units=1,lot=0.01,lot_size_step=0.01 "
            "where portfolio_id=?", (portfolio_id,),
        )
        conn.execute(
            "update portfolio_members set lot_multiplier=1,lot=0.01,lot_size_step=0.01 "
            "where portfolio_id=?", (portfolio_id,),
        )
        conn.execute(
            "update portfolios set total_lot=?,total_units=? where id=?",
            (round(count * 0.01, 8), count, portfolio_id),
        )
        conn.commit()


def _history_workers(cfg: configparser.ConfigParser, payload: dict[str, Any]) -> tuple[bool, int]:
    use_multiterminal = payload.get("use_multiterminal", True)
    if not isinstance(use_multiterminal, bool):
        raise ValueError("use_multiterminal debe ser verdadero o falso")
    configured = safe_int(
        cfg.get("Multiterminal", "workers", fallback="1"), 1, minimum=1, maximum=64,
    )
    return use_multiterminal, configured if use_multiterminal else 1


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
    use_multiterminal, workers = _history_workers(cfg, payload)
    command = [
        str(config.get("python_executable") or sys.executable), "-u", str(project / "run_tests.py"),
        "--template", str(_template_path(config, cfg)), "--multi-terminal",
        "--terminals-config", str(settings), "--max-workers", str(workers),
        "--infer-tester-from-set", "--prefer-set-path-timeframe", "--model", "1",
        "--from-date", from_date, "--to-date", to_date,
    ]
    broker = str(config.get("broker") or cfg.get("General", "ubs_broker", fallback="ROBOFOREX")).upper()
    _add_symbol_options(command, cfg, broker)
    _add_symbol_universe_option(command, project, broker)
    paths, protected = _manual_sets(memory_path, portfolio_id)
    _repair_manual_lots(memory_path, portfolio_id, paths)
    # La copia se hace despues de normalizar: el backtest tiene que ver el
    # mismo contenido que queda guardado, no el que subio el usuario.
    copies = _history_sets(paths[0].parent.parent / HISTORY_SETS_DIRNAME, portfolio_id, paths)
    _refuse_pool_report_overwrite(copies, protected)
    for path in copies:
        command.extend(["--set-file", str(path)])
    normalized = {
        "portfolio_id": portfolio_id, "from_date": from_date, "to_date": to_date,
        "model": 1, "use_multiterminal": use_multiterminal,
    }
    return normalized, command, project
