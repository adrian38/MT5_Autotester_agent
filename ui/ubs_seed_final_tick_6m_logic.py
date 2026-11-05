"""Acciones de la pantalla UBS Seeds Final Tick 6M."""
from __future__ import annotations


class UBSSeedFinalTick6MLogicMixin:
    def _refresh_ubs_seed_final_tick_6m(self) -> None:
        self._refresh_seed_final_tick_stage("six_month")

    def _run_ubs_seed_final_tick_6m(self) -> None:
        self._run_seed_final_tick_stage("six_month")

    def _rerun_ubs_seed_final_tick_6m(self) -> None:
        self._run_seed_final_tick_stage("six_month", pending_only=False)

    def _retry_ubs_seed_final_tick_6m_quality(self) -> None:
        self._run_seed_final_tick_stage("six_month", retry_quality=True)

    def _reconcile_ubs_seed_final_tick_6m(self) -> None:
        self._run_seed_final_tick_stage("six_month", reconcile_only=True)
