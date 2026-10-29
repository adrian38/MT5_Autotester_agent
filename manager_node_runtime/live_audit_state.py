from __future__ import annotations

from pathlib import Path
from typing import Any

from .live_account_snapshot import RUNNING_SNAPSHOT_STATUSES
from .live_audit_helpers import RUNNING_STATUSES, _safe_state
from .live_audit_report_upload import replace_tester_reports


class LiveAuditStateMixin:
    """Consulta de estado y acceso seguro a artefactos de la auditoría."""

    def is_running(self) -> bool:
        """Indica si una auditoría o un sondeo ocupa las terminales."""
        with self.lock:
            return (
                any(
                    str(item.get("status")) in RUNNING_STATUSES
                    for item in self.states.values()
                )
                or str(self.account_snapshot.get("status") or "")
                in RUNNING_SNAPSHOT_STATUSES
            )

    def all_states(self) -> dict[str, Any]:
        with self.lock:
            return {key: _safe_state(value) for key, value in self.states.items()}

    def state(self, audit_key: str | int) -> dict[str, Any]:
        with self.lock:
            key = str(audit_key)
            raw = self.states.get(key) or {"audit_key": key, "status": "idle"}
            return _safe_state(raw)

    def replace_reports(self, audit_key: str | int, payload: Any) -> dict[str, Any]:
        """Sustituye reportes tester de la ejecución visible, sin relanzar MT5."""
        return replace_tester_reports(self, str(audit_key), payload)

    def artifact_path(self, audit_key: str, audit_id: str, filename: str) -> Path:
        """Resuelve únicamente reportes de la ejecución visible de una auditoría."""
        identifiers = (str(audit_key), str(audit_id))
        if any(
            not value or len(value) > 255
            or not all(char.isalnum() or char in "-_." for char in value)
            for value in identifiers
        ):
            raise ValueError("Identificador de artefacto no válido")
        if not filename or Path(filename).name != filename:
            raise ValueError("Nombre de artefacto no válido")
        allowed = {".htm", ".html", ".png", ".gif", ".jpg", ".jpeg"}
        if Path(filename).suffix.casefold() not in allowed:
            raise ValueError("Tipo de artefacto no permitido")
        with self.lock:
            raw = self.states.get(str(audit_key))
            if not raw or str(raw.get("audit_id") or "") != str(audit_id):
                raise FileNotFoundError("La ejecución solicitada no es la ejecución visible")
        reports_dir = (
            self.runtime_dir / f"audit_{audit_key}" / str(audit_id) / "reports"
        ).resolve()
        path = (reports_dir / filename).resolve()
        if path.parent != reports_dir or not path.is_file():
            raise FileNotFoundError(filename)
        return path
