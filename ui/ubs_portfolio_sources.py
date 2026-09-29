"""Candidatos, grupos de activos y sets ya usados por otros portafolios."""
from __future__ import annotations

from dataclasses import asdict
import sqlite3
from pathlib import Path

from ubs.account import broker_asset_universe_path, normalize_broker
from portfolio_manager.ubs_portfolio import (
    PortfolioAvailability,
    PortfolioResult,
    PortfolioType,
    filter_rows_grid_off,
    normalize_margin_profile,
    portfolio_group_key,
    summarize_robust_rows,
)
from ui.ubs_portfolio_base import (
    BASE_DIR,
    PORTFOLIO_ASSET_GROUP_FLAGS,
    PORTFOLIO_MARGIN_PROFILE_DISPLAY,
    PORTFOLIO_TYPE_LABELS,
)


class UBSPortfolioSourcesMixin:
    """Candidatos, grupos de activos y sets ya usados por otros portafolios."""

    def _final_tick_passed_candidates(
        self,
        conn: sqlite3.Connection,
        account_type: str,
        *,
        include_quarantined: bool = False,
    ) -> list[sqlite3.Row]:
        return conn.execute(
            """
            select ? as account_type,
                   ? || ':' || c.id as candidate_id,
                   c.id as source_candidate_id,
                   c.set_path, c.symbol, c.target_symbol,
                   c.period, c.family,
                   c.report_path as is_report_path,
                   cr.report_path as oos_report_path,
                   ft6.ohlc_report_path as final_ohlc_report_path,
                   ft6.real_tick_report_path as final_tick_report_path,
                   ft6.from_date as final_tick_from_date,
                   ft6.to_date as final_tick_to_date
            from candidates c
            join candidate_robustness cr on cr.candidate_id = c.id
            join candidate_final_tick_6m ft6 on ft6.candidate_id = c.id
            where c.status = 'accepted'
              and cr.status = 'accepted'
              and ft6.status = 'accepted'
              and (? = 1 or not exists (
                  select 1 from portfolio_quarantine pq
                  where pq.set_path = c.set_path
              ))
            order by c.id
            """,
            (account_type, account_type, 1 if include_quarantined else 0),
        ).fetchall()

    def _final_tick_passed_candidates_all_accounts(
        self,
        *,
        include_quarantined: bool = False,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for account_type, memory_path in self._ubs_portfolio_source_paths():
            conn = self._ubs_portfolio_conn_for_memory(memory_path)
            try:
                rows.extend(
                    dict(row)
                    for row in self._final_tick_passed_candidates(
                        conn,
                        account_type,
                        include_quarantined=include_quarantined,
                    )
                )
            finally:
                conn.close()
        return rows

    def _portfolio_type_from_label(self, value: object) -> PortfolioType:
        text = str(value or "").strip()
        if text in PORTFOLIO_TYPE_LABELS:
            return PORTFOLIO_TYPE_LABELS[text]
        try:
            return PortfolioType(text.lower())
        except ValueError:
            return PortfolioType.BALANCED

    def _active_broker_margin_profile(self) -> str:
        broker_func = getattr(self, "_ubs_broker", None)
        if callable(broker_func):
            try:
                return normalize_broker(broker_func()).lower()
            except Exception:
                pass
        broker_var = getattr(self, "ubs_broker", None)
        try:
            broker_value = broker_var.get() if broker_var is not None else ""
        except Exception:
            broker_value = ""
        return normalize_broker(broker_value or "ROBOFOREX").lower()

    def _portfolio_margin_profile(self) -> str:
        profile_var = getattr(self, "ubs_portfolio_margin_profile", None)
        try:
            profile_value = profile_var.get() if profile_var is not None else ""
        except Exception:
            profile_value = ""
        return normalize_margin_profile(profile_value or self._active_broker_margin_profile())

    def _portfolio_margin_profile_display(self, profile: str | None = None) -> str:
        return PORTFOLIO_MARGIN_PROFILE_DISPLAY.get(
            normalize_margin_profile(profile or self._active_broker_margin_profile()),
            "ROBOFOREX",
        )

    def _ubs_portfolio_allowed_asset_groups(self) -> set[str]:
        groups: set[str] = set()
        for group, suffix in PORTFOLIO_ASSET_GROUP_FLAGS:
            var = getattr(self, f"ubs_portfolio_{suffix}", None)
            if var is None or bool(var.get()):
                groups.add(group)
        return groups

    def _ubs_portfolio_universe_files(self) -> list[Path]:
        broker_var = getattr(self, "ubs_broker", None)
        try:
            broker_value = broker_var.get() if broker_var is not None else ""
        except Exception:
            broker_value = ""
        broker = normalize_broker(broker_value or "ROBOFOREX")
        path = broker_asset_universe_path(BASE_DIR, broker)
        return [path] if path.exists() else []

    def _portfolio_group_key(self, symbol: str) -> str:
        universe_files = self._ubs_portfolio_universe_files()
        return portfolio_group_key(symbol, universe_files=universe_files or None)

    def _portfolio_row_group(self, row: object) -> str:
        if isinstance(row, dict):
            symbol = str(row.get("target_symbol") or row.get("symbol") or "")
        else:
            getter = getattr(row, "get", None)
            if callable(getter):
                symbol = str(getter("target_symbol") or getter("symbol") or "")
            else:
                symbol = str(getattr(row, "target_symbol", "") or getattr(row, "symbol", ""))
        return self._portfolio_group_key(symbol)

    def _filter_portfolio_rows_by_allowed_groups(
        self,
        rows: list[dict[str, object]],
        allowed_groups: set[str],
    ) -> tuple[list[dict[str, object]], dict[str, int]]:
        counts: dict[str, int] = {}
        filtered: list[dict[str, object]] = []
        for row in rows:
            group = self._portfolio_row_group(row)
            counts[group] = counts.get(group, 0) + 1
            if group in allowed_groups:
                filtered.append(row)
        return filtered, counts

    def _filter_portfolio_sets_by_allowed_groups(
        self,
        sets: list,
        allowed_groups: set[str],
    ) -> tuple[list, dict[str, int]]:
        counts: dict[str, int] = {}
        filtered: list = []
        for strategy in sets:
            group = self._portfolio_group_key(str(getattr(strategy, "symbol", "")))
            counts[group] = counts.get(group, 0) + 1
            if group in allowed_groups:
                filtered.append(strategy)
        return filtered, counts

    def _portfolio_type_reserve_pct(self, configured_reserve: float, portfolio_type: PortfolioType) -> float:
        if portfolio_type == PortfolioType.CONSERVATIVE:
            return max(configured_reserve, 25.0)
        if portfolio_type == PortfolioType.BALANCED:
            return max(configured_reserve, 15.0)
        return configured_reserve

    def _used_set_paths(
        self,
        conn: sqlite3.Connection,
        target_portfolio_type: PortfolioType,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_scope: str = "full_history",
        target_month: int | None = None,
    ) -> list[str]:
        if target_portfolio_type == PortfolioType.AGGRESSIVE:
            type_filter = (
                "and lower(coalesce(nullif(p.portfolio_type, ''), nullif(p.type, ''), '')) = 'aggressive'"
            )
        else:
            type_filter = (
                "and lower(coalesce(nullif(p.portfolio_type, ''), nullif(p.type, ''), '')) <> 'aggressive'"
            )
        rows = conn.execute(
            f"""
            select pa.set_path
            from portfolio_allocations pa
            join portfolios p on p.id = pa.portfolio_id
            where pa.set_path is not null and pa.set_path <> ''
              {type_filter}
              and coalesce(nullif(p.portfolio_scope, ''), 'full_history') = ?
              and (? is null or p.target_month = ?)
              and (? is null or pa.portfolio_id <> ?)
            union
            select pm.set_path
            from portfolio_members pm
            join portfolios p on p.id = pm.portfolio_id
            where pm.set_path is not null and pm.set_path <> ''
              {type_filter}
              and coalesce(nullif(p.portfolio_scope, ''), 'full_history') = ?
              and (? is null or p.target_month = ?)
              and (? is null or pm.portfolio_id <> ?)
            """
            ,
            (
                portfolio_scope,
                target_month,
                target_month,
                exclude_portfolio_id,
                exclude_portfolio_id,
                portfolio_scope,
                target_month,
                target_month,
                exclude_portfolio_id,
                exclude_portfolio_id,
            ),
        ).fetchall()
        return [str(row["set_path"]) for row in rows]

    def _used_set_paths_all_accounts(
        self,
        target_portfolio_type: PortfolioType,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_scope: str = "full_history",
        target_month: int | None = None,
    ) -> list[str]:
        used: set[str] = set()
        active_memory = self._ubs_memory_path().resolve()
        for _account_type, memory_path in self._ubs_portfolio_source_paths():
            conn = self._ubs_portfolio_conn_for_memory(memory_path)
            try:
                excluded = exclude_portfolio_id if memory_path.resolve() == active_memory else None
                used.update(
                    self._used_set_paths(
                        conn,
                        target_portfolio_type,
                        exclude_portfolio_id=excluded,
                        portfolio_scope=portfolio_scope,
                        target_month=target_month,
                    )
                )
            finally:
                conn.close()
        return sorted(used)

    def _used_set_paths_all_risk_profiles(
        self,
        *,
        exclude_portfolio_id: int | None = None,
        portfolio_scope: str = "full_history",
        target_month: int | None = None,
    ) -> list[str]:
        used: set[str] = set()
        for portfolio_type in (PortfolioType.AGGRESSIVE, PortfolioType.BALANCED):
            used.update(
                self._used_set_paths_all_accounts(
                    portfolio_type,
                    exclude_portfolio_id=exclude_portfolio_id,
                    portfolio_scope=portfolio_scope,
                    target_month=target_month,
                )
            )
        return sorted(used)

    def _used_monthly_set_paths_all_accounts(
        self,
        *,
        exclude_portfolio_id: int | None = None,
    ) -> list[str]:
        used: set[str] = set()
        active_memory = self._ubs_memory_path().resolve()
        for _account_type, memory_path in self._ubs_portfolio_source_paths():
            conn = self._ubs_portfolio_conn_for_memory(memory_path)
            try:
                excluded = exclude_portfolio_id if memory_path.resolve() == active_memory else None
                rows = conn.execute(
                    """
                    select pa.set_path
                    from portfolio_allocations pa
                    join portfolios p on p.id = pa.portfolio_id
                    where pa.set_path is not null and pa.set_path <> ''
                      and coalesce(nullif(p.portfolio_scope, ''), 'full_history') = 'monthly'
                      and (? is null or pa.portfolio_id <> ?)
                    union
                    select pm.set_path
                    from portfolio_members pm
                    join portfolios p on p.id = pm.portfolio_id
                    where pm.set_path is not null and pm.set_path <> ''
                      and coalesce(nullif(p.portfolio_scope, ''), 'full_history') = 'monthly'
                      and (? is null or pm.portfolio_id <> ?)
                    """,
                    (excluded, excluded, excluded, excluded),
                ).fetchall()
                used.update(str(row["set_path"]) for row in rows)
            finally:
                conn.close()
        return sorted(used)

    def _portfolio_availability(
        self,
        _conn: sqlite3.Connection | None = None,
        *,
        target_portfolio_type: PortfolioType | None = None,
    ) -> PortfolioAvailability:
        rows = self._final_tick_passed_candidates_all_accounts()
        grid_off_var = getattr(self, "ubs_portfolio_grid_off", None)
        if grid_off_var is not None and bool(grid_off_var.get()):
            rows, _warnings = filter_rows_grid_off(rows)
        allowed_groups = self._ubs_portfolio_allowed_asset_groups()
        if allowed_groups:
            rows, _group_counts = self._filter_portfolio_rows_by_allowed_groups(rows, allowed_groups)
        exclude_used_var = getattr(self, "ubs_portfolio_exclude_used_sets", None)
        exclude_used = exclude_used_var is None or bool(exclude_used_var.get())
        used = self._used_set_paths_all_risk_profiles() if exclude_used else []
        return summarize_robust_rows(rows, used)

    def _portfolio_result_metrics(
        self,
        inputs: dict[str, object],
        result: PortfolioResult,
    ) -> dict[str, object]:
        return {
            "inputs": inputs,
            "warnings": result.warnings,
            "group_summary": result.group_summary,
            "equity_curve_2020_2026": result.equity_curve_2020_2026,
            "unused_sets": [asdict(item) for item in result.unused_sets],
            "stress_bootstrap": asdict(result.stress_bootstrap) if result.stress_bootstrap else None,
            "seasonal_coverage": result.seasonal_coverage,
            "seasonal_validation": result.seasonal_validation,
            "margin_summary": result.margin_summary,
            "daily_dd_summary": result.daily_dd_summary,
            "max_daily_dd": result.max_daily_dd,
            "target_daily_dd": result.target_daily_dd,
            "daily_dd_full_history": result.daily_dd_full_history,
            "enforce_point_dd": result.enforce_point_dd,
            "actual_closed_valley_dd": result.actual_closed_valley_dd,
            "floating_dd_buffer": result.floating_dd_buffer,
            "floating_overlap_audit": result.floating_overlap_audit,
        }
