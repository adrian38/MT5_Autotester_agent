from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ubs.models import Seed
from ubs.path_utils import resolve_workspace_path
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


STALE_REGRESSION_SQL = """
delete from candidate_regression
where candidate_id in (
    select rg.candidate_id
    from candidate_regression rg
    left join candidates c on c.id=rg.candidate_id
    left join candidate_robustness cr on cr.candidate_id=rg.candidate_id
    left join candidate_final_tick_6m ft6 on ft6.candidate_id=rg.candidate_id
    where {scope_filter} (c.id is null
       or not (
           c.status='accepted'
           and cr.status='accepted'
           and ft6.status='accepted'
       ))
)
"""


STALE_FT6_SQL = """
delete from candidate_final_tick_6m
where candidate_id in (
    select ft6.candidate_id
    from candidate_final_tick_6m ft6
    left join candidates c on c.id = ft6.candidate_id
    left join candidate_robustness cr on cr.candidate_id = ft6.candidate_id
    left join candidate_final_tick ft on ft.candidate_id = ft6.candidate_id
    where {scope_filter} (c.id is null
       or not (
           c.status='accepted'
           and cr.status='accepted'
           and ft.status in ('accepted', 'pending_ohlc_trades')
       ))
)
"""


STALE_FT_SQL = """
delete from candidate_final_tick
where candidate_id in (
    select ft.candidate_id
    from candidate_final_tick ft
    left join candidates c on c.id = ft.candidate_id
    left join candidate_robustness cr on cr.candidate_id = ft.candidate_id
    where {scope_filter} (c.id is null
       or not (
           c.status='accepted'
           and cr.status='accepted'
       ))
)
"""


STALE_ROBUST_SQL = """
delete from candidate_robustness
where candidate_id in (
    select cr.candidate_id
    from candidate_robustness cr
    left join candidates c on c.id = cr.candidate_id
    where {scope_filter} (c.id is null
       or not (
           c.status='accepted'
       ))
)
"""


