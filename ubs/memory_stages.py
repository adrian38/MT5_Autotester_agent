from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ubs.score import ScoreResult


FINAL_TICK_STAGE_TABLES = {
    "probe": "candidate_final_tick",
    "six_month": "candidate_final_tick_6m",
}
FINAL_TICK_6M_PROBE_ELIGIBLE_STATUSES = ("accepted", "pending_ohlc_trades")


def metrics_have_empty_tester_context(metrics_json: object) -> bool:
    """Return whether stored score metrics came from an unusable MT5 report."""
    try:
        metrics = json.loads(str(metrics_json or "{}"))
    except (TypeError, ValueError):
        return False
    symbol = str(metrics.get("symbol") or "").strip()
    timeframe = str(metrics.get("timeframe") or "").strip().upper()
    try:
        trades = int(metrics.get("trades") or 0)
    except (TypeError, ValueError):
        trades = 0
    return trades <= 0 and (not symbol or timeframe in {"", "M0"})


def final_tick_table_for_stage(stage: str | None) -> str:
    key = str(stage or "probe").strip().lower().replace("-", "_")
    if key in {"6m", "sixmonth", "six_month"}:
        key = "six_month"
    if key not in FINAL_TICK_STAGE_TABLES:
        raise ValueError(f"Etapa Final Tick desconocida: {stage}")
    return FINAL_TICK_STAGE_TABLES[key]


FINAL_TICK_UPSERT_SQL = """
insert into {final_tick_table} (
    candidate_id, run_id, status, accepted,
    ohlc_report_path, real_tick_report_path,
    ohlc_score, real_tick_score,
    ohlc_metrics_json, real_tick_metrics_json, similarity_json,
    history_quality, min_history_quality, from_date, to_date,
    max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct,
    evaluated_at
) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
on conflict(candidate_id) do update set
    run_id=excluded.run_id,
    status=excluded.status,
    accepted=excluded.accepted,
    ohlc_report_path=excluded.ohlc_report_path,
    real_tick_report_path=excluded.real_tick_report_path,
    ohlc_score=excluded.ohlc_score,
    real_tick_score=excluded.real_tick_score,
    ohlc_metrics_json=excluded.ohlc_metrics_json,
    real_tick_metrics_json=excluded.real_tick_metrics_json,
    similarity_json=excluded.similarity_json,
    history_quality=excluded.history_quality,
    min_history_quality=excluded.min_history_quality,
    from_date=excluded.from_date,
    to_date=excluded.to_date,
    max_net_delta_pct=excluded.max_net_delta_pct,
    max_pf_delta_pct=excluded.max_pf_delta_pct,
    max_dd_delta_pct=excluded.max_dd_delta_pct,
    max_trades_delta_pct=excluded.max_trades_delta_pct,
    evaluated_at=excluded.evaluated_at
"""


