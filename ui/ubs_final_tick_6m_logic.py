"""Acciones de la pantalla UBS Final Tick 6M."""
from __future__ import annotations


class UBSFinalTick6MLogicMixin:
    def _refresh_ubs_final_tick_6m(self) -> None:
        self._refresh_final_tick_stage("six_month")

    def _refresh_ubs_final_tick_6m_panel(self) -> None:
        self._refresh_final_tick_panel("six_month")

    def _run_ubs_final_tick_6m_for_latest_run(self) -> bool:
        return self._run_ubs_final_tick_for_latest_run(
            pending_only=True, final_tick_stage="six_month",
        )

    def _rerun_ubs_final_tick_6m_for_latest_run(self) -> bool:
        return self._run_ubs_final_tick_for_latest_run(
            pending_only=False, final_tick_stage="six_month",
        )

    def _retry_ubs_final_tick_6m_pending_quality(self) -> bool:
        return self._retry_ubs_final_tick_pending_quality(final_tick_stage="six_month")

    def _manual_accept_selected_ubs_final_tick_6m(self) -> None:
        self._manual_mark_final_tick("six_month", "accepted")

    def _manual_reject_selected_ubs_final_tick_6m(self) -> None:
        self._manual_mark_final_tick("six_month", "rejected")

    def _open_selected_ubs_final_tick_6m_set(self) -> None:
        self._open_final_tick_path("six_month", "set")

    def _open_selected_ubs_final_tick_6m_ohlc_report(self) -> None:
        self._open_final_tick_path("six_month", "ohlc_report")

    def _open_selected_ubs_final_tick_6m_real_report(self) -> None:
        self._open_final_tick_path("six_month", "real_report")
