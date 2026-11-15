from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from ubs.weights import (
    FeedbackSignal,
    NON_PARAMETER_CHANGE_KEYS,
    TIMEFRAME_PATCH_KEYS,
    candidate_group_key,
    parameter_mutation_keys,
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


SOURCE_FEEDBACK_SQL = """
with recursive selected_sources as (
    select
        s.run_id, s.generation as source_generation, s.seed_path,
        s.symbol, s.period, s.family
    from generation_seed_selection s
    where s.run_id in ({placeholders})
), source_descendants as (
    select
        s.run_id, s.source_generation, s.seed_path,
        s.symbol, s.period, s.family,
        c.id as candidate_id, c.generation as candidate_generation,
        c.set_path, c.status
    from selected_sources s
    join candidates c
      on c.run_id=s.run_id
     and c.generation=s.source_generation
     and c.seed_path=s.seed_path

    union all

    select
        d.run_id, d.source_generation, d.seed_path,
        d.symbol, d.period, d.family,
        c.id as candidate_id, c.generation as candidate_generation,
        c.set_path, c.status
    from source_descendants d
    join candidates c
      on c.run_id=d.run_id
     and c.generation>d.candidate_generation
     and c.seed_path=d.set_path
)
select
    d.run_id, d.source_generation as generation, d.seed_path,
    d.symbol, d.period, d.family, d.status,
    cr.status as robust_status,
    ft.status as final_tick_status,
    ft6.status as final_tick_6m_status
from source_descendants d
left join candidate_robustness cr
  on cr.candidate_id=d.candidate_id
 and d.status='accepted'
left join candidate_final_tick ft
  on ft.candidate_id=d.candidate_id
 and d.status='accepted'
 and cr.status='accepted'
left join candidate_final_tick_6m ft6
  on ft6.candidate_id=d.candidate_id
 and d.status='accepted'
 and cr.status='accepted'
 and ft.status in ('accepted', 'pending_ohlc_trades')
where d.status in ('accepted', 'rejected', 'no_trades')
"""


class MemoryFeedbackMixin:
    """Senales de feedback por mutacion, activo y timeframe."""

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

    def candidate_source_feedback_rows(self) -> list[sqlite3.Row]:
        """Return FT 6M outcomes credited to every selected source ancestor."""

        run_ids = self._discovery_run_ids(limit=10)
        if not run_ids:
            return []
        placeholders = ",".join("?" for _run_id in run_ids)
        return self.conn.execute(
            SOURCE_FEEDBACK_SQL.format(placeholders=placeholders),
            run_ids,
        ).fetchall()

    def candidate_policy_feedback_rows(self) -> list[sqlite3.Row]:
        run_ids = self._discovery_run_ids(limit=10)
        if not run_ids:
            return []
        placeholders = ",".join("?" for _run_id in run_ids)
        return self.conn.execute(
            f"""
            select
                c.run_id, c.generation, c.seed_path, c.policy, c.status,
                c.period as target_period, s.period as source_period,
                cr.status as robust_status,
                ft.status as final_tick_status,
                ft6.status as final_tick_6m_status,
                rg.status as regression_status
            from candidates c
            left join generation_seed_selection s
              on s.run_id = c.run_id
             and s.generation = c.generation
             and s.seed_path = c.seed_path
            left join candidate_robustness cr
              on cr.candidate_id = c.id
             and c.status = 'accepted'
            left join candidate_final_tick ft
              on ft.candidate_id = c.id
             and c.status = 'accepted'
             and cr.status = 'accepted'
            left join candidate_final_tick_6m ft6
              on ft6.candidate_id = c.id
             and c.status = 'accepted'
             and cr.status = 'accepted'
             and ft.status in ('accepted', 'pending_ohlc_trades')
            left join candidate_regression rg
              on rg.candidate_id = c.id
             and ft6.status = 'accepted'
            where c.run_id in ({placeholders})
              and c.status in ('accepted', 'rejected', 'no_trades')
            """,
            run_ids,
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

    def mutation_feedback_signals(self, *, terminal_stage: str | None = None) -> dict[str, FeedbackSignal]:
        rows = [
            (row, parameter_mutation_keys(row["mutated_keys"]))
            for row in self._candidate_feedback_rows()
            if parameter_mutation_keys(row["mutated_keys"])
        ]
        global_groups: dict[object, list[object]] = {}
        grouped: dict[str, dict[object, list[object]]] = {}
        for row, mutation_keys in rows:
            global_groups.setdefault(candidate_group_key(row), []).append(row)
            for key in mutation_keys:
                if key and key not in TIMEFRAME_PATCH_KEYS:
                    grouped.setdefault(key, {}).setdefault(candidate_group_key(row, key), []).append(row)
        return probability_feedback_signals(
            grouped,
            global_groups,
            normalize_keys=False,
            terminal_stage=terminal_stage,
        )

    def mutation_feedback(self, *, terminal_stage: str | None = None) -> dict[str, float]:
        return {
            key: signal.effective_score
            for key, signal in self.mutation_feedback_signals(terminal_stage=terminal_stage).items()
        }

    def mutation_direction_feedback_signals(
        self,
        *,
        terminal_stage: str | None = None,
    ) -> dict[str, dict[str, FeedbackSignal]]:
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
                if not key or key in TIMEFRAME_PATCH_KEYS or key in NON_PARAMETER_CHANGE_KEYS:
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

        composite_signals = probability_feedback_signals(
            grouped,
            global_groups,
            normalize_keys=False,
            terminal_stage=terminal_stage,
        )
        result: dict[str, dict[str, FeedbackSignal]] = {}
        for composite, signal in composite_signals.items():
            key, direction = composite.rsplit(separator, 1)
            result.setdefault(key, {})[direction] = signal
        return result

    def mutation_direction_feedback(self, *, terminal_stage: str | None = None) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, directions in self.mutation_direction_feedback_signals(
            terminal_stage=terminal_stage
        ).items():
            up = directions.get("up")
            down = directions.get("down")
            value = (up.effective_score if up else 0.0) - (down.effective_score if down else 0.0)
            if value:
                result[key] = round(value, 6)
        return result

    def asset_feedback_signals(
        self,
        aliases: dict[str, str] | None = None,
        *,
        allowed_symbols: Iterable[str] | None = None,
        terminal_stage: str | None = None,
    ) -> dict[str, FeedbackSignal]:
        aliases = {str(key).upper(): str(value).upper() for key, value in (aliases or {}).items()}

        def _canonical(symbol: object) -> str:
            raw = str(symbol or "").upper()
            return aliases.get(raw, raw)

        allowed = (
            {_canonical(symbol) for symbol in allowed_symbols if str(symbol or "").strip()}
            if allowed_symbols is not None
            else None
        )

        rows = self._candidate_feedback_rows()
        seed_rows = self._seed_feedback_rows()
        global_groups: dict[object, list[object]] = {}
        grouped: dict[str, dict[object, list[object]]] = {}
        for row in rows:
            key = _canonical(row["target_symbol"])
            if allowed is not None and key not in allowed:
                continue
            group = candidate_group_key(row)
            global_groups.setdefault(group, []).append(row)
            grouped.setdefault(key, {}).setdefault(group, []).append(row)
        for row in seed_rows:
            key = _canonical(row["symbol"])
            if allowed is not None and key not in allowed:
                continue
            group = seed_group_key(row)
            global_groups.setdefault(group, []).append(row)
            grouped.setdefault(key, {}).setdefault(group, []).append(row)
        return probability_feedback_signals(
            grouped,
            global_groups,
            terminal_stage=terminal_stage,
        )

    def asset_feedback(
        self,
        aliases: dict[str, str] | None = None,
        *,
        allowed_symbols: Iterable[str] | None = None,
        terminal_stage: str | None = None,
    ) -> dict[str, float]:
        return {
            key: signal.effective_score
            for key, signal in self.asset_feedback_signals(
                aliases,
                allowed_symbols=allowed_symbols,
                terminal_stage=terminal_stage,
            ).items()
        }

    def asset_feedback_with_groups(
        self,
        aliases: dict[str, str] | None,
        group_by_symbol: dict[str, str],
        *,
        terminal_stage: str | None = None,
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
            # group_by_symbol is the active target universe. Unknown historical
            # assets must not shift its relative asset/group probability baseline.
            if not group_key:
                continue
            cohort = candidate_group_key(row)
            global_groups.setdefault(cohort, []).append(row)
            grouped_assets.setdefault(asset_key, {}).setdefault(cohort, []).append(row)
            grouped_asset_groups.setdefault(group_key, {}).setdefault(cohort, []).append(row)
        for row in seed_rows:
            asset_key = _canonical(row["symbol"])
            group_key = normalized_groups.get(asset_key, "")
            if not group_key:
                continue
            cohort = seed_group_key(row)
            global_groups.setdefault(cohort, []).append(row)
            grouped_assets.setdefault(asset_key, {}).setdefault(cohort, []).append(row)
            grouped_asset_groups.setdefault(group_key, {}).setdefault(cohort, []).append(row)
        asset_signals = probability_feedback_signals(
            grouped_assets,
            global_groups,
            terminal_stage=terminal_stage,
        )
        group_signals = probability_feedback_signals(
            grouped_asset_groups,
            global_groups,
            normalize_keys=False,
            terminal_stage=terminal_stage,
        )
        return (
            {key: signal.effective_score for key, signal in asset_signals.items()},
            {key: signal.effective_score for key, signal in group_signals.items()},
        )

    def timeframe_feedback_signals(self, *, terminal_stage: str | None = None) -> dict[str, FeedbackSignal]:
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
        return probability_feedback_signals(
            grouped,
            global_groups,
            terminal_stage=terminal_stage,
        )

    def timeframe_feedback(self, *, terminal_stage: str | None = None) -> dict[str, float]:
        return {
            key: signal.effective_score
            for key, signal in self.timeframe_feedback_signals(
                terminal_stage=terminal_stage
            ).items()
        }
