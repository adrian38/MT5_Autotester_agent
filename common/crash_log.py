"""Donde acaba una excepcion que no atrapa nadie.

El 2026-11-01 el hilo del nodo dejo de aceptar conexiones a las 04:20 y el
manager se paso nueve horas con «Connection refused». No hubo manera de saber
por que: `logs/manager_node.log` solo anota arranques y paradas, y la traza de
un hilo que muere la imprime `threading.excepthook` por `stderr`, que con
`py app_ui.py` es la consola del lanzador. Esa consola ya no existia.

Aqui se duplica `stderr` a un fichero y se enganchan los dos `excepthook`, de
modo que la proxima vez la traza este en el disco. No se sustituye nada: lo que
iba a la consola sigue yendo, y si no hay consola —aplicacion congelada o
`pythonw`— el fichero es lo unico que queda.
"""
from __future__ import annotations

import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

LOG_NAME = "app_stderr.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
"""Por encima de esto el fichero pasa a `.old` y se empieza otro.

Una traza suelta no ocupa nada, pero `node.py` imprime una por cada 500 que
contesta: un fallo repetido durante dias llenaria el disco sin que nadie mire.
"""


def _rotate(path: Path) -> None:
    try:
        if path.is_file() and path.stat().st_size > MAX_LOG_BYTES:
            path.replace(path.parent / f"{path.name}.old")
    except OSError:
        pass


def _append(path: Path, text: str) -> None:
    """Anade al fichero sin dejar que un fallo de disco tumbe al que escribe.

    Se abre y se cierra en cada escritura, igual que `manager_node_lifecycle`:
    un descriptor abierto durante toda la vida de la aplicacion impide rotar el
    fichero y se pierde entero si el proceso muere mal.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path)
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(text)
    except OSError:
        pass


class _Tee:
    """`stderr` que ademas deja copia en disco.

    `stream` puede ser `None`: con `pythonw` o con la aplicacion congelada no
    hay consola detras, y entonces esto es el unico destino.
    """

    def __init__(self, stream: TextIO | None, path: Path) -> None:
        self._stream = stream
        self._path = path

    def write(self, text: str) -> int:
        if text:
            _append(self._path, text)
        if self._stream is None:
            return len(text)
        try:
            return self._stream.write(text)
        except (OSError, ValueError):
            return len(text)

    def flush(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.flush()
        except (OSError, ValueError):
            pass

    def isatty(self) -> bool:
        return bool(self._stream is not None and self._stream.isatty())

    @property
    def closed(self) -> bool:
        # Sin consola detras el fichero sigue abierto para escribir, y quien
        # pregunte por esto -`io` lo hace- no debe llevarse un AttributeError.
        return bool(getattr(self._stream, "closed", False))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def _stamp(title: str, detail: str) -> str:
    moment = datetime.now().astimezone().isoformat(timespec="seconds")
    return f"\n[{moment}] {title}\n{detail}"


def install(project_dir: Path) -> Path:
    """Engancha los dos `excepthook` y duplica `stderr`. Devuelve el fichero.

    Los hooks previos se conservan y se siguen llamando: `sys.excepthook` es el
    que imprime la traza final del interprete y quitarlo dejaria la consola
    muda. Lo unico que se anade es la copia en disco.
    """
    path = (project_dir / "logs" / LOG_NAME).resolve()
    if isinstance(sys.stderr, _Tee):
        # Instalar dos veces duplicaria cada linea y encadenaria los hooks.
        return path
    sys.stderr = _Tee(sys.stderr, path)

    previous_thread_hook = threading.excepthook
    previous_hook = sys.excepthook

    def thread_hook(args: Any) -> None:
        thread = getattr(args, "thread", None)
        name = getattr(thread, "name", None) or "sin nombre"
        _append(path, _stamp(
            f"Excepcion sin atrapar en el hilo {name}",
            "".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback,
            )),
        ))
        previous_thread_hook(args)

    def hook(exc_type: Any, exc_value: Any, exc_traceback: Any) -> None:
        _append(path, _stamp(
            "Excepcion sin atrapar en el hilo principal",
            "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)),
        ))
        previous_hook(exc_type, exc_value, exc_traceback)

    threading.excepthook = thread_hook
    sys.excepthook = hook
    return path
