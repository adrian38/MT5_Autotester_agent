from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ubs.db import connect_memory
from ubs.models import Seed, Variant
from ubs.path_utils import resolve_workspace_path, workspace_path_exists
from ubs.selection import SelectionFitnessModel, SelectionPrediction
from ubs.weights import parameter_mutation_keys


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


from ubs.memory_feedback import MemoryFeedbackMixin
from ubs.memory_schema import MemorySchemaMixin
from ubs.memory_scores import MemoryScoresMixin
from ubs.memory_selection import MemorySelectionMixin
from ubs.memory_stages import MemoryStagesMixin


class AgentMemory(
    MemorySchemaMixin,
    MemorySelectionMixin,
    MemoryScoresMixin,
    MemoryStagesMixin,
    MemoryFeedbackMixin,
):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.active_final_tick_stage = "probe"
        self._defer_commits = 0
        self._selection_fitness_models: dict[
            tuple[int | None, str], SelectionFitnessModel | None
        ] = {}
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
            where run_id=? and generation=?
              and status in ('report_mismatch', 'no_report', 'pending_tester_context')
            order by id
            """,
            (run_id, generation),
        ).fetchall()

    def retryable_problem_candidates_for_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select *
            from candidates
            where run_id=? and status in ('report_mismatch', 'no_report', 'pending_tester_context')
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
        mutated_keys=parameter_mutation_keys(row["mutated_keys"]),
        missing_lot_keys=tuple(key for key in str(row["missing_lot_keys"] or "").split(";") if key),
        policy=row["policy"] or "",
        timeframe_keys=tuple(key for key in str(row["timeframe_keys"] if "timeframe_keys" in row.keys() else "").split(";") if key),
    )
