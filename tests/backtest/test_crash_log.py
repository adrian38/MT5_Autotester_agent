"""Que una traza no se quede en una consola que luego se cierra.

El 2026-11-01 el hilo del nodo murio y no hubo forma de saber por que: la traza
la imprimio `threading.excepthook` por `stderr`, que con `py app_ui.py` es la
consola del lanzador, y ese proceso ya no existia cuando se miro.
"""
from __future__ import annotations

import io
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from common import crash_log


class CrashLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(self._restore, sys.stderr, sys.excepthook, threading.excepthook)
        self.console = io.StringIO()
        sys.stderr = self.console
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    @staticmethod
    def _restore(stream, hook, thread_hook) -> None:
        sys.stderr = stream
        sys.excepthook = hook
        threading.excepthook = thread_hook

    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    def test_stderr_reaches_both_the_console_and_the_file(self) -> None:
        path = crash_log.install(self.root)
        print("algo ha ido mal", file=sys.stderr)
        self.assertIn("algo ha ido mal", self.console.getvalue())
        self.assertIn("algo ha ido mal", self.read(path))

    def test_a_thread_that_dies_leaves_its_traceback(self) -> None:
        path = crash_log.install(self.root)

        def explode() -> None:
            raise RuntimeError("el hilo del nodo se fue")

        thread = threading.Thread(target=explode, name="mt5-manager-node")
        thread.start()
        thread.join(timeout=5)
        written = self.read(path)
        self.assertIn("mt5-manager-node", written)
        self.assertIn("el hilo del nodo se fue", written)
        self.assertIn("RuntimeError", written)

    def test_the_main_thread_hook_also_writes(self) -> None:
        path = crash_log.install(self.root)
        try:
            raise ValueError("arranque roto")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        written = self.read(path)
        self.assertIn("hilo principal", written)
        self.assertIn("arranque roto", written)

    def test_the_previous_hook_still_runs(self) -> None:
        seen: list[str] = []
        sys.excepthook = lambda *exc_info: seen.append(str(exc_info[1]))
        crash_log.install(self.root)
        try:
            raise ValueError("llega al de antes")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        self.assertEqual(seen, ["llega al de antes"])

    def test_installing_twice_does_not_duplicate_every_line(self) -> None:
        path = crash_log.install(self.root)
        self.assertEqual(crash_log.install(self.root), path)
        sys.stderr.write("una sola vez\n")
        self.assertEqual(self.read(path).count("una sola vez"), 1)

    def test_the_file_rotates_instead_of_growing_without_end(self) -> None:
        path = crash_log.install(self.root)
        with mock.patch.object(crash_log, "MAX_LOG_BYTES", 64):
            sys.stderr.write("x" * 128 + "\n")
            sys.stderr.write("despues de rotar\n")
        self.assertIn("despues de rotar", self.read(path))
        self.assertNotIn("x" * 128, self.read(path))
        self.assertIn("x" * 128, self.read(path.parent / f"{path.name}.old"))

    def test_it_survives_a_console_that_is_not_there(self) -> None:
        sys.stderr = None
        path = crash_log.install(self.root)
        sys.stderr.write("sin consola detras\n")
        self.assertIn("sin consola detras", self.read(path))


if __name__ == "__main__":
    unittest.main()
