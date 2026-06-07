from __future__ import annotations

from datetime import datetime
from pathlib import Path

from run_tests import LOG_DIR, REPORT_DIR, load_experts_from_dir


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
        reports = sorted(REPORT_DIR.glob("*"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
        files = [path for path in reports if path.is_file() and path.suffix.lower() == ".htm"]
        if hasattr(self, "reports_tree"):
            for i, path in enumerate(files[:200]):
                size_kb = max(1, round(path.stat().st_size / 1024))
                date = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                tag = "odd" if i % 2 else "even"
                self.reports_tree.insert("", "end", values=(path.name, date, size_kb), tags=(tag,))
        self.reports_count.set(f"{len(files)}")

    def _refresh_last_log(self) -> None:
        candidates = [path for path in LOG_DIR.glob("*.log") if path.is_file()]
        if not candidates:
            self.last_log_text.set("Sin log")
            return
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        self.last_log_text.set(latest.name)
