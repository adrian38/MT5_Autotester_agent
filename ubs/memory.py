from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ubs.models import Seed, Variant
from ubs.score import ScoreResult


class AgentMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
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
                or str(row["status"] or "") not in {"accepted", "rejected", "report_mismatch"}
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

    def mutation_feedback(self) -> dict[str, float]:
        rows = self.conn.execute(
            """
            select mutated_keys, score, accepted
            from candidates
            where score is not null and mutated_keys != '' and status in ('accepted', 'rejected')
            """
        ).fetchall()
        totals: dict[str, list[float]] = {}
        for row in rows:
            bonus = float(row["score"]) + (15.0 if row["accepted"] else 0.0)
            for key in str(row["mutated_keys"]).split(";"):
                if key:
                    totals.setdefault(key, []).append(bonus)
        return {key: sum(values) / len(values) for key, values in totals.items()}

    def asset_feedback(self, aliases: dict[str, str] | None = None) -> dict[str, float]:
        aliases = {str(key).upper(): str(value).upper() for key, value in (aliases or {}).items()}

        def _canonical(symbol: object) -> str:
            raw = str(symbol or "").upper()
            return aliases.get(raw, raw)

        rows = self.conn.execute(
            """
            select target_symbol, score, accepted
            from candidates
            where score is not null and status in ('accepted', 'rejected')
            """
        ).fetchall()
        totals: dict[str, list[float]] = {}
        for row in rows:
            value = float(row["score"]) + (20.0 if row["accepted"] else 0.0)
            totals.setdefault(_canonical(row["target_symbol"]), []).append(value)
        seed_rows = self.conn.execute(
            """
            select symbol, score, accepted
            from seed_scores
            where active=1 and score is not null and status in ('accepted', 'rejected')
            """
        ).fetchall()
        for row in seed_rows:
            value = float(row["score"]) + (20.0 if row["accepted"] else 0.0)
            totals.setdefault(_canonical(row["symbol"]), []).append(value)
        return {symbol: sum(values) / len(values) for symbol, values in totals.items()}

    def timeframe_feedback(self) -> dict[str, float]:
        rows = self.conn.execute(
            """
            select period, score, accepted
            from candidates
            where score is not null and status in ('accepted', 'rejected')
            """
        ).fetchall()
        totals: dict[str, list[float]] = {}
        for row in rows:
            value = float(row["score"]) + (15.0 if row["accepted"] else 0.0)
            totals.setdefault(str(row["period"]).upper(), []).append(value)
        seed_rows = self.conn.execute(
            """
            select period, score, accepted
            from seed_scores
            where active=1 and score is not null and status in ('accepted', 'rejected')
            """
        ).fetchall()
        for row in seed_rows:
            value = float(row["score"]) + (15.0 if row["accepted"] else 0.0)
            totals.setdefault(str(row["period"]).upper(), []).append(value)
        return {period: sum(values) / len(values) for period, values in totals.items()}

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

    def mismatch_candidates_for_generation(self, run_id: int, generation: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select *
            from candidates
            where run_id=? and generation=? and status='report_mismatch'
            order by id
            """,
            (run_id, generation),
        ).fetchall()

    def mismatch_candidates_for_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select *
            from candidates
            where run_id=? and status='report_mismatch'
            order by generation, id
            """,
            (run_id,),
        ).fetchall()



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

