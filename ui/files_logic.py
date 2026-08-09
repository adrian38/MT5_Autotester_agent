from __future__ import annotations

from datetime import datetime
from pathlib import Path
import heapq
import os

from run_tests import LOG_DIR, REPORT_DIR, load_experts_from_dir


REPORTS_TREE_LIMIT = 200


class FilesLogicMixin:
    def _refresh_experts(self) -> None:
        for item in self.experts_tree.get_children() if hasattr(self, "experts_tree") else []:
            self.experts_tree.delete(item)
        experts: list[str] = []
        root = Path(self.experts_root.get()).expanduser() if self.experts_root.get().strip() else None
        if root and root.exists():
            try:
                experts = load_experts_from_dir(root)
            except OSError:
                experts = []
        if hasattr(self, "experts_tree"):
            for i, expert in enumerate(experts):
                tag = "odd" if i % 2 else "even"
                self.experts_tree.insert("", "end", values=(expert,), tags=(tag,))
        self.experts_count.set(f"{len(experts)}")

    def _refresh_reports(self) -> None:
        if hasattr(self, "reports_tree"):
            for item in self.reports_tree.get_children():
                self.reports_tree.delete(item)
        # `reports/` llega a cientos de miles de ficheros. Ordenar Paths con
        # `exists()`+`stat()` en la clave costaba minutos para pintar 200 filas;
        # `scandir` trae nombre y stat de una pasada y el heap solo retiene 200.
        total = 0
        newest: list[tuple[float, str, int]] = []
        try:
            with os.scandir(REPORT_DIR) as entries:
                for entry in entries:
                    if not entry.name.lower().endswith(".htm"):
                        continue
                    try:
                        if not entry.is_file():
                            continue
                        info = entry.stat()
                    except OSError:
                        continue
                    total += 1
                    item = (info.st_mtime, entry.name, info.st_size)
                    if len(newest) < REPORTS_TREE_LIMIT:
                        heapq.heappush(newest, item)
                    elif item > newest[0]:
                        heapq.heapreplace(newest, item)
        except OSError:
            newest = []
            total = 0
        if hasattr(self, "reports_tree"):
            for i, (mtime, name, size) in enumerate(sorted(newest, reverse=True)):
                size_kb = max(1, round(size / 1024))
                date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                tag = "odd" if i % 2 else "even"
                self.reports_tree.insert("", "end", values=(name, date, size_kb), tags=(tag,))
        self.reports_count.set(f"{total}")

    def _refresh_last_log(self) -> None:
        candidates = [path for path in LOG_DIR.glob("*.log") if path.is_file()]
        if not candidates:
            self.last_log_text.set("Sin log")
            return
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        self.last_log_text.set(latest.name)
