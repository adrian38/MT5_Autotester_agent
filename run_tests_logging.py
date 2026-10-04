"""Registro asincrono del run y limitadores de acciones del watchdog."""
from __future__ import annotations

import atexit
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from run_tests_base import (
    LOG_DIR,
    RUN_LOG_QUEUE_MAX_BATCHES,
    WATCHDOG_RESTART_MIN_INTERVAL_SECONDS,
    WATCHDOG_TERMINATE_MIN_INTERVAL_SECONDS,
)


class RunLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.last_log_path = LOG_DIR / "last_run.log"
        self.last_log_path.write_text("", encoding="utf-8")
        self._log_file = self.log_path.open("a", encoding="utf-8", buffering=1)
        try:
            self._last_log_file = self.last_log_path.open("a", encoding="utf-8", buffering=1)
        except Exception:
            self._log_file.close()
            raise
        self._queue: queue.Queue[tuple[list[str], list[str]] | object] = queue.Queue(
            maxsize=RUN_LOG_QUEUE_MAX_BATCHES
        )
        self._state_lock = threading.Lock()
        self._closed = False
        self._dropped_messages = 0
        self._write_error_reported = False
        self._sentinel = object()
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="run-log-writer",
            daemon=True,
        )
        self._writer_thread.start()
        atexit.register(self.close)

    def write(self, message: str = "") -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}" if message else ""
        self._enqueue([message], [line])

    def write_many(self, messages: list[str]) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"[{timestamp}] {message}" if message else "" for message in messages]
        self._enqueue(list(messages), lines)

    def _enqueue(self, messages: list[str], lines: list[str]) -> None:
        if not messages:
            return
        with self._state_lock:
            if self._closed:
                return
            try:
                self._queue.put_nowait((messages, lines))
            except queue.Full:
                self._dropped_messages += len(messages)

    def _take_dropped_messages(self) -> int:
        with self._state_lock:
            dropped = self._dropped_messages
            self._dropped_messages = 0
        return dropped

    def _writer_loop(self) -> None:
        try:
            while True:
                item = self._queue.get()
                try:
                    dropped = self._take_dropped_messages()
                    if dropped:
                        warning = f"AVISO LOGGER: se descartaron {dropped} mensajes por saturacion."
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self._write_payload([warning], [f"[{timestamp}] {warning}"])
                    if item is self._sentinel:
                        return
                    messages, lines = item
                    self._write_payload(messages, lines)
                finally:
                    self._queue.task_done()
        finally:
            self._log_file.close()
            self._last_log_file.close()

    def _write_payload(self, messages: list[str], lines: list[str]) -> None:
        try:
            print("\n".join(messages), flush=True)
        except (OSError, UnicodeError):
            pass
        payload = "".join(f"{line}\n" for line in lines)
        try:
            self._log_file.write(payload)
            self._last_log_file.write(payload)
        except (OSError, ValueError) as exc:
            if not self._write_error_reported:
                self._write_error_reported = True
                try:
                    print(f"ERROR LOGGER: no se pudo escribir el log: {exc}", file=sys.stderr, flush=True)
                except (OSError, UnicodeError):
                    pass

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(self._sentinel)
        self._writer_thread.join()
        try:
            atexit.unregister(self.close)
        except Exception:
            pass

class ActionRateLimiter:
    """Reserve process actions in separate time slots across worker threads."""

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._lock = threading.Lock()
        self._next_action_at = 0.0

    def wait_for_turn(self, logger: RunLogger, action: str) -> float:
        with self._lock:
            now = time.monotonic()
            action_at = max(now, self._next_action_at)
            self._next_action_at = action_at + self.min_interval_seconds
        delay = max(0.0, action_at - now)
        if delay > 0.05:
            logger.write(f"{action}: turno escalonado en {delay:.1f}s para evitar una tormenta de procesos.")
            time.sleep(delay)
        return delay

_WATCHDOG_TERMINATE_LIMITER = ActionRateLimiter(WATCHDOG_TERMINATE_MIN_INTERVAL_SECONDS)

_WATCHDOG_RESTART_LIMITER = ActionRateLimiter(WATCHDOG_RESTART_MIN_INTERVAL_SECONDS)
