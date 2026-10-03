from __future__ import annotations

import json
import statistics


class AuditReportFormatter:
    """Formateo compartido de los informes de auditoría UBS."""

    @staticmethod
    def fmt_counts(data: dict[str, int]) -> str:
        return ", ".join(f"{key}={value}" for key, value in sorted(data.items())) or "sin filas"

    @staticmethod
    def stat(values: list[object]) -> str:
        nums: list[float] = []
        for value in values:
            try:
                if value is not None:
                    nums.append(float(value))
            except (TypeError, ValueError):
                pass
        if not nums:
            return "n=0"
        return (
            f"n={len(nums)} min={min(nums):.2f} avg={sum(nums)/len(nums):.2f} "
            f"med={statistics.median(nums):.2f} max={max(nums):.2f}"
        )

    @staticmethod
    def parse_json(raw: object) -> dict[str, object]:
        try:
            data = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def fnum(value: object) -> str:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return ""
