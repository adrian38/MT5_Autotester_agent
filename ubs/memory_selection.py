from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from ubs.models import Seed
from ubs.selection import (
    SelectionFitnessModel,
    SelectionPrediction,
    descendant_fitness_predictions,
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


class MemorySelectionMixin:
    """Modelo de fitness y predicciones de seleccion de semillas."""

    def selection_fitness_model(
        self,
        *,
        exclude_run_id: int | None = None,
        target: str = "final_tick_6m",
    ) -> SelectionFitnessModel | None:
        cache_key = (exclude_run_id, target)
        if cache_key in self._selection_fitness_models:
            return self._selection_fitness_models[cache_key]
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
        model = SelectionFitnessModel.train(rows, target=target)
        self._selection_fitness_models[cache_key] = model
        return model

    def seed_selection_predictions(
        self,
        seeds: list[Seed],
        *,
        exclude_run_id: int | None = None,
        target: str = "final_tick_6m",
    ) -> dict[str, SelectionPrediction]:
        model = self.selection_fitness_model(exclude_run_id=exclude_run_id, target=target)
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

    def discovery_seed_descendant_predictions(
        self,
        seeds: list[Seed],
        *,
        exclude_run_id: int,
    ) -> dict[str, SelectionPrediction]:
        """Predict source usefulness from finalized FT 6M outcomes of prior children."""

        discovery_run_ids = self._discovery_run_ids(before_run_id=exclude_run_id)
        paths = [str(seed.path) for seed in seeds]
        if not discovery_run_ids:
            return {path: SelectionPrediction(0.0, 0.0, 0.0) for path in paths}
        placeholders = ",".join("?" for _run_id in discovery_run_ids)
        rows = self.conn.execute(
            f"""
            select
                c.run_id, c.generation, c.seed_path, c.set_path, c.status,
                cr.status as robust_status,
                ft.status as final_tick_status,
                ft6.status as final_tick_6m_status
            from candidates c
            left join candidate_robustness cr
              on cr.candidate_id=c.id
             and c.status='accepted'
            left join candidate_final_tick ft
              on ft.candidate_id=c.id
             and c.status='accepted'
             and cr.status='accepted'
            left join candidate_final_tick_6m ft6
              on ft6.candidate_id=c.id
             and c.status='accepted'
             and cr.status='accepted'
             and ft.status in ('accepted', 'pending_ohlc_trades')
            where c.run_id in ({placeholders})
              and c.status in ('accepted', 'rejected', 'no_trades')
            """,
            discovery_run_ids,
        ).fetchall()
        return descendant_fitness_predictions(rows, paths)

    def _discovery_run_ids(
        self,
        *,
        limit: int = 0,
        before_run_id: int | None = None,
    ) -> list[int]:
        params: tuple[object, ...] = ()
        before_clause = ""
        if before_run_id is not None:
            before_clause = "and id < ?"
            params = (int(before_run_id),)
        result: list[int] = []
        for row in self.conn.execute(
            f"select id, config_json from runs where hidden=0 {before_clause} order by id desc",
            params,
        ).fetchall():
            try:
                config = json.loads(str(row["config_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            args = config.get("args") if isinstance(config, dict) else {}
            generation = config.get("generation") if isinstance(config, dict) else {}
            args = args if isinstance(args, dict) else {}
            generation = generation if isinstance(generation, dict) else {}
            is_discovery = bool(args.get("force_unseeded_universe")) or str(
                args.get("generation_mode") or generation.get("mode") or ""
            ).lower() == "discovery"
            if is_discovery:
                result.append(int(row["id"]))
                if limit > 0 and len(result) >= limit:
                    break
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