class MemoryScoresMixin:
    """Puntuaciones de candidatos y preparacion de semillas."""

    def record_score(self, set_path: Path, result: ScoreResult | None, status: str, report_path: Path | None = None, *, metadata: dict[str, object] | None = None) -> None:
        accepted = int(status == "accepted" and bool(result and result.accepted)) if result else None
        score_value = None if status == "no_history" else (result.score if result else None)
        metrics_json = result.to_json() if result else None
        if result and metadata:
            payload = json.loads(metrics_json)
            payload.update(metadata)
            payload["accepted"] = bool(accepted)
            metrics_json = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        self.conn.execute(
            """
            update candidates
            set report_path=?, score=?, accepted=?, metrics_json=?, status=?
            where set_path=?
            """,
            (
                str(report_path) if report_path else (result.report_path if result else None),
                score_value,
                accepted,
                metrics_json,
                status,
                str(set_path),
            ),
        )
        if status != "accepted":
            self.conn.execute(
                """
                delete from candidate_regression
                where candidate_id in (select id from candidates where set_path=?)
                """,
                (str(set_path),),
            )
            self.conn.execute(
                """
                delete from candidate_final_tick_6m
                where candidate_id in (select id from candidates where set_path=?)
                """,
                (str(set_path),),
            )
            self.conn.execute(
                """
                delete from candidate_final_tick
                where candidate_id in (select id from candidates where set_path=?)
                """,
                (str(set_path),),
            )
            self.conn.execute(
                """
                delete from candidate_robustness
                where candidate_id in (select id from candidates where set_path=?)
                """,
                (str(set_path),),
            )
        self._commit()

    def cleanup_stale_stage_rows(self, run_id: int | None = None) -> dict[str, int]:
        scope_filter = ""
        params: tuple[object, ...] = ()
        if run_id is not None:
            scope_filter = "c.run_id=? and"
            params = (int(run_id),)

        cur_regression = self.conn.execute(
            STALE_REGRESSION_SQL.format(scope_filter=scope_filter),
            params,
        )
        cur_6m = self.conn.execute(
            STALE_FT6_SQL.format(scope_filter=scope_filter),
            params,
        )
        cur_ft = self.conn.execute(
            STALE_FT_SQL.format(scope_filter=scope_filter),
            params,
        )
        cur_robust = self.conn.execute(
            STALE_ROBUST_SQL.format(scope_filter=scope_filter),
            params,
        )
        self._commit()
        return {
            "robustness": int(cur_robust.rowcount or 0),
            "final_tick": int(cur_ft.rowcount or 0),
            "final_tick_6m": int(cur_6m.rowcount or 0),
            "regression": int(cur_regression.rowcount or 0),
        }

    def _touch_prepared_seed(self, seed, stat, now, should_eval, stored_path_text) -> None:
        """Refresca los datos de la semilla sin tocar su veredicto terminal."""
        if should_eval:
            self.conn.execute(
                """
                update seed_scores
                set seed_mtime=?, seed_size=?, symbol=?, period=?, family=?, run_strategy=?,
                    report_path=null, score=null, accepted=null, metrics_json=null,
                    status='pending', active=1, last_seen=?, evaluated_at=null
                where seed_path=?
                """,
                (
                    float(stat.st_mtime),
                    int(stat.st_size),
                    seed.symbol,
                    seed.period,
                    seed.family,
                    seed.run_strategy,
                    now,
                    stored_path_text,
                ),
            )
        else:
            self.conn.execute(
                """
                update seed_scores
                set seed_mtime=?, seed_size=?, symbol=?, period=?, family=?, run_strategy=?,
                    active=1, last_seen=?
                where seed_path=?
                """,
                (
                    float(stat.st_mtime),
                    int(stat.st_size),
                    seed.symbol,
                    seed.period,
                    seed.family,
                    seed.run_strategy,
                    now,
                    stored_path_text,
                ),
            )

    def _update_prepared_seed(self, seed, row, stat, now, should_eval,
                              stored_path_text, previous_status) -> None:
        """Actualiza la fila de una semilla que ya estaba en la memoria."""
        if should_eval and previous_status == "no_trades":
            self.conn.execute(
                """
                update seed_scores
                set seed_mtime=?, seed_size=?, symbol=?, period=?, family=?, run_strategy=?,
                    active=1, last_seen=?
                where seed_path=?
                """,
                (
                    float(stat.st_mtime),
                    int(stat.st_size),
                    seed.symbol,
                    seed.period,
                    seed.family,
                    seed.run_strategy,
                    now,
                    stored_path_text,
                ),
            )
        else:
            self._touch_prepared_seed(seed, stat, now, should_eval, stored_path_text)

    def _store_prepared_seed(self, seed, row, stat, now, should_eval,
                             stored_path_text, previous_status) -> None:
        """Inserta o actualiza la fila de la semilla segun su veredicto previo."""
        path_text = str(seed.path)
        if row is None:
            self.conn.execute(
                """
                insert into seed_scores (
                    seed_path, seed_mtime, seed_size, symbol, period, family, run_strategy,
                    status, active, last_seen
                ) values (?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?)
                """,
                (
                    path_text,
                    float(stat.st_mtime),
                    int(stat.st_size),
                    seed.symbol,
                    seed.period,
                    seed.family,
                    seed.run_strategy,
                    now,
                ),
            )
        else:
            self._update_prepared_seed(
                seed, row, stat, now, should_eval, stored_path_text, previous_status,
            )

    def _prepare_one_seed(self, seed, existing, now, pending, force) -> None:
        """Deja una semilla lista para evaluar o conserva su veredicto."""
        try:
            stat = seed.path.stat()
        except OSError:
            return
        path_text = str(seed.path)
        row = existing.get(path_text)
        stored_path_text = str(row["seed_path"]) if row is not None else path_text
        previous_status = str(row["status"] or "") if row is not None else ""
        quarantined_mismatch = (
            previous_status == "report_mismatch"
            and not metrics_have_empty_tester_context(row["metrics_json"])
        ) if row is not None else False
        changed = (
            row is None
            or abs(float(row["seed_mtime"] or 0.0) - float(stat.st_mtime)) > 0.001
            or int(row["seed_size"] or -1) != int(stat.st_size)
            or (
                previous_status not in {"accepted", "rejected", "invalid_seed", "trade_disabled"}
                and not quarantined_mismatch
            )
            or str(row["symbol"] or "").strip().upper() != seed.symbol.strip().upper()
            or str(row["period"] or "").strip().upper() != seed.period.strip().upper()
        )
        should_eval = force or changed
        if should_eval:
            pending.append(seed)
        self._store_prepared_seed(
            seed, row, stat, now, should_eval, stored_path_text, previous_status,
        )

    def prepare_seed_evaluation(self, seeds: list[Seed], *, force: bool = False) -> list[Seed]:
        existing = {
            str(resolve_workspace_path(row["seed_path"])): row
            for row in self.conn.execute("select * from seed_scores").fetchall()
        }
        now = datetime.now().isoformat(timespec="seconds")
        current_paths = {str(seed.path) for seed in seeds}
        self.conn.execute(
            "update seed_scores set active=0 where seed_path not in ({})".format(
                ",".join("?" for _ in current_paths) if current_paths else "''"
            ),
            tuple(current_paths),
        )
        pending: list[Seed] = []
        for seed in seeds:
            self._prepare_one_seed(seed, existing, now, pending, force)
        self.conn.commit()
        return pending

    def prepare_single_seed_evaluation(self, seed: Seed, *, force: bool = False) -> bool:
        try:
            stat = seed.path.stat()
        except OSError:
            return False
        path_text = str(seed.path)
        row = self.conn.execute("select * from seed_scores where seed_path=?", (path_text,)).fetchone()
        now = datetime.now().isoformat(timespec="seconds")
        if row is None:
            self.conn.execute(
                """
                insert into seed_scores (
                    seed_path, seed_mtime, seed_size, symbol, period, family, run_strategy,
                    status, active, last_seen
                ) values (?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?)
                """,
                (
                    path_text,
                    float(stat.st_mtime),
                    int(stat.st_size),
                    seed.symbol,
                    seed.period,
                    seed.family,
                    seed.run_strategy,
                    now,
                ),
            )
        else:
            self.conn.execute(
                """
                update seed_scores
                set seed_mtime=?, seed_size=?, symbol=?, period=?, family=?, run_strategy=?,
                    report_path=null, score=null, accepted=null, metrics_json=null,
                    status='pending', active=1, last_seen=?, evaluated_at=null
                where seed_path=?
                """
                if force
                else """
                update seed_scores
                set seed_mtime=?, seed_size=?, symbol=?, period=?, family=?, run_strategy=?,
                    active=1, last_seen=?
                where seed_path=?
                """,
                (
                    float(stat.st_mtime),
                    int(stat.st_size),
                    seed.symbol,
                    seed.period,
                    seed.family,
                    seed.run_strategy,
                    now,
                    path_text,
                ),
            )
        self.conn.commit()
        return True

    def apply_seed_overrides(self, seeds: list[Seed]) -> list[Seed]:
        rows = self.conn.execute("select seed_path, symbol, period from seed_overrides").fetchall()
        overrides = {
            str(row["seed_path"]): (
                str(row["symbol"] or "").strip().upper(),
                str(row["period"] or "").strip().upper(),
            )
            for row in rows
        }
        if not overrides:
            return seeds
        resolved: list[Seed] = []
        for seed in seeds:
            symbol_override, period_override = overrides.get(str(seed.path), ("", ""))
            resolved.append(
                Seed(
                    path=seed.path,
                    symbol=symbol_override or seed.symbol,
                    period=period_override or seed.period,
                    family=seed.family,
                    run_strategy=seed.run_strategy,
                )
            )
        return resolved

    def record_seed_score(self, seed: Seed, result: ScoreResult | None, status: str, report_path: Path | None = None, *, metadata: dict[str, object] | None = None) -> None:
        accepted = int(status == "accepted" and bool(result and result.accepted)) if result else None
        score_value = None if status == "no_history" else (result.score if result else None)
        metrics_json = result.to_json() if result else None
        if result and metadata:
            payload = json.loads(metrics_json)
            payload.update(metadata)
            payload["accepted"] = bool(accepted)
            metrics_json = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        self.conn.execute(
            """
            update seed_scores
            set symbol=?, period=?, family=?, run_strategy=?,
                report_path=?, score=?, accepted=?, metrics_json=?, status=?, active=1,
                evaluated_at=?
            where seed_path=?
            """,
            (
                seed.symbol,
                seed.period,
                seed.family,
                seed.run_strategy,
                str(report_path) if report_path else (result.report_path if result else None),
                score_value,
                accepted,
                metrics_json,
                status,
                datetime.now().isoformat(timespec="seconds"),
                str(seed.path),
            ),
        )
        self.conn.commit()

    def seed_score_row(self, seed_path: Path) -> sqlite3.Row | None:
        return self.conn.execute(
            "select * from seed_scores where seed_path=? and active=1",
            (str(seed_path),),
        ).fetchone()

    def accepted_candidates_for_robustness(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select c.*, cr.status as robust_status
            from candidates c
            left join candidate_robustness cr on cr.candidate_id = c.id
            where c.run_id=? and c.status='accepted'
            order by c.generation, c.id
            """,
            (run_id,),
        ).fetchall()
