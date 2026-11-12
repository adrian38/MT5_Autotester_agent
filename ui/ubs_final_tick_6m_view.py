"""Vista de UBS Final Tick 6M."""
from __future__ import annotations


class UBSFinalTick6MViewMixin:
    def _build_ubs_final_tick_6m(self, parent) -> None:
        self._build_final_tick_stage(parent, "six_month")
