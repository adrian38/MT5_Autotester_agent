from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ubs.db import connect_memory
from ubs.models import Seed, Variant
from ubs.score import ScoreResult
from ubs.weights import (
    ASSET_ACCEPTED_BONUS,
    MUTATION_ACCEPTED_BONUS,
    SEED_WEIGHT_SCALE,
    TIMEFRAME_ACCEPTED_BONUS,
    candidate_group_key,
    feedback_weight,
    grouped_shrunk_mean,
    seed_group_key,
)


def aggregate_feedback_value(groups: dict[object, list[float]]) -> float | None:
    return grouped_shrunk_mean(groups)


class AgentMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = connect_memory(self.path, enable_wal=True)
        self._init()

    def close(self) -> None:
        self.conn.close()

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
                hidden integer not null default 0
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
            """
        )
        self._ensure_column("runs", "hidden", "integer not null default 0")
        self.conn.execute(
            """
            update seed_scores
            set status='report_mismatch', accepted=null
            where status in ('accepted', 'rejected')
              and (upper(symbol)='UNKNOWN' or upper(period)='UNKNOWN')
            """
        )
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in self.conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            self.conn.execute(f"alter table {table} add column {column} {definition}")

    def create_run(
        self,
        source_dir: Path,
        output_dir: Path,
        generations: int,
        variants_per_seed: int,
        max_seeds: int,
        execute_backtests: bool,
        dry_run: bool,
    ) -> int:
        cur = self.conn.execute(
            """
            insert into runs (
                created_at, source_dir, output_dir, generations, variants_per_seed,
                max_seeds, execute_backtests, dry_run
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_variant(self, run_id: int, generation: int, variant: Variant, status: str = "generated") -> None:
        self.conn.execute(
            """
            insert into candidates (
                run_id, generation, seed_path, set_path, symbol, target_symbol, period,
                family, run_strategy, mutated_keys, missing_lot_keys, policy, status, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ";".join(variant.missing_lot_keys),
                variant.policy,
                status,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

    def record_score(self, set_path: Path, result: ScoreResult | None, status: str, report_path: Path | None = None) -> None:
        accepted = int(status == "accepted" and bool(result and result.accepted)) if result else None
        self.conn.execute(
            """
            update candidates
            set report_path=?, score=?, accepted=?, metrics_json=?, status=?
            where set_path=?
            """,
            (
                str(report_path) if report_path else (result.report_path if result else None),
                result.score if result else None,
                accepted,
                result.to_json() if result else None,
                status,
                str(set_path),
            ),
        )
        self.conn.commit()

    def prepare_seed_evaluation(self, seeds: list[Seed], *, force: bool = False) -> list[Seed]:
        existing = {
            str(row["seed_path"]): row
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
            changed = (
                row is None
                or abs(float(row["seed_mtime"] or 0.0) - float(stat.st_mtime)) > 0.001
                or int(row["seed_size"] or -1) != int(stat.st_size)
                or str(row["status"] or "") not in {"accepted", "rejected", "invalid_seed"}
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
                previous_status = str(row["status"] or "")
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
                            path_text,
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
                            path_text,
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
                            path_text,
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
                result.score if result else None,
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

    def accepted_candidates_for_final_tick(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
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
            left join candidate_final_tick ft on ft.candidate_id = c.id
            where c.run_id=? and c.status='accepted' and cr.status='accepted'
            order by c.generation, c.id
            """,
            (run_id,),
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
    ) -> None:
        accepted = int(status == "accepted" and bool(result and result.accepted)) if result else None
        self.conn.execute(
            """
            insert into candidate_robustness (
                candidate_id, run_id, status, report_path, score, accepted,
                metrics_json, from_date, to_date, positive_bonus, negative_bonus, evaluated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(candidate_id) do update set
                run_id=excluded.run_id,
                status=excluded.status,
                report_path=excluded.report_path,
                score=excluded.score,
                accepted=excluded.accepted,
                metrics_json=excluded.metrics_json,
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
                from_date.strip(),
                to_date.strip(),
                float(positive_bonus),
                float(negative_bonus),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

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
    ) -> None:
        accepted = int(status == "accepted")
        self.conn.execute(
            """
            insert into candidate_final_tick (
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
        self.conn.commit()

    def mutation_feedback(self) -> dict[str, float]:
        rows = self.conn.execute(
            """
            select
                c.run_id, c.seed_path, c.target_symbol, c.symbol, c.period, c.family,
                c.mutated_keys, c.score, c.accepted, c.metrics_json, c.status, c.report_path,
                cr.status as robust_status,
                cr.positive_bonus as robust_positive_bonus,
                cr.negative_bonus as robust_negative_bonus,
                cr.metrics_json as robust_metrics_json,
                ft.status as final_tick_status,
                ft.similarity_json as final_tick_similarity_json
            from candidates c
            left join candidate_robustness cr on cr.candidate_id = c.id
            left join candidate_final_tick ft on ft.candidate_id = c.id
            where c.mutated_keys != ''
              and c.status in ('accepted', 'rejected', 'no_trades')
              and (c.score is not null or c.status = 'no_trades')
            """
        ).fetchall()
        totals: dict[str, dict[object, list[float]]] = {}
        for row in rows:
            value = feedback_weight(row, accepted_bonus=MUTATION_ACCEPTED_BONUS)
            if value is None:
                continue
            for key in str(row["mutated_keys"]).split(";"):
                if key:
                    totals.setdefault(key, {}).setdefault(candidate_group_key(row, key), []).append(value)
        return {
            key: value
            for key, groups in totals.items()
            if (value := aggregate_feedback_value(groups)) is not None
        }

    def asset_feedback(self, aliases: dict[str, str] | None = None) -> dict[str, float]:
        aliases = {str(key).upper(): str(value).upper() for key, value in (aliases or {}).items()}

        def _canonical(symbol: object) -> str:
            raw = str(symbol or "").upper()
            return aliases.get(raw, raw)

        rows = self.conn.execute(
            """
            select
                c.run_id, c.seed_path, c.target_symbol, c.symbol, c.period, c.family,
                c.score, c.accepted, c.metrics_json, c.status, c.report_path,
                cr.status as robust_status,
                cr.positive_bonus as robust_positive_bonus,
                cr.negative_bonus as robust_negative_bonus,
                cr.metrics_json as robust_metrics_json,
                ft.status as final_tick_status,
                ft.similarity_json as final_tick_similarity_json
            from candidates c
            left join candidate_robustness cr on cr.candidate_id = c.id
            left join candidate_final_tick ft on ft.candidate_id = c.id
            where c.status in ('accepted', 'rejected', 'no_trades')
              and (c.score is not null or c.status = 'no_trades')
            """
        ).fetchall()
        seed_rows = self.conn.execute(
            """
            select seed_path, symbol, period, score, accepted, metrics_json, status, report_path
            from seed_scores
            where active=1
              and status in ('accepted', 'rejected', 'no_trades')
              and (score is not null or status = 'no_trades')
            """
        ).fetchall()
        totals: dict[str, dict[object, list[float]]] = {}
        for row in rows:
            value = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
            if value is None:
                continue
            key = _canonical(row["target_symbol"])
            totals.setdefault(key, {}).setdefault(candidate_group_key(row), []).append(value)
        for row in seed_rows:
            value = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
            if value is None:
                continue
            key = _canonical(row["symbol"])
            totals.setdefault(key, {}).setdefault(seed_group_key(row), []).append(value * SEED_WEIGHT_SCALE)
        return {
            symbol: value
            for symbol, groups in totals.items()
            if (value := grouped_shrunk_mean(groups)) is not None
        }

    def timeframe_feedback(self) -> dict[str, float]:
        rows = self.conn.execute(
            """
            select
                c.run_id, c.seed_path, c.target_symbol, c.symbol, c.period, c.family,
                c.score, c.accepted, c.metrics_json, c.status, c.report_path,
                cr.status as robust_status,
                cr.positive_bonus as robust_positive_bonus,
                cr.negative_bonus as robust_negative_bonus,
                cr.metrics_json as robust_metrics_json,
                ft.status as final_tick_status,
                ft.similarity_json as final_tick_similarity_json
            from candidates c
            left join candidate_robustness cr on cr.candidate_id = c.id
            left join candidate_final_tick ft on ft.candidate_id = c.id
            where c.status in ('accepted', 'rejected', 'no_trades')
              and (c.score is not null or c.status = 'no_trades')
            """
        ).fetchall()
        seed_rows = self.conn.execute(
            """
            select seed_path, symbol, period, score, accepted, metrics_json, status, report_path
            from seed_scores
            where active=1
              and status in ('accepted', 'rejected', 'no_trades')
              and (score is not null or status = 'no_trades')
            """
        ).fetchall()
        totals: dict[str, dict[object, list[float]]] = {}
        for row in rows:
            value = feedback_weight(row, accepted_bonus=TIMEFRAME_ACCEPTED_BONUS)
            if value is None:
                continue
            key = str(row["period"]).upper()
            totals.setdefault(key, {}).setdefault(candidate_group_key(row), []).append(value)
        for row in seed_rows:
            value = feedback_weight(row, accepted_bonus=TIMEFRAME_ACCEPTED_BONUS)
            if value is None:
                continue
            key = str(row["period"]).upper()
            totals.setdefault(key, {}).setdefault(seed_group_key(row), []).append(value * SEED_WEIGHT_SCALE)
        return {
            period: value
            for period, groups in totals.items()
            if (value := grouped_shrunk_mean(groups)) is not None
        }

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
            path = Path(row["set_path"])
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
        return [variant_from_candidate_row(row) for row in rows if Path(row["set_path"]).exists()]

    def candidate_by_id(self, candidate_id: int) -> sqlite3.Row | None:
        return self.conn.execute("select * from candidates where id=?", (candidate_id,)).fetchone()

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
    )
