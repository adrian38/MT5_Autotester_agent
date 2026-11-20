"""Rastro en disco de cada lanzamiento de script del UI.

La consola de la aplicacion es volatil: cuando un lanzamiento se cancela o un
proceso muere, el motivo se pierde con ella. Aqui queda escrito.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from ui.app_base import BASE_DIR

RUN_LOG_FILE = BASE_DIR / "logs" / "ui_runs.log"
RUN_OUTPUT_DIR = BASE_DIR / "logs" / "ui_runs"


def run_log(message: str) -> None:
    """Anota una linea sin alterar el curso de la ejecucion."""
    try:
        RUN_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with RUN_LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(f"[{timestamp}] pid={os.getpid()} {message}\n")
    except OSError:
        pass


def new_run_output_path(script_name: str) -> Path | None:
    """Fichero donde volcar la salida completa del proceso que va a nacer."""
    try:
        RUN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return RUN_OUTPUT_DIR / f"{stamp}_{Path(script_name).stem}.log"
