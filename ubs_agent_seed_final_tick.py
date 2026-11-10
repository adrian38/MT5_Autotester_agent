"""Final Tick independiente para las seeds UBS aceptadas."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from ubs.path_utils import resolve_workspace_path
from ubs_agent_final_tick import normalize_final_tick_stage
from ubs_agent_final_tick_entry import evaluate_candidate_final_tick


SEED_FINAL_TICK_TABLES = {
    "probe": "seed_final_tick",
    "six_month": "seed_final_tick_6m",
}


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


class SeedFinalTickMemoryAdapter:
    """Adapta las seeds al motor Final Tick sin mezclarlas con candidatos."""

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
