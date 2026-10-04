from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from ubs.db import connect_memory
from ubs.memory import AgentMemory
from ubs.weights import ASSET_ACCEPTED_BONUS, SEED_WEIGHT_SCALE, TIMEFRAME_ACCEPTED_BONUS, candidate_group_key, feedback_weight, seed_group_key


BASE_DIR = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent


class UBSUniverseStatsMixin:
    """Recuento de pesos por activo y por timeframe leido de la memoria."""

    def _read_ubs_universe_memory(self, memory_path, signal_aliases, universe_symbols):
        """Filas, semillas y senales de la memoria, o None si SQLite falla."""
        try:
            conn = connect_memory(memory_path)
            conn.row_factory = sqlite3.Row
            if hasattr(self, "_ensure_ubs_memory_schema"):
                self._ensure_ubs_memory_schema(conn)
            rows = conn.execute(
                """
                select
                    c.run_id, c.seed_path, c.target_symbol, c.symbol, c.period, c.family,
                    c.policy, c.score, c.accepted, c.metrics_json, c.status, c.report_path,
                    cr.status as robust_status,
                    cr.positive_bonus as robust_positive_bonus,
                    cr.negative_bonus as robust_negative_bonus,
                    cr.metrics_json as robust_metrics_json,
                    ft.status as final_tick_status,
                    ft.similarity_json as final_tick_similarity_json,
                    ft6.status as final_tick_6m_status,
                    ft6.similarity_json as final_tick_6m_similarity_json,
                    rg.status as regression_status,
                    rg.points_applied as regression_points_applied
                from candidates c
                left join candidate_robustness cr on cr.candidate_id = c.id
                left join candidate_final_tick ft on ft.candidate_id = c.id
                left join candidate_final_tick_6m ft6 on ft6.candidate_id = c.id
                left join candidate_regression rg on rg.candidate_id = c.id
                """
            ).fetchall()
            seed_table = conn.execute(
                "select name from sqlite_master where type='table' and name='seed_scores'"
            ).fetchone()
            seed_rows = []
            if seed_table:
                seed_rows = conn.execute(
                    """
                    select seed_path, symbol, period, score, accepted, metrics_json, status, active, report_path
                    from seed_scores
                    where active=1
                    """
                ).fetchall()
            conn.close()
            memory = AgentMemory(memory_path)
            try:
                asset_signals = memory.asset_feedback_signals(
                    signal_aliases,
                    allowed_symbols=universe_symbols,
                )
                timeframe_signals = memory.timeframe_feedback_signals()
            finally:
                memory.close()
        except sqlite3.Error as exc:
            self.ubs_universe_summary.set(f"No se pudo leer memoria UBS: {exc}")
            self.ubs_timeframe_summary.set("Sin pesos por error SQLite")
            return None
        return rows, seed_rows, asset_signals, timeframe_signals

    def _apply_ubs_candidate_weights(self, row, asset_stat, tf_stat, counters) -> None:
        """Pesos del candidato sobre su activo y su timeframe."""
        score = float(row["score"] or 0.0)
        accepted = bool(row["accepted"])
        robust_status = str(row["robust_status"] or "")
        if robust_status == "accepted":
            counters["total_robust_accepted"] += 1
        elif robust_status == "rejected":
            counters["total_robust_rejected"] += 1
        asset_weight = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
        tf_weight = feedback_weight(row, accepted_bonus=TIMEFRAME_ACCEPTED_BONUS)
        if asset_weight is None and tf_weight is None:
            asset_stat["pending"] = int(asset_stat["pending"]) + 1
            tf_stat["pending"] = int(tf_stat["pending"]) + 1
            counters["total_pending"] += 1
            return
        for stat, weight in ((asset_stat, asset_weight), (tf_stat, tf_weight)):
            if weight is None:
                return
            stat["scores"].append(score)
            stat["weights"].append(weight)
            groups = stat["weight_groups"]
            if isinstance(groups, dict):
                groups.setdefault(candidate_group_key(row), []).append(weight)
            stat["tests"] = int(stat["tests"]) + 1
            stat["accepted"] = int(stat["accepted"]) + (1 if accepted else 0)
            stat["best"] = score if stat["best"] is None else max(float(stat["best"]), score)
        counters["total_scored"] += 1

    def _apply_ubs_seed_weights(
        self, row, asset_stats, tf_stat, counters, eligible_canonicals,
    ) -> None:
        """Pesos de la semilla sobre sus activos y su timeframe."""
        score = float(row["score"] or 0.0)
        accepted = bool(row["accepted"])
        asset_weight = feedback_weight(row, accepted_bonus=ASSET_ACCEPTED_BONUS)
        tf_weight = feedback_weight(row, accepted_bonus=TIMEFRAME_ACCEPTED_BONUS)
        if asset_weight is None and tf_weight is None:
            for canonical in eligible_canonicals:
                asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                asset_stat["pending"] = int(asset_stat["pending"]) + 1
            tf_stat["pending"] = int(tf_stat["pending"]) + 1
            counters["total_seed_pending"] += 1
            return
        if asset_weight is not None:
            asset_weight *= SEED_WEIGHT_SCALE
        if tf_weight is not None:
            tf_weight *= SEED_WEIGHT_SCALE
        if asset_weight is not None:
            for canonical in eligible_canonicals:
                asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                asset_stat["scores"].append(score)
                asset_stat["weights"].append(asset_weight)
                groups = asset_stat["weight_groups"]
                if isinstance(groups, dict):
                    groups.setdefault(seed_group_key(row), []).append(asset_weight)
                asset_stat["tests"] = int(asset_stat["tests"]) + 1
                asset_stat["accepted"] = int(asset_stat["accepted"]) + (1 if accepted else 0)
                asset_stat["best"] = score if asset_stat["best"] is None else max(float(asset_stat["best"]), score)
        if tf_weight is not None:
            stat = tf_stat
            weight = tf_weight
            stat["scores"].append(score)
            stat["weights"].append(weight)
            groups = stat["weight_groups"]
            if isinstance(groups, dict):
                groups.setdefault(seed_group_key(row), []).append(weight)
            stat["tests"] = int(stat["tests"]) + 1
            stat["accepted"] = int(stat["accepted"]) + (1 if accepted else 0)
            stat["best"] = score if stat["best"] is None else max(float(stat["best"]), score)
        counters["total_seed_scored"] += 1

    def _accumulate_ubs_candidate_row(
        self, row, asset_stats, timeframe_stats, counters,
        aliases, symbol_map, suffix_universe, symbol_suffix, futures_suffix,
        shares_suffix, universe_symbols, universe_symbols_tuple,
        disabled_symbols, seed_enabled_when_disabled,
    ) -> None:
        """Suma una fila de candidato a los recuentos por activo y por timeframe."""
        status = str(row["status"] or "")
        if str(row["policy"] or "") == "history_probe":
            return
        if status == "report_mismatch":
            counters["total_mismatch"] += 1
            return
        canonical = self._canonical_ubs_symbol(
            self._ubs_row_metric_symbol(row) or row["target_symbol"] or row["symbol"],
            aliases,
            symbol_map=symbol_map,
            suffix_universe=suffix_universe,
            symbol_suffix=symbol_suffix,
            futures_suffix=futures_suffix,
            shares_suffix=shares_suffix,
        )
        # Historical rows stay in SQLite for audit, but only the live
        # broker universe may create selectable rows or asset weights.
        if canonical.upper() not in universe_symbols:
            counters["total_outside_universe"] += 1
            return
        if canonical.upper() in disabled_symbols:
            return
        period = str(row["period"] or "UNKNOWN").upper()
        asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
        tf_stat = timeframe_stats.setdefault(period, self._empty_ubs_stat())
        if status == "trade_disabled":
            return
        if status not in {"accepted", "rejected", "no_trades"}:
            asset_stat["pending"] = int(asset_stat["pending"]) + 1
            tf_stat["pending"] = int(tf_stat["pending"]) + 1
            counters["total_pending"] += 1
            return
        self._apply_ubs_candidate_weights(
            row, asset_stat, tf_stat, counters,
        )

    def _accumulate_ubs_candidate_rows(
        self, rows, asset_stats, timeframe_stats,
        aliases, symbol_map, suffix_universe, symbol_suffix, futures_suffix,
        shares_suffix, universe_symbols, universe_symbols_tuple,
        disabled_symbols, seed_enabled_when_disabled,
    ) -> dict[str, int]:
        """Recuentos por activo y timeframe de los candidatos del run."""
        counters = {name: 0 for name in (
            "total_scored", "total_pending", "total_mismatch",
            "total_outside_universe", "total_robust_accepted", "total_robust_rejected",
        )}
        for row in rows:
            self._accumulate_ubs_candidate_row(
                row, asset_stats, timeframe_stats, counters,
                aliases, symbol_map, suffix_universe, symbol_suffix, futures_suffix,
                shares_suffix, universe_symbols, universe_symbols_tuple,
                disabled_symbols, seed_enabled_when_disabled,
            )
        return counters

    def _accumulate_ubs_seed_row(self, row, counters, asset_stats, timeframe_stats, aliases, symbol_map, suffix_universe, symbol_suffix, futures_suffix, shares_suffix, universe_symbols, universe_symbols_tuple, disabled_symbols, seed_enabled_when_disabled) -> None:
        """Suma una semilla evaluada a los recuentos por activo y por timeframe."""
        status = str(row["status"] or "")
        if status == "report_mismatch":
            counters["total_seed_mismatch"] += 1
            return
        canonicals = self._ubs_seed_row_canonical_symbols(
            row,
            universe_symbols_tuple,
            aliases,
            symbol_map,
            suffix_universe,
            symbol_suffix,
            futures_suffix,
            shares_suffix,
        )
        current_canonicals = tuple(
            canonical
            for canonical in canonicals
            if canonical.upper() in universe_symbols
        )
        if not current_canonicals:
            counters["total_seed_outside_universe"] += 1
            return
        eligible_canonicals = tuple(
            canonical
            for canonical in current_canonicals
            if (
                canonical.upper() not in disabled_symbols
                or canonical.upper() in seed_enabled_when_disabled
            )
        )
        if not eligible_canonicals:
            return
        period = str(row["period"] or "UNKNOWN").upper()
        tf_stat = timeframe_stats.setdefault(period, self._empty_ubs_stat())
        if status == "trade_disabled":
            return
        if status not in {"accepted", "rejected", "no_trades"}:
            for canonical in eligible_canonicals:
                asset_stat = asset_stats.setdefault(canonical, self._empty_ubs_stat())
                asset_stat["pending"] = int(asset_stat["pending"]) + 1
            tf_stat["pending"] = int(tf_stat["pending"]) + 1
            counters["total_seed_pending"] += 1
            return
        self._apply_ubs_seed_weights(
            row, asset_stats, tf_stat, counters,
            eligible_canonicals,
        )

    def _accumulate_ubs_seed_rows(
        self, seed_rows, asset_stats, timeframe_stats,
        aliases, symbol_map, suffix_universe, symbol_suffix, futures_suffix,
        shares_suffix, universe_symbols, universe_symbols_tuple,
        disabled_symbols, seed_enabled_when_disabled,
    ) -> dict[str, int]:
        """Recuentos por activo y timeframe de las semillas evaluadas."""
        counters = {name: 0 for name in (
            "total_seed_scored", "total_seed_pending",
            "total_seed_mismatch", "total_seed_outside_universe",
        )}
        for row in seed_rows:
            self._accumulate_ubs_seed_row(row, counters, asset_stats, timeframe_stats, aliases, symbol_map, suffix_universe, symbol_suffix, futures_suffix, shares_suffix, universe_symbols, universe_symbols_tuple, disabled_symbols, seed_enabled_when_disabled)
        return counters

    def _collect_ubs_universe_stats(
        self, memory_path, aliases, universe_symbols, universe_symbols_tuple,
        symbol_map, signal_aliases, suffix_universe,
        symbol_suffix, futures_suffix, shares_suffix,
        disabled_symbols, seed_enabled_when_disabled,
    ) -> dict[str, object]:
        """Recuentos por activo y por timeframe leidos de la memoria del run."""
        asset_stats: dict[str, dict[str, object]] = {}
        timeframe_stats: dict[str, dict[str, object]] = {}
        asset_signals: dict = {}
        timeframe_signals: dict = {}
        counters = {name: 0 for name in (
            "total_scored", "total_pending", "total_mismatch", "total_outside_universe",
            "total_robust_accepted", "total_robust_rejected", "total_seed_scored",
            "total_seed_pending", "total_seed_mismatch", "total_seed_outside_universe",
        )}
        if memory_path.exists():
            read = self._read_ubs_universe_memory(
                memory_path, signal_aliases, universe_symbols,
            )
            if read is None:
                return None
            rows, seed_rows, asset_signals, timeframe_signals = read
            context = (
                aliases, symbol_map, suffix_universe, symbol_suffix, futures_suffix,
                shares_suffix, universe_symbols, universe_symbols_tuple,
                disabled_symbols, seed_enabled_when_disabled,
            )
            counters.update(self._accumulate_ubs_candidate_rows(
                rows, asset_stats, timeframe_stats, *context,
            ))
            counters.update(self._accumulate_ubs_seed_rows(
                seed_rows, asset_stats, timeframe_stats, *context,
            ))
        return {
            "asset_stats": asset_stats,
            "timeframe_stats": timeframe_stats,
            "asset_signals": asset_signals,
            "timeframe_signals": timeframe_signals,
            **counters,
        }
