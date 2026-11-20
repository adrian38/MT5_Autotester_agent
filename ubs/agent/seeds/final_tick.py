"""Final Tick independiente para las seeds UBS aceptadas."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from ubs.path_utils import resolve_workspace_path
from ubs.agent.final_tick.evaluate import normalize_final_tick_stage
from ubs.agent.final_tick.entry import evaluate_candidate_final_tick
from ubs.agent.seeds.trade_alignment import final_tick_trade_alignment


SEED_FINAL_TICK_TABLES = {
    "probe": "seed_final_tick",
    "six_month": "seed_final_tick_6m",
}
PENDING_ECONOMIC_SAMPLE = "pending_economic_sample"


def seed_final_tick_table_for_stage(stage: object) -> str:
    return SEED_FINAL_TICK_TABLES[normalize_final_tick_stage(stage)]


def _seed_stage_query(stage: str) -> str:
    table = seed_final_tick_table_for_stage(stage)
    probe_join = ""
    if table == "seed_final_tick_6m":
        probe_join = """
            join seed_final_tick probe on probe.seed_id=ss.id
             and probe.status in ('accepted', 'pending_ohlc_trades')
             and probe.seed_mtime=ss.seed_mtime and probe.seed_size=ss.seed_size
        """
    return f"""
        select ss.*, ss.seed_path as set_path, ss.symbol as target_symbol,
               '' as mutated_keys, '' as missing_lot_keys,
               'seed_final_tick' as policy, '' as timeframe_keys,
               ft.status as final_tick_status,
               ft.from_date as final_tick_from_date,
               ft.to_date as final_tick_to_date,
               ft.ohlc_report_path as ft_ohlc_report_path,
               ft.real_tick_report_path as ft_real_tick_report_path,
               ft.ohlc_metrics_json as ft_ohlc_metrics_json,
               ft.real_tick_metrics_json as ft_real_tick_metrics_json,
               ft.similarity_json as ft_similarity_json,
               ft.history_quality as ft_history_quality,
               ft.min_history_quality as ft_min_history_quality,
               ft.ohlc_score as ft_ohlc_score,
               ft.real_tick_score as ft_real_tick_score,
               ft.evaluated_at as ft_evaluated_at
        from seed_scores ss
        {probe_join}
        left join {table} ft on ft.seed_id=ss.id
         and ft.seed_mtime=ss.seed_mtime and ft.seed_size=ss.seed_size
        where ss.active=1 and ss.status='accepted'
        order by upper(ss.symbol), upper(ss.period), lower(ss.seed_path)
    """


def eligible_seed_final_tick_rows(memory, stage: object = "probe") -> list:
    normalized = normalize_final_tick_stage(stage)
    return memory.conn.execute(_seed_stage_query(normalized)).fetchall()


def seed_final_tick_cohort_id(rows: list, stage: str) -> int:
    parts = [normalize_final_tick_stage(stage)]
    parts.extend(
        f"{int(row['id'])}:{float(row['seed_mtime']):.6f}:{int(row['seed_size'])}"
        for row in rows
    )
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) or 1


def economic_sample_pending_check(
    similarity: dict, min_trades: int, trade_alignment: dict | None,
) -> dict | None:
    """Detecta una divergencia economica aislada con muestra todavia pequena."""
    if {str(reason) for reason in similarity.get("reasons", [])} != {"economic_direction"}:
        return None
    checks = similarity.get("checks")
    if not isinstance(checks, dict):
        return None
    economic = checks.get("economic_direction")
    trades = checks.get("trades")
    if not isinstance(economic, dict) or not isinstance(trades, dict):
        return None
    if not economic.get("required") or economic.get("matches", True):
        return None
    if not trades.get("accepted", False):
        return None
    try:
        ohlc_trades = int(float(trades["ohlc"]))
        tick_trades = int(float(trades["real_tick"]))
    except (KeyError, TypeError, ValueError):
        return None
    minimum = max(1, int(min_trades))
    if min(ohlc_trades, tick_trades) >= minimum:
        return None
    if not isinstance(trade_alignment, dict) or not trade_alignment.get("accepted"):
        return None
    return {
        "accepted": False,
        "checked": True,
        "ohlc_trades": ohlc_trades,
        "real_tick_trades": tick_trades,
        "min_trades": minimum,
    }


def _row_trade_alignment(row) -> dict | None:
    ohlc_report = str(row["ft_ohlc_report_path"] or "").strip()
    tick_report = str(row["ft_real_tick_report_path"] or "").strip()
    if not ohlc_report or not tick_report:
        return None
    return final_tick_trade_alignment(
        resolve_workspace_path(ohlc_report), resolve_workspace_path(tick_report),
    )


def seed_economic_sample_review_rows(
    memory, min_trades: int, from_date: str, to_date: str,
) -> list:
    """Filas 6M ya rechazadas que la nueva regla debe ampliar."""
    matches = []
    for row in eligible_seed_final_tick_rows(memory, "six_month"):
        if str(row["final_tick_status"] or "") != "rejected":
            continue
        if (str(row["final_tick_from_date"] or ""), str(row["final_tick_to_date"] or "")) != (from_date, to_date):
            continue
        try:
            similarity = json.loads(str(row["ft_similarity_json"] or "{}"))
        except (TypeError, ValueError):
            continue
        alignment = _row_trade_alignment(row)
        if isinstance(similarity, dict) and economic_sample_pending_check(
            similarity, min_trades, alignment,
        ):
            matches.append(row)
    return matches


def mark_seed_economic_sample_pending(
    memory, rows: list, min_trades: int, retry_from: str, retry_to: str,
) -> int:
    """Reclasifica filas historicas sin borrar sus informes de evidencia."""
    updated = 0
    for row in rows:
        similarity = json.loads(str(row["ft_similarity_json"] or "{}"))
        alignment = _row_trade_alignment(row)
        detail = economic_sample_pending_check(similarity, min_trades, alignment)
        if detail is None:
            continue
        detail.update({"retry_from": retry_from, "retry_to": retry_to})
        similarity.setdefault("checks", {})["trade_alignment"] = alignment
        similarity.setdefault("checks", {})["economic_sample"] = detail
        memory.conn.execute(
            "update seed_final_tick_6m set status=?, accepted=0, similarity_json=? where seed_id=?",
            (PENDING_ECONOMIC_SAMPLE, json.dumps(similarity, sort_keys=True), int(row["id"])),
        )
        updated += 1
    memory.conn.commit()
    return updated


class SeedFinalTickMemoryAdapter:
    """Adapta las seeds al motor Final Tick sin mezclarlas con candidatos."""

    require_economic_direction_similarity = True
    allow_better_tick_drawdown_rescue = True
    automatic_economic_sample_retry = True

    def __init__(self, memory, output_dir: Path, run_id: int = 1) -> None:
        self._memory = memory
        self._output_dir = output_dir / "seed_final_tick_runs"
        self.run_id = int(run_id)
        self.active_final_tick_stage = "probe"
        self._seed_rows: dict[int, object] = {}

    def __getattr__(self, name: str):
        return getattr(self._memory, name)

    def latest_run(self) -> dict[str, object]:
        return {"id": self.run_id, "output_dir": str(self._output_dir)}

    def run_by_id(self, _run_id: int) -> dict[str, object]:
        return self.latest_run()

    def accepted_candidates_for_final_tick(
        self, _run_id: int, *, final_tick_stage: str = "probe"
    ) -> list:
        rows = eligible_seed_final_tick_rows(self._memory, final_tick_stage)
        self._seed_rows = {int(row["id"]): row for row in rows}
        return rows

    def final_tick_similarity_status(
        self, status, ohlc_result, real_tick_result, similarity, args,
    ) -> str:
        """Aplaza una divergencia economica pequena hasta el rango alternativo."""
        if self.active_final_tick_stage != "six_month" or status != "rejected":
            return status
        retry_from = str(getattr(args, "final_tick_ohlc_from_date", "") or "").strip()
        retry_to = str(getattr(args, "final_tick_ohlc_to_date", "") or "").strip()
        current = (str(args.from_date or "").strip(), str(args.to_date or "").strip())
        if not retry_from or not retry_to or current == (retry_from, retry_to):
            return status
        alignment = final_tick_trade_alignment(
            resolve_workspace_path(ohlc_result.report_path),
            resolve_workspace_path(real_tick_result.report_path),
        )
        detail = economic_sample_pending_check(
            similarity,
            int(getattr(args, "final_tick_economic_min_trades", 50)),
            alignment,
        )
        if detail is None:
            return status
        detail.update({"retry_from": retry_from, "retry_to": retry_to})
        similarity.setdefault("checks", {})["trade_alignment"] = alignment
        similarity.setdefault("checks", {})["economic_sample"] = detail
        return PENDING_ECONOMIC_SAMPLE

    def record_candidate_final_tick(
        self, candidate_id, _run_id, status, ohlc_result, real_tick_result,
        ohlc_report_path, real_tick_report_path, similarity_json, history_quality,
        min_history_quality, from_date, to_date, max_net_delta_pct,
        max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct, *,
        max_dd_gap_pp=3.0, final_tick_stage=None,
    ) -> None:
        seed_id = int(candidate_id)
        row = self._seed_rows.get(seed_id) or self._memory.conn.execute(
            "select * from seed_scores where id=?", (seed_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Seed inexistente: {seed_id}")
        stage = final_tick_stage or self.active_final_tick_stage
        table = seed_final_tick_table_for_stage(stage)
        self._memory.conn.execute(
            _seed_final_tick_upsert(table),
            _seed_final_tick_values(
                row, status, ohlc_result, real_tick_result, ohlc_report_path,
                real_tick_report_path, similarity_json, history_quality,
                min_history_quality, from_date, to_date, max_net_delta_pct,
                max_pf_delta_pct, max_dd_delta_pct, max_trades_delta_pct,
                max_dd_gap_pp,
            ),
        )
        if table == "seed_final_tick" and status not in {"accepted", "pending_ohlc_trades"}:
            self._memory.conn.execute(
                "delete from seed_final_tick_6m where seed_id=?", (seed_id,)
            )
        self._memory.conn.commit()


def _seed_final_tick_upsert(table: str) -> str:
    return f"""
        insert into {table} (
            seed_id, status, accepted, ohlc_report_path, real_tick_report_path,
            ohlc_score, real_tick_score, ohlc_metrics_json, real_tick_metrics_json,
            similarity_json, history_quality, min_history_quality, from_date, to_date,
            max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct,
            max_trades_delta_pct, max_dd_gap_pp, seed_mtime, seed_size, evaluated_at
        ) values ({','.join('?' for _ in range(22))})
        on conflict(seed_id) do update set
            status=excluded.status, accepted=excluded.accepted,
            ohlc_report_path=excluded.ohlc_report_path,
            real_tick_report_path=excluded.real_tick_report_path,
            ohlc_score=excluded.ohlc_score, real_tick_score=excluded.real_tick_score,
            ohlc_metrics_json=excluded.ohlc_metrics_json,
            real_tick_metrics_json=excluded.real_tick_metrics_json,
            similarity_json=excluded.similarity_json,
            history_quality=excluded.history_quality,
            min_history_quality=excluded.min_history_quality,
            from_date=excluded.from_date, to_date=excluded.to_date,
            max_net_delta_pct=excluded.max_net_delta_pct,
            max_pf_delta_pct=excluded.max_pf_delta_pct,
            max_dd_delta_pct=excluded.max_dd_delta_pct,
            max_trades_delta_pct=excluded.max_trades_delta_pct,
            max_dd_gap_pp=excluded.max_dd_gap_pp,
            seed_mtime=excluded.seed_mtime, seed_size=excluded.seed_size,
            evaluated_at=excluded.evaluated_at
    """


def _result_path(explicit_path, result) -> str | None:
    if explicit_path:
        return str(explicit_path)
    return result.report_path if result else None


def _seed_final_tick_values(
    row, status, ohlc_result, real_tick_result, ohlc_report_path,
    real_tick_report_path, similarity_json, history_quality, min_history_quality,
    from_date, to_date, max_net_delta_pct, max_pf_delta_pct, max_dd_delta_pct,
    max_trades_delta_pct, max_dd_gap_pp,
) -> tuple:
    return (
        int(row["id"]), status, int(status == "accepted"),
        _result_path(ohlc_report_path, ohlc_result),
        _result_path(real_tick_report_path, real_tick_result),
        ohlc_result.score if ohlc_result else None,
        real_tick_result.score if real_tick_result else None,
        ohlc_result.to_json() if ohlc_result else None,
        real_tick_result.to_json() if real_tick_result else None,
        similarity_json, history_quality, float(min_history_quality),
        str(from_date or "").strip(), str(to_date or "").strip(),
        float(max_net_delta_pct), float(max_pf_delta_pct), float(max_dd_delta_pct),
        float(max_trades_delta_pct), float(max_dd_gap_pp),
        float(row["seed_mtime"]), int(row["seed_size"]),
        datetime.now().isoformat(timespec="seconds"),
    )


def evaluate_seed_final_tick(args, memory, score_config) -> int:
    output_dir = resolve_workspace_path(args.output_dir)
    stage = normalize_final_tick_stage(args.final_tick_stage)
    rows = eligible_seed_final_tick_rows(memory, stage)
    adapter = SeedFinalTickMemoryAdapter(
        memory, output_dir, seed_final_tick_cohort_id(rows, stage)
    )
    args.final_tick_run_id = adapter.run_id
    return evaluate_candidate_final_tick(args, adapter, score_config)
