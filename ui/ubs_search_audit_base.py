"""Estados terminales de la auditoria de run y su recuento."""
from __future__ import annotations



AUDIT_FINAL_STATUSES = {"accepted", "rejected"}


def audit_nonfinal_count(
    status_counts: dict[str, int],
    *,
    additional_final_statuses: set[str] | frozenset[str] = frozenset(),
) -> int:
    """Count stored stage rows that have not reached a final pass/fail state."""
    final_statuses = AUDIT_FINAL_STATUSES | {
        str(status or "").strip().lower() for status in additional_final_statuses
    }
    return sum(
        int(count or 0)
        for status, count in status_counts.items()
        if str(status or "").strip().lower() not in final_statuses
    )
