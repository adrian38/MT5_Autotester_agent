"""Barra de carga en consola para el arranque de la UI.

El arranque hace trabajo pesado en el hilo principal (migracion legacy,
construccion de pestanas, refresco inicial) y hasta que termina la ventana no
responde. Sin feedback eso es indistinguible de un cuelgue, asi que cada fase
se anuncia por consola con su tiempo.

Uso:

    progress = StartupProgress(["Ajustes", "Migracion", "Interfaz"])
    progress.advance("Ajustes")
    ...
    progress.advance("Migracion")
    ...
    progress.done()

`advance()` cierra la fase anterior (imprimiendo su duracion) y abre la
siguiente. Si algo revienta, `fail()` deja constancia de en que fase fue.
"""

from __future__ import annotations

from collections.abc import Sequence
import sys
import time


_CURRENT: "StartupProgress | None" = None


def current() -> "StartupProgress | None":
    """Ultima barra creada.

    Permite informar del fallo desde `main()` cuando el constructor de la UI
    revienta y por tanto no hay instancia a la que preguntar.
    """

    return _CURRENT


class StartupProgress:
    """Dibuja una barra de progreso de una sola linea durante el arranque."""

    def __init__(
        self,
        steps: Sequence[str],
        *,
        width: int = 28,
        stream: object | None = None,
        enabled: bool = True,
    ) -> None:
        global _CURRENT
        _CURRENT = self
        self._steps = [str(step) for step in steps]
        self._width = max(int(width), 4)
        self._stream = stream if stream is not None else sys.stdout
        # Sin consola (build con PyInstaller --noconsole) `sys.stdout` puede ser
        # None: en ese caso la barra se desactiva en lugar de romper el arranque.
        self._enabled = bool(enabled and self._stream is not None)
        self._index = 0
        self._label = ""
        self._phase_started = 0.0
        self._started = time.perf_counter()
        self._finished = False

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._started

    def _write(self, text: str) -> None:
        if not self._enabled:
            return
        try:
            self._stream.write(text)  # type: ignore[union-attr]
            self._stream.flush()  # type: ignore[union-attr]
        except (OSError, ValueError):
            # Consola cerrada a mitad de arranque: seguimos sin barra.
            self._enabled = False

    def _bar(self, done: int) -> str:
        total = len(self._steps) or 1
        filled = int(self._width * min(done, total) / total)
        return "#" * filled + "-" * (self._width - filled)

    def _close_phase(self) -> None:
        if not self._label:
            return
        seconds = time.perf_counter() - self._phase_started
        self._index += 1
        percent = 100.0 * self._index / (len(self._steps) or 1)
        self._write(
            f"\r[{self._bar(self._index)}] {percent:3.0f}%  {self._label} ({seconds:.1f}s)\n"
        )
        self._label = ""

    def advance(self, label: str) -> None:
        """Cierra la fase en curso y abre `label`."""

        self._close_phase()
        self._label = str(label)
        self._phase_started = time.perf_counter()
        percent = 100.0 * self._index / (len(self._steps) or 1)
        self._write(f"\r[{self._bar(self._index)}] {percent:3.0f}%  {self._label}...")

    def note(self, text: str) -> None:
        """Mensaje suelto sin cerrar la fase (p. ej. un aviso no fatal)."""

        if not self._label:
            self._write(f"{text}\n")
            return
        self._write(f"\r{' ' * (self._width + 48)}\r{text}\n")
        percent = 100.0 * self._index / (len(self._steps) or 1)
        self._write(f"\r[{self._bar(self._index)}] {percent:3.0f}%  {self._label}...")

    def fail(self, exc: BaseException) -> None:
        label = self._label or "arranque"
        self._label = ""
        self._write(f"\rERROR en '{label}': {type(exc).__name__}: {exc}\n")

    def done(self) -> None:
        if self._finished:
            return
        self._close_phase()
        self._finished = True
        self._write(f"[{self._bar(len(self._steps))}] 100%  listo en {self.elapsed:.1f}s\n")
