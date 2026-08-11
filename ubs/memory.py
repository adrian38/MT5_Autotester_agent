from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ubs.db import connect_memory
from ubs.models import Seed, Variant
from ubs.path_utils import resolve_workspace_path, workspace_path_exists
from ubs.score import ScoreResult
from ubs.selection import SelectionFitnessModel, SelectionPrediction
from ubs.weights import (
    FeedbackSignal,
    TIMEFRAME_PATCH_KEYS,
    candidate_group_key,
    probability_feedback_signals,
    seed_group_key,
)


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


class AgentMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.active_final_tick_stage = "probe"
        self._defer_commits = 0
        self._selection_fitness_models: dict[int | None, SelectionFitnessModel | None] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = connect_memory(self.path, enable_wal=True)
        self._init()

    def close(self) -> None:
        self.conn.close()

    def _commit(self) -> None:
        if self._defer_commits <= 0:
            self.conn.commit()

    @contextmanager
    def batch_updates(self):
        """Commit a group of row updates atomically instead of per row."""

        self._defer_commits += 1
        try:
            yield
        except Exception:
            self.conn.rollback()
            raise
        else:
            if self._defer_commits == 1:
                self.conn.commit()
        finally:
            self._defer_commits -= 1

    def _init(self) -> None:
        self.conn.executescript(
            """
            create table if not exists runs (
                id integer primary key autoincrement,
                created_at text not null,
                source_dir text not null,
                output_dir text not null,
                generations integer not null,
                variants_per_seed integer not null,
                max_seeds integer not null,
                execute_backtests integer not null,
                dry_run integer not null,
                hidden integer not null default 0,
                config_json text not null default ''
            );
            create table if not exists candidates (
                id integer primary key autoincrement,
                run_id integer not null,
                generation integer not null,
                seed_path text not null,
                set_path text not null,
                symbol text not null,
                target_symbol text not null,
                period text not null,
                family text not null,
                run_strategy text not null,
                mutated_keys text not null,
                timeframe_keys text not null default '',
                mutation_details_json text not null default '',
                missing_lot_keys text not null,
                policy text not null,
                report_path text,
                score real,
                accepted integer,
                metrics_json text,
                status text not null,
                created_at text not null
            );
            create table if not exists seed_scores (
                id integer primary key autoincrement,
                seed_path text not null unique,
                seed_mtime real not null,
                seed_size integer not null,
                symbol text not null,
                period text not null,
                family text not null,
                run_strategy text not null,
                report_path text,
                score real,
                accepted integer,
                metrics_json text,
                status text not null,
                active integer not null default 1,
                last_seen text not null,
                evaluated_at text
            );
            create table if not exists seed_overrides (
                seed_path text primary key,
                symbol text not null default '',
                period text not null default '',
                updated_at text not null
            );
            create table if not exists candidate_robustness (
                candidate_id integer primary key,
                run_id integer not null,
                status text not null,
                report_path text,
                score real,
                accepted integer,
                metrics_json text,
                degradation_json text not null default '',
                from_date text not null default '',
                to_date text not null default '',
                positive_bonus real not null default 70.0,
                negative_bonus real not null default -70.0,
                evaluated_at text not null
            );
            create table if not exists candidate_final_tick (
                candidate_id integer primary key,
                run_id integer not null,
                status text not null,
                accepted integer,
                ohlc_report_path text,
                real_tick_report_path text,
                ohlc_score real,
                real_tick_score real,
                ohlc_metrics_json text,
                real_tick_metrics_json text,
                similarity_json text,
                history_quality real,
                min_history_quality real not null default 80.0,
                from_date text not null default '',
                to_date text not null default '',
                max_net_delta_pct real not null default 35.0,
                max_pf_delta_pct real not null default 35.0,
                max_dd_delta_pct real not null default 35.0,
                max_trades_delta_pct real not null default 35.0,
                evaluated_at text not null
            );
            create table if not exists candidate_final_tick_6m (
                candidate_id integer primary key,
                run_id integer not null,
                status text not null,
                accepted integer,
                ohlc_report_path text,
                real_tick_report_path text,
                ohlc_score real,
                real_tick_score real,
                ohlc_metrics_json text,
                real_tick_metrics_json text,
                similarity_json text,
                history_quality real,
                min_history_quality real not null default 80.0,
                from_date text not null default '',
                to_date text not null default '',
                max_net_delta_pct real not null default 35.0,
                max_pf_delta_pct real not null default 35.0,
                max_dd_delta_pct real not null default 35.0,
                max_trades_delta_pct real not null default 35.0,
                evaluated_at text not null
            );
            create table if not exists candidate_regression (
                candidate_id integer primary key,
                run_id integer not null,
                status text not null,
                accepted integer,
                report_path text,
                score real,
                metrics_json text,
                details_json text,
                from_date text not null default '2017.01.01',
                to_date text not null default '2019.12.31',
                positive_points real not null default 80.0,
                negative_points real not null default -100.0,
                points_applied real not null default 0.0,
                evaluated_at text not null
            );
            create table if not exists generation_seed_selection (
                run_id integer not null,
                generation integer not null,
                rank integer not null,
                seed_path text not null,
                symbol text not null,
                period text not null,
                family text not null,
                run_strategy text not null,
                selection_score real not null,
                asset_weight real not null,
                timeframe_weight real not null,
                diversity real not null,
                created_at text not null,
                primary key (run_id, generation, rank)
            );
            """
        )
        self._ensure_column("runs", "hidden", "integer not null default 0")
        self._ensure_column("runs", "config_json", "text not null default ''")
        self._ensure_column("candidates", "timeframe_keys", "text not null default ''")
        self._ensure_column("candidates", "mutation_details_json", "text not null default ''")
        self._ensure_column("candidate_robustness", "degradation_json", "text not null default ''")
        self._ensure_column("generation_seed_selection", "fitness_probability", "real not null default 0.0")
        self._ensure_column("generation_seed_selection", "fitness_weight", "real not null default 0.0")
        self._ensure_column("generation_seed_selection", "fitness_evidence", "real not null default 0.0")
        self._detach_history_probe_runs()
        self.conn.execute(
            """
            update seed_scores
            set status='report_mismatch', accepted=null
            where status in ('accepted', 'rejected')
              and (upper(symbol)='UNKNOWN' or upper(period)='UNKNOWN')
            """
        )
        self._reclassify_empty_tester_contexts()
        self._reclassify_legacy_real_tick_no_history()
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in self.conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            self.conn.execute(f"alter table {table} add column {column} {definition}")

    def _detach_history_probe_runs(self) -> None:
        rows = self.conn.execute("select id, config_json from runs").fetchall()
        probe_run_ids: list[int] = []
        for row in rows:
            try:
                data = json.loads(row["config_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("mode") == "history_probe":
                probe_run_ids.append(int(row["id"]))
        if not probe_run_ids:
            return
        placeholders = ",".join("?" for _ in probe_run_ids)
        self.conn.execute(
            f"update candidates set run_id=0, generation=0 where run_id in ({placeholders})",
            tuple(probe_run_ids),
        )
        self.conn.execute(f"delete from runs where id in ({placeholders})", tuple(probe_run_ids))

    def _reclassify_empty_tester_contexts(self) -> None:
        migrations = (
            ("candidates", ("no_trades",), "report_mismatch"),
            (
                "seed_scores",
                ("no_trades", "report_mismatch", "pending_tester_context"),
                "pending_tester_context",
            ),
        )
        for table, statuses, target_status in migrations:
            placeholders = ",".join("?" for _ in statuses)
            rows = self.conn.execute(
                f"""
                select id, metrics_json
                from {table}
                where status in ({placeholders}) and coalesce(metrics_json, '') != ''
                """,
                statuses,
            ).fetchall()
            ids: list[int] = []
            for row in rows:
                if metrics_have_empty_tester_context(row["metrics_json"]):
                    ids.append(int(row["id"]))
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self.conn.execute(
                    f"update {table} set status=?, accepted=null where id in ({placeholders})",
                    (target_status, *ids),
                )

    def _reclassify_legacy_real_tick_no_history(self) -> int:
        """Migrate transient Model=4 sync failures stored as final rejections."""
        migrated = 0
        migrated_at = datetime.now().isoformat(timespec="seconds")
        for table in FINAL_TICK_STAGE_TABLES.values():
            rows = self.conn.execute(
                f"""
                select candidate_id, similarity_json, history_quality, min_history_quality
                from {table}
                where status='rejected'
                  and coalesce(similarity_json, '') != ''
                """
            ).fetchall()
            for row in rows:
                try:
                    context = json.loads(row["similarity_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(context, dict):
                    continue
                reasons = context.get("reasons")
                history = context.get("history")
                if (
                    not isinstance(reasons, list)
                    or "real_tick_no_history" not in reasons
                    or not isinstance(history, dict)
                    or history.get("tick_download_failed") is not True
                ):
                    continue

                context["accepted"] = False
                context["technical_failure"] = True
                context.setdefault("history_quality", row["history_quality"])
                context.setdefault("min_history_quality", row["min_history_quality"])
                history["retryable"] = True
                history["failure_type"] = "tick_history_sync"
                history["recommendation"] = (
                    "reintentar tras estabilizar la conexion MT5; "
                    "no asumir ausencia de historico del broker"
                )
                context["status_audit"] = {
                    "classification": "transient_tick_sync_failure",
                    "migrated_at": migrated_at,
                    "migrated_from_status": "rejected",
                }
                self.conn.execute(
                    f"""
                    update {table}
                    set status='pending_history_quality',
                        accepted=0,
                        real_tick_score=null,
                        real_tick_metrics_json=null,
                        similarity_json=?
                    where candidate_id=? and status='rejected'
                    """,
                    (
                        json.dumps(context, ensure_ascii=True, sort_keys=True),
                        int(row["candidate_id"]),
                    ),
                )
                migrated += 1
        return migrated

    def create_run(
        self,
        source_dir: Path,
        output_dir: Path,
        generations: int,
        variants_per_seed: int,
        max_seeds: int,
        execute_backtests: bool,
        dry_run: bool,
        config: dict[str, object] | None = None,
    ) -> int:
        config_json = json.dumps(config or {}, ensure_ascii=True, sort_keys=True)
        cur = self.conn.execute(
            """
            insert into runs (
                created_at, source_dir, output_dir, generations, variants_per_seed,
                max_seeds, execute_backtests, dry_run, config_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                str(source_dir),
                str(output_dir),
                generations,
                variants_per_seed,
                max_seeds,
                int(execute_backtests),
                int(dry_run),
                config_json,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_variant(self, run_id: int, generation: int, variant: Variant, status: str = "generated") -> None:
        self.conn.execute(
            """
            insert into candidates (
                run_id, generation, seed_path, set_path, symbol, target_symbol, period,
                family, run_strategy, mutated_keys, timeframe_keys, mutation_details_json,
                missing_lot_keys, policy, status, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                generation,
                str(variant.seed.path),
                str(variant.path),
                variant.seed.symbol,
                variant.target_symbol,
                variant.target_period,
                variant.seed.family,
                variant.seed.run_strategy,
                ";".join(variant.mutated_keys),
                ";".join(variant.timeframe_keys),
                json.dumps(tuple(variant.mutation_details), ensure_ascii=True, sort_keys=True),
                ";".join(variant.missing_lot_keys),
                variant.policy,
                status,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

    def record_seed_selection(
        self,
        run_id: int,
        generation: int,
        ranked_seeds: list[tuple[float, Seed, float, float, float]],
        fitness_predictions: dict[str, SelectionPrediction] | None = None,
    ) -> None:
        fitness_predictions = fitness_predictions or {}
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "delete from generation_seed_selection where run_id=? and generation=?",
            (run_id, generation),
        )
        self.conn.executemany(
            """
            insert into generation_seed_selection (
                run_id, generation, rank, seed_path, symbol, period, family, run_strategy,
                selection_score, asset_weight, timeframe_weight, diversity,
                fitness_probability, fitness_weight, fitness_evidence, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    generation,
                    rank,
                    str(seed.path),
                    seed.symbol,
                    seed.period,
                    seed.family,
                    seed.run_strategy,
                    float(selection_score),
                    float(asset_weight),
                    float(timeframe_weight),
                    float(diversity),
                    float(fitness_predictions.get(str(seed.path), SelectionPrediction(0.0, 0.0, 0.0)).probability),
                    float(fitness_predictions.get(str(seed.path), SelectionPrediction(0.0, 0.0, 0.0)).weight),
                    float(fitness_predictions.get(str(seed.path), SelectionPrediction(0.0, 0.0, 0.0)).evidence),
                    now,
                )
                for rank, (selection_score, seed, asset_weight, timeframe_weight, diversity)
                in enumerate(ranked_seeds, start=1)
            ],
        )
        self.conn.commit()

    def selection_fitness_model(self, *, exclude_run_id: int | None = None) -> SelectionFitnessModel | None:
        if exclude_run_id in self._selection_fitness_models:
            return self._selection_fitness_models[exclude_run_id]
        params: tuple[object, ...] = ()
        run_filter = ""
        if exclude_run_id is not None:
            run_filter = "and c.run_id < ?"
            params = (int(exclude_run_id),)
        rows = self.conn.execute(
            f"""
            select
                c.run_id, c.period, c.score, c.metrics_json, c.status,
                cr.status as robust_status,
                ft.status as final_tick_status,
                ft6.status as final_tick_6m_status
            from candidates c
            left join candidate_robustness cr on cr.candidate_id = c.id
            left join candidate_final_tick ft on ft.candidate_id = c.id
            left join candidate_final_tick_6m ft6 on ft6.candidate_id = c.id
            where c.status='accepted'
              and c.score is not null
              and coalesce(c.metrics_json, '') != ''
              {run_filter}
            """,
            params,
        ).fetchall()
        model = SelectionFitnessModel.train(rows)
        self._selection_fitness_models[exclude_run_id] = model
        return model

    def seed_selection_predictions(
        self,
        seeds: list[Seed],
        *,
        exclude_run_id: int | None = None,
    ) -> dict[str, SelectionPrediction]:
        model = self.selection_fitness_model(exclude_run_id=exclude_run_id)
        if model is None:
            return {str(seed.path): SelectionPrediction(0.0, 0.0, 0.0) for seed in seeds}
        feature_rows = self._selection_feature_rows(str(seed.path) for seed in seeds)
        result: dict[str, SelectionPrediction] = {}
        for seed in seeds:
            path = str(seed.path)
            row = feature_rows.get(path)
            result[path] = (
                model.predict(row["score"], row["metrics_json"], row["period"])
                if row is not None
                else SelectionPrediction(model.prior_probability, 0.0, 0.0)
            )
        return result

    def _selection_feature_rows(self, paths: Iterable[str]) -> dict[str, sqlite3.Row]:
        """Load the latest candidate/seed features with bounded batch queries."""
        unique_paths = list(dict.fromkeys(str(path) for path in paths))
        result: dict[str, sqlite3.Row] = {}
        chunk_size = 400  # Safely below SQLite's traditional 999-variable limit.
        for start in range(0, len(unique_paths), chunk_size):
            chunk = unique_paths[start : start + chunk_size]
            placeholders = ",".join("?" for _path in chunk)
            rows = self.conn.execute(
                f"""
                select c.set_path as feature_path, c.score, c.metrics_json, c.period
                from candidates c
                join (
                    select set_path, max(id) as latest_id
                    from candidates
                    where set_path in ({placeholders})
                      and score is not null
                      and coalesce(metrics_json, '') != ''
                    group by set_path
                ) latest on latest.latest_id = c.id
                """,
                tuple(chunk),
            ).fetchall()
            result.update((str(row["feature_path"]), row) for row in rows)

        missing = [path for path in unique_paths if path not in result]
        for start in range(0, len(missing), chunk_size):
            chunk = missing[start : start + chunk_size]
            placeholders = ",".join("?" for _path in chunk)
            rows = self.conn.execute(
                f"""
                select seed_path as feature_path, score, metrics_json, period
                from seed_scores
                where seed_path in ({placeholders})
                  and active=1
                  and score is not null
                  and coalesce(metrics_json, '') != ''
                """,
                tuple(chunk),
            ).fetchall()
            result.update((str(row["feature_path"]), row) for row in rows)
        return result

    def record_score(self, set_path: Path, result: ScoreResult | None, status: str, report_path: Path | None = None) -> None:
        accepted = int(status == "accepted" and bool(result and result.accepted)) if result else None
        score_value = None if status == "no_history" else (result.score if result else None)
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
                result.to_json() if result else None,
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
            f"""
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
            """,
            params,
        )
        cur_6m = self.conn.execute(
            f"""
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
            """,
            params,
        )
        cur_ft = self.conn.execute(
            f"""
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
            """,
            params,
        )
        cur_robust = self.conn.execute(
            f"""
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
            """,
            params,
        )
        self._commit()
        return {
            "robustness": int(cur_robust.rowcount or 0),
            "final_tick": int(cur_ft.rowcount or 0),
            "final_tick_6m": int(cur_6m.rowcount or 0),
            "regression": int(cur_regression.rowcount or 0),
        }

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
            try:
                stat = seed.path.stat()
            except OSError:
                continue
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
                    previous_status not in {"accepted", "rejected", "invalid_seed"}
                    and not quarantined_mismatch
                )
                or str(row["symbol"] or "").strip().upper() != seed.symbol.strip().upper()
                or str(row["period"] or "").strip().upper() != seed.period.strip().upper()
            )
            should_eval = force or changed
            if should_eval:
                pending.append(seed)
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
                elif should_eval:
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

    def record_seed_score(self, seed: Seed, result: ScoreResult | None, status: str, report_path: Path | None = None) -> None:
        accepted = int(status == "accepted" and bool(result and result.accepted)) if result else None
        score_value = None if status == "no_history" else (result.score if result else None)
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
                result.to_json() if result else None,
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
            f"""
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
            """,
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

    def _candidate_feedback_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select
                c.run_id, c.seed_path, c.target_symbol, c.symbol, c.period, c.family,
                c.mutated_keys, c.mutation_details_json,
                c.score, c.accepted, c.metrics_json, c.status, c.report_path,
                cr.status as robust_status,
                cr.positive_bonus as robust_positive_bonus,
                cr.negative_bonus as robust_negative_bonus,
                cr.metrics_json as robust_metrics_json,
                ft.status as final_tick_status,
                ft.similarity_json as final_tick_similarity_json,
                ft6.status as final_tick_6m_status,
                ft6.similarity_json as final_tick_6m_similarity_json,
                rg.status as regression_status,
                rg.metrics_json as regression_metrics_json,
                rg.details_json as regression_details_json,
                rg.points_applied as regression_points_applied
            from candidates c
            left join candidate_robustness cr
              on cr.candidate_id = c.id
             and c.status='accepted'
            left join candidate_final_tick ft
              on ft.candidate_id = c.id
             and c.status='accepted'
             and cr.status='accepted'
            left join candidate_final_tick_6m ft6
              on ft6.candidate_id = c.id
             and c.status='accepted'
             and cr.status='accepted'
             and ft.status in ('accepted', 'pending_ohlc_trades')
            left join candidate_regression rg
              on rg.candidate_id = c.id
             and ft6.status='accepted'
            where c.status in ('accepted', 'rejected', 'no_trades')
              and (c.score is not null or c.status = 'no_trades')
            """
        ).fetchall()

    def _seed_feedback_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select seed_path, symbol, period, score, accepted, metrics_json, status, report_path
            from seed_scores
            where active=1
              and status in ('accepted', 'rejected', 'no_trades')
              and (score is not null or status = 'no_trades')
            """
        ).fetchall()

    def mutation_feedback_signals(self) -> dict[str, FeedbackSignal]:
        rows = [row for row in self._candidate_feedback_rows() if str(row["mutated_keys"] or "")]
        global_groups: dict[object, list[object]] = {}
        grouped: dict[str, dict[object, list[object]]] = {}
        for row in rows:
            global_groups.setdefault(candidate_group_key(row), []).append(row)
            for key in str(row["mutated_keys"]).split(";"):
                key = key.strip()
                if key and key not in TIMEFRAME_PATCH_KEYS:
                    grouped.setdefault(key, {}).setdefault(candidate_group_key(row, key), []).append(row)
        return probability_feedback_signals(grouped, global_groups, normalize_keys=False)

    def mutation_feedback(self) -> dict[str, float]:
        return {key: signal.effective_score for key, signal in self.mutation_feedback_signals().items()}

    def mutation_direction_feedback_signals(self) -> dict[str, dict[str, FeedbackSignal]]:
        rows = [row for row in self._candidate_feedback_rows() if str(row["mutation_details_json"] or "")]
        global_groups: dict[object, list[object]] = {}
        grouped: dict[str, dict[object, list[object]]] = {}
        separator = "\0"
        for row in rows:
            try:
                details = json.loads(str(row["mutation_details_json"] or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(details, list):
                continue
            valid_details: list[tuple[str, str]] = []
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                # A legacy wrap replaced an out-of-range local step with a
                # random value anywhere in the range.  Its resulting delta is
                # not evidence about the requested direction.
                if detail.get("wrapped") is True:
                    continue
                key = str(detail.get("key") or "").strip()
                if not key or key in TIMEFRAME_PATCH_KEYS:
                    continue
                try:
                    delta = float(detail.get("delta") or 0.0)
                except (TypeError, ValueError):
                    continue
                if delta == 0.0:
                    continue
                valid_details.append((key, "up" if delta > 0 else "down"))
            if not valid_details:
                continue
            global_groups.setdefault(candidate_group_key(row), []).append(row)
            for key, direction in valid_details:
                composite = f"{key}{separator}{direction}"
                grouped.setdefault(composite, {}).setdefault(
                    candidate_group_key(row, key, direction), []
                ).append(row)

        composite_signals = probability_feedback_signals(grouped, global_groups, normalize_keys=False)
        result: dict[str, dict[str, FeedbackSignal]] = {}
        for composite, signal in composite_signals.items():
            key, direction = composite.rsplit(separator, 1)
            result.setdefault(key, {})[direction] = signal
        return result

    def mutation_direction_feedback(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, directions in self.mutation_direction_feedback_signals().items():
            up = directions.get("up")
            down = directions.get("down")
            value = (up.effective_score if up else 0.0) - (down.effective_score if down else 0.0)
            if value:
                result[key] = round(value, 6)
        return result

    def asset_feedback_signals(self, aliases: dict[str, str] | None = None) -> dict[str, FeedbackSignal]:
        aliases = {str(key).upper(): str(value).upper() for key, value in (aliases or {}).items()}

        def _canonical(symbol: object) -> str:
            raw = str(symbol or "").upper()
            return aliases.get(raw, raw)

        rows = self._candidate_feedback_rows()
        seed_rows = self._seed_feedback_rows()
        global_groups: dict[object, list[object]] = {}
        grouped: dict[str, dict[object, list[object]]] = {}
        for row in rows:
            key = _canonical(row["target_symbol"])
            group = candidate_group_key(row)
            global_groups.setdefault(group, []).append(row)
            grouped.setdefault(key, {}).setdefault(group, []).append(row)
        for row in seed_rows:
            key = _canonical(row["symbol"])
            group = seed_group_key(row)
            global_groups.setdefault(group, []).append(row)
            grouped.setdefault(key, {}).setdefault(group, []).append(row)
        return probability_feedback_signals(grouped, global_groups)

    def asset_feedback(self, aliases: dict[str, str] | None = None) -> dict[str, float]:
        return {key: signal.effective_score for key, signal in self.asset_feedback_signals(aliases).items()}

    def asset_feedback_with_groups(
        self,
        aliases: dict[str, str] | None,
        group_by_symbol: dict[str, str],
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Return instrument and asset-group lifecycle feedback from one scan."""
        aliases = {str(key).upper(): str(value).upper() for key, value in (aliases or {}).items()}
        normalized_groups = {
            str(key).upper(): str(value)
            for key, value in group_by_symbol.items()
        }

        def _canonical(symbol: object) -> str:
            raw = str(symbol or "").upper()
            return aliases.get(raw, raw)

        rows = self._candidate_feedback_rows()
        seed_rows = self._seed_feedback_rows()
        global_groups: dict[object, list[object]] = {}
        grouped_assets: dict[str, dict[object, list[object]]] = {}
        grouped_asset_groups: dict[str, dict[object, list[object]]] = {}
        for row in rows:
            asset_key = _canonical(row["target_symbol"])
            group_key = normalized_groups.get(asset_key, "")
            cohort = candidate_group_key(row)
            global_groups.setdefault(cohort, []).append(row)
            grouped_assets.setdefault(asset_key, {}).setdefault(cohort, []).append(row)
            if group_key:
                grouped_asset_groups.setdefault(group_key, {}).setdefault(cohort, []).append(row)
        for row in seed_rows:
            asset_key = _canonical(row["symbol"])
            group_key = normalized_groups.get(asset_key, "")
            cohort = seed_group_key(row)
            global_groups.setdefault(cohort, []).append(row)
            grouped_assets.setdefault(asset_key, {}).setdefault(cohort, []).append(row)
            if group_key:
                grouped_asset_groups.setdefault(group_key, {}).setdefault(cohort, []).append(row)
        asset_signals = probability_feedback_signals(grouped_assets, global_groups)
        group_signals = probability_feedback_signals(
            grouped_asset_groups,
            global_groups,
            normalize_keys=False,
        )
        return (
            {key: signal.effective_score for key, signal in asset_signals.items()},
            {key: signal.effective_score for key, signal in group_signals.items()},
        )

    def timeframe_feedback_signals(self) -> dict[str, FeedbackSignal]:
        rows = self._candidate_feedback_rows()
        seed_rows = self._seed_feedback_rows()
        global_groups: dict[object, list[object]] = {}
        grouped: dict[str, dict[object, list[object]]] = {}
        for row in rows:
            key = str(row["period"]).upper()
            group = candidate_group_key(row)
            global_groups.setdefault(group, []).append(row)
            grouped.setdefault(key, {}).setdefault(group, []).append(row)
        for row in seed_rows:
            key = str(row["period"]).upper()
            group = seed_group_key(row)
            global_groups.setdefault(group, []).append(row)
            grouped.setdefault(key, {}).setdefault(group, []).append(row)
        return probability_feedback_signals(grouped, global_groups)

    def timeframe_feedback(self) -> dict[str, float]:
        return {key: signal.effective_score for key, signal in self.timeframe_feedback_signals().items()}

    def continuation_seeds(self, limit: int = 0) -> tuple[int, int, list[Seed]]:
        run = self.conn.execute("select id from runs order by id desc limit 1").fetchone()
        if run is None:
            return 0, 0, []
        run_id = int(run["id"])
        generation = self.conn.execute(
            "select max(generation) as generation from candidates where run_id=?",
            (run_id,),
        ).fetchone()
        latest_generation = int(generation["generation"] or 0)
        if latest_generation <= 0:
            return run_id, 0, []
        rows = self.conn.execute(
            """
            select *
            from candidates
            where run_id=? and generation=? and status in ('accepted', 'rejected')
            order by
                case
                    when status = 'accepted' then 0
                    when score is not null then 1
                    else 2
                end,
                score desc,
                id desc
            """,
            (run_id, latest_generation),
        ).fetchall()

        seeds: list[Seed] = []
        seen: set[str] = set()
        for row in rows:
            path = resolve_workspace_path(row["set_path"])
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen or not path.exists():
                continue
            seen.add(key)
            seeds.append(
                Seed(
                    path=path,
                    symbol=row["target_symbol"] or row["symbol"] or "UNKNOWN",
                    period=row["period"] or "UNKNOWN",
                    family=row["family"] or path.parent.name,
                    run_strategy=row["run_strategy"] or "",
                )
            )
            if limit > 0 and len(seeds) >= limit:
                break
        return run_id, latest_generation, seeds

    def latest_run(self) -> sqlite3.Row | None:
        return self.conn.execute("select * from runs order by id desc limit 1").fetchone()

    def max_generation(self, run_id: int) -> int:
        row = self.conn.execute(
            "select max(generation) as generation from candidates where run_id=?",
            (run_id,),
        ).fetchone()
        return int(row["generation"] or 0)

    def pending_generated_generation(self, run_id: int) -> int:
        row = self.conn.execute(
            """
            select min(generation) as generation
            from candidates
            where run_id=? and status='generated'
            """,
            (run_id,),
        ).fetchone()
        return int(row["generation"] or 0)

    def variants_for_generation(self, run_id: int, generation: int, *, status: str | None = None) -> list[Variant]:
        if status:
            rows = self.conn.execute(
                "select * from candidates where run_id=? and generation=? and status=? order by id",
                (run_id, generation, status),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "select * from candidates where run_id=? and generation=? order by id",
                (run_id, generation),
            ).fetchall()
        return [variant_from_candidate_row(row) for row in rows if workspace_path_exists(row["set_path"])]

    def candidate_by_id(self, candidate_id: int) -> sqlite3.Row | None:
        return self.conn.execute("select * from candidates where id=?", (candidate_id,)).fetchone()

    def candidates_for_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select *
            from candidates
            where run_id=?
            order by generation, id
            """,
            (run_id,),
        ).fetchall()

    def run_by_id(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute("select * from runs where id=?", (run_id,)).fetchone()

    def retryable_problem_candidates_for_generation(self, run_id: int, generation: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select *
            from candidates
            where run_id=? and generation=? and status in ('report_mismatch', 'no_report')
            order by id
            """,
            (run_id, generation),
        ).fetchall()

    def retryable_problem_candidates_for_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select *
            from candidates
            where run_id=? and status in ('report_mismatch', 'no_report')
            order by generation, id
            """,
            (run_id,),
        ).fetchall()

    def mismatch_candidates_for_generation(self, run_id: int, generation: int) -> list[sqlite3.Row]:
        return self.retryable_problem_candidates_for_generation(run_id, generation)

    def mismatch_candidates_for_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.retryable_problem_candidates_for_run(run_id)



def variant_from_candidate_row(row: sqlite3.Row) -> Variant:
    seed = Seed(
        path=Path(row["seed_path"]),
        symbol=row["symbol"] or "UNKNOWN",
        period=row["period"] or "UNKNOWN",
        family=row["family"] or Path(row["seed_path"]).parent.name,
        run_strategy=row["run_strategy"] or "",
    )
    return Variant(
        path=Path(row["set_path"]),
        seed=seed,
        target_symbol=row["target_symbol"] or row["symbol"] or "UNKNOWN",
        target_period=(row["period"] or seed.period or "UNKNOWN").upper(),
        mutated_keys=tuple(key for key in str(row["mutated_keys"] or "").split(";") if key),
        missing_lot_keys=tuple(key for key in str(row["missing_lot_keys"] or "").split(";") if key),
        policy=row["policy"] or "",
        timeframe_keys=tuple(key for key in str(row["timeframe_keys"] if "timeframe_keys" in row.keys() else "").split(";") if key),
    )