class MemoryStagesMixin:
    """Filas de robustez, Final Tick y regresiva de cada candidato."""

    def accepted_candidates_for_final_tick(
        self,
        run_id: int,
        *,
        final_tick_stage: str = "probe",
    ) -> list[sqlite3.Row]:
        final_tick_table = final_tick_table_for_stage(final_tick_stage)
        probe_join = ""
        if final_tick_table != "candidate_final_tick":
            probe_join = (
                "join candidate_final_tick probe_ft on probe_ft.candidate_id = c.id "
                "and probe_ft.status in ('accepted', 'pending_ohlc_trades')"
            )
        return self.conn.execute(
            f"""
            select
                c.*,
                cr.status as robust_status,
                cr.report_path as robust_report_path,
                ft.status as final_tick_status,
                ft.from_date as final_tick_from_date,
                ft.to_date as final_tick_to_date,
                ft.ohlc_report_path as ft_ohlc_report_path,
                ft.ohlc_metrics_json as ft_ohlc_metrics_json
            from candidates c
            join candidate_robustness cr on cr.candidate_id = c.id
            {probe_join}
            left join {final_tick_table} ft on ft.candidate_id = c.id
            where c.run_id=? and c.status='accepted' and cr.status='accepted'
            order by c.generation, c.id
            """,
            (run_id,),
        ).fetchall()

    def accepted_candidates_for_regression(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select
                c.*,
                cr.status as robust_status,
                ft6.status as final_tick_6m_status,
                rg.status as regression_status,
                rg.report_path as regression_report_path,
                rg.from_date as regression_from_date,
                rg.to_date as regression_to_date
            from candidates c
            join candidate_robustness cr
              on cr.candidate_id = c.id
             and cr.status = 'accepted'
            join candidate_final_tick_6m ft6
              on ft6.candidate_id = c.id
             and ft6.status = 'accepted'
            left join candidate_regression rg on rg.candidate_id = c.id
            where c.run_id=? and c.status='accepted'
            order by c.generation, c.id
            """,
            (run_id,),
        ).fetchall()

    def regression_rows_for_rescore(self, run_id: int | None = None) -> list[sqlite3.Row]:
        where = "and c.run_id=?" if run_id else ""
        params = (int(run_id),) if run_id else ()
        return self.conn.execute(
            f"""
            select c.*, rg.report_path as regression_report_path, rg.status as regression_status
            from candidates c
            join candidate_regression rg on rg.candidate_id = c.id
            where rg.status in ('accepted', 'rejected', 'no_trades')
              and coalesce(rg.report_path, '') != ''
              {where}
            order by c.run_id, c.generation, c.id
            """,
            params,
        ).fetchall()

    def record_candidate_robustness(
        self,
        candidate_id: int,
        run_id: int,
        result: ScoreResult | None,
        status: str,
        report_path: Path | None,
        from_date: str,
        to_date: str,
        positive_bonus: float,
        negative_bonus: float,
        degradation: dict[str, object] | None = None,
    ) -> None:
        accepted = int(status == "accepted" and bool(result and result.accepted)) if result else None
        self.conn.execute(
            """
            insert into candidate_robustness (
                candidate_id, run_id, status, report_path, score, accepted,
                metrics_json, degradation_json, from_date, to_date,
                positive_bonus, negative_bonus, evaluated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(candidate_id) do update set
                run_id=excluded.run_id,
                status=excluded.status,
                report_path=excluded.report_path,
                score=excluded.score,
                accepted=excluded.accepted,
                metrics_json=excluded.metrics_json,
                degradation_json=excluded.degradation_json,
                from_date=excluded.from_date,
                to_date=excluded.to_date,
                positive_bonus=excluded.positive_bonus,
                negative_bonus=excluded.negative_bonus,
                evaluated_at=excluded.evaluated_at
            """,
            (
                candidate_id,
                run_id,
                status,
                str(report_path) if report_path else (result.report_path if result else None),
                result.score if result else None,
                accepted,
                result.to_json() if result else None,
                json.dumps(degradation or {}, ensure_ascii=True, sort_keys=True),
                from_date.strip(),
                to_date.strip(),
                float(positive_bonus),
                float(negative_bonus),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        if status != "accepted":
            self.conn.execute("delete from candidate_regression where candidate_id=?", (int(candidate_id),))
            self.conn.execute("delete from candidate_final_tick_6m where candidate_id=?", (int(candidate_id),))
            self.conn.execute("delete from candidate_final_tick where candidate_id=?", (int(candidate_id),))
        self._commit()

    def record_candidate_final_tick(
        self,
        candidate_id: int,
        run_id: int,
        status: str,
        ohlc_result: ScoreResult | None,
        real_tick_result: ScoreResult | None,
        ohlc_report_path: Path | None,
        real_tick_report_path: Path | None,
        similarity_json: str | None,
        history_quality: float | None,
        min_history_quality: float,
        from_date: str,
        to_date: str,
        max_net_delta_pct: float,
        max_pf_delta_pct: float,
        max_dd_delta_pct: float,
        max_trades_delta_pct: float,
        *,
        final_tick_stage: str | None = None,
    ) -> None:
        accepted = int(status == "accepted")
        final_tick_table = final_tick_table_for_stage(final_tick_stage or self.active_final_tick_stage)
        self.conn.execute(
            FINAL_TICK_UPSERT_SQL.format(final_tick_table=final_tick_table),
            (
                candidate_id,
                run_id,
                status,
                accepted,
                str(ohlc_report_path) if ohlc_report_path else (ohlc_result.report_path if ohlc_result else None),
                str(real_tick_report_path) if real_tick_report_path else (
                    real_tick_result.report_path if real_tick_result else None
                ),
                ohlc_result.score if ohlc_result else None,
                real_tick_result.score if real_tick_result else None,
                ohlc_result.to_json() if ohlc_result else None,
                real_tick_result.to_json() if real_tick_result else None,
                similarity_json,
                history_quality,
                float(min_history_quality),
                from_date.strip(),
                to_date.strip(),
                float(max_net_delta_pct),
                float(max_pf_delta_pct),
                float(max_dd_delta_pct),
                float(max_trades_delta_pct),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        if final_tick_table == "candidate_final_tick" and status not in {"accepted", "pending_ohlc_trades"}:
            self.conn.execute("delete from candidate_regression where candidate_id=?", (int(candidate_id),))
            self.conn.execute("delete from candidate_final_tick_6m where candidate_id=?", (int(candidate_id),))
        if final_tick_table == "candidate_final_tick_6m" and status != "accepted":
            self.conn.execute("delete from candidate_regression where candidate_id=?", (int(candidate_id),))
        self._commit()

    def record_candidate_regression(
        self,
        candidate_id: int,
        run_id: int,
        status: str,
        result: ScoreResult | None,
        report_path: Path | None,
        details_json: str | None,
        from_date: str,
        to_date: str,
        positive_points: float,
        negative_points: float,
        points_applied: float,
    ) -> None:
        normalized_status = str(status or "").strip().lower()
        accepted_value = 1 if normalized_status == "accepted" else (
            0 if normalized_status in {"rejected", "no_trades"} else None
        )
        self.conn.execute(
            """
            insert into candidate_regression (
                candidate_id, run_id, status, accepted, report_path, score,
                metrics_json, details_json, from_date, to_date,
                positive_points, negative_points, points_applied, evaluated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(candidate_id) do update set
                run_id=excluded.run_id,
                status=excluded.status,
                accepted=excluded.accepted,
                report_path=excluded.report_path,
                score=excluded.score,
                metrics_json=excluded.metrics_json,
                details_json=excluded.details_json,
                from_date=excluded.from_date,
                to_date=excluded.to_date,
                positive_points=excluded.positive_points,
                negative_points=excluded.negative_points,
                points_applied=excluded.points_applied,
                evaluated_at=excluded.evaluated_at
            """,
            (
                int(candidate_id),
                int(run_id),
                str(status),
                accepted_value,
                str(report_path) if report_path else (result.report_path if result else None),
                result.score if result else None,
                result.to_json() if result else None,
                details_json,
                str(from_date or "").strip(),
                str(to_date or "").strip(),
                float(positive_points),
                float(negative_points),
                float(points_applied),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self._commit()
