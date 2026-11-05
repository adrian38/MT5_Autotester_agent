"""Vista de UBS Seeds Final Tick 6M."""
from __future__ import annotations


class UBSSeedFinalTick6MViewMixin:
    def _build_ubs_seed_final_tick_6m(self, parent) -> None:
        self._build_seed_final_tick_stage(parent, "six_month")
