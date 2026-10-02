"""Seleccion y filtrado de los sets candidatos al portafolio."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .mt5_report import StrategyReport, parse_report
from ubs.path_utils import resolve_workspace_path
from .ubs_portfolio import (
    PortfolioAvailability,
    ProgressCallback,
    RobustStrategySet,
    portfolio_symbol_key,
)
from .ubs_portfolio_reports import (
    build_robust_strategy_set,
    period_report_from_strategy_report,
)
from .ubs_portfolio_utils import (
    _coerce_month_end,
    _first_existing_report_path,
    _latest_month_from_monthly,
    _logical_stem,
    _month_window,
    _norm_path,
    _row_int,
    _row_value,
    portfolio_display_symbol,
    score_set_for_portfolio,
)


def summarize_robust_rows(rows: Iterable[object], used_set_paths: Iterable[str]) -> PortfolioAvailability:
    used = {_norm_path(path) for path in used_set_paths}
    robust_accepted = 0
    already_used = 0
    by_symbol: dict[str, int] = {}
    seen: set[str] = set()
    for row in rows:
        set_path = str(_row_value(row, "set_path", default=""))
        if not set_path or set_path in seen:
            continue
        seen.add(set_path)
        robust_accepted += 1
        symbol = portfolio_display_symbol(str(_row_value(row, "target_symbol", "symbol", default="")))
        if _norm_path(set_path) in used:
            already_used += 1
            continue
        by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
    available = sum(by_symbol.values())
    return PortfolioAvailability(
        robust_accepted=robust_accepted,
        already_used=already_used,
        available=available,
        symbols_available=len(by_symbol),
        by_symbol=dict(sorted(by_symbol.items())),
    )


def _latest_robust_rows(rows: Sequence[object]) -> list[object]:
    latest_by_stem: dict[str, object] = {}
    for row in rows:
        set_path = str(_row_value(row, "set_path", default=""))
        if not set_path:
            continue
        account_type = str(_row_value(row, "account_type", default="")).strip().upper()
        stem = _logical_stem(set_path)
        if account_type:
            stem = f"{account_type}:{stem}"
        current = latest_by_stem.get(stem)
        if current is None or _row_int(row, "source_candidate_id", "candidate_id") > _row_int(
            current, "source_candidate_id", "candidate_id"
        ):
            latest_by_stem[stem] = row
    return list(latest_by_stem.values())


def _load_robust_strategy(row, set_path: str, is_path: Path, oos_path: Path, parse) -> RobustStrategySet:
    is_period = period_report_from_strategy_report(parse(is_path), "2020_2024")
    oos_period = period_report_from_strategy_report(parse(oos_path), "2025_2026")
    target_symbol = str(_row_value(row, "target_symbol", "symbol", default=is_period.symbol))
    return build_robust_strategy_set(
        set_id=set_path,
        candidate_id=str(_row_value(row, "candidate_id", "id", default=set_path)),
        symbol=target_symbol,
        timeframe=str(_row_value(row, "period", "timeframe", default=is_period.timeframe)),
        strategy_family=str(_row_value(row, "family", "strategy_family", default="")),
        robustness_status="accepted",
        already_used=False,
        report_2020_2024=is_period,
        report_2025_2026=oos_period,
        set_path=set_path,
        is_report_path=str(is_path),
        oos_report_path=str(oos_path),
    )


def load_robust_sets_from_rows(
    rows: Sequence[object],
    used_set_paths: Iterable[str],
    *,
    parse: Callable[[Path], StrategyReport] = parse_report,
    progress: ProgressCallback | None = None,
) -> tuple[list[RobustStrategySet], list[str]]:
    warnings: list[str] = []
    used = {_norm_path(path) for path in used_set_paths}
    loaded: list[RobustStrategySet] = []
    skipped_missing = 0
    skipped_parse = 0
    missing_examples: list[str] = []
    parse_examples: list[str] = []
    candidates = _latest_robust_rows(rows)
    for index, row in enumerate(candidates, start=1):
        set_path = str(_row_value(row, "set_path", default=""))
        if _norm_path(set_path) in used:
            continue
        if progress:
            progress(f"Analizando set Final Tick OK {index}/{len(candidates)}")
        is_path = resolve_workspace_path(str(_row_value(row, "is_report_path", "report_path", default="")))
        oos_path = resolve_workspace_path(str(_row_value(row, "oos_report_path", "robust_report_path", default="")))
        if not is_path.is_file() or not oos_path.is_file():
            skipped_missing += 1
            if len(missing_examples) < 5:
                missing_parts = []
                if not is_path.is_file():
                    missing_parts.append(f"base={is_path.name or '-'}")
                if not oos_path.is_file():
                    missing_parts.append(f"robustez={oos_path.name or '-'}")
                missing_examples.append(
                    f"{Path(set_path).name}: " + ", ".join(missing_parts)
                )
            continue
        try:
            loaded.append(_load_robust_strategy(row, set_path, is_path, oos_path, parse))
        except Exception as exc:
            skipped_parse += 1
            if len(parse_examples) < 5:
                message = str(exc).strip() or "sin detalle"
                parse_examples.append(
                    f"{Path(set_path).name}: {type(exc).__name__}: {message}"
                )
            continue

    if skipped_missing:
        warnings.append(f"{skipped_missing} candidato(s) omitido(s): faltan reportes base o robustez.")
        warnings.append("Ejemplos de reportes ausentes: " + " | ".join(missing_examples))
    if skipped_parse:
        warnings.append(f"{skipped_parse} candidato(s) omitido(s): reporte ilegible o curva invalida.")
        warnings.append("Ejemplos de errores de carga: " + " | ".join(parse_examples))
    return loaded, warnings


def filter_eligible_sets(
    sets: list[RobustStrategySet],
    min_trades_2020_2026: int = 100,
) -> list[RobustStrategySet]:
    eligible: list[RobustStrategySet] = []
    for strategy in sets:
        if strategy.robustness_status != "accepted":
            continue
        if strategy.already_used:
            continue
        if not strategy.curve_2020_2026_001:
            continue
        if strategy.trades_2020_2026 < min_trades_2020_2026:
            continue
        if strategy.net_profit_2020_2026_001 <= 0:
            continue
        eligible.append(strategy)
    return eligible


def recent_positive_month_count(
    monthly: dict[int, dict[int, float]],
    end_date: str | datetime | None = None,
    *,
    window_months: int = 6,
) -> int:
    end = _coerce_month_end(end_date)
    if end is None:
        end = _latest_month_from_monthly(monthly)
    if end is None or window_months <= 0:
        return 0
    count = 0
    for year, month in _month_window(end.year, end.month, window_months):
        if float(monthly.get(year, {}).get(month, 0.0)) > 0:
            count += 1
    return count


def filter_rows_by_recent_positive_months(
    rows: Sequence[object],
    *,
    min_positive_months: int = 3,
    window_months: int = 6,
    parse: Callable[[Path], StrategyReport] = parse_report,
    progress: ProgressCallback | None = None,
) -> tuple[list[object], list[str]]:
    filtered: list[object] = []
    skipped_no_report = 0
    skipped_parse = 0
    skipped_months = 0

    for index, row in enumerate(rows, start=1):
        if progress:
            progress(f"Filtrando meses positivos {index}/{len(rows)}")
        report_path = _first_existing_report_path(
            row,
            "final_tick_report_path",
            "real_tick_report_path",
            "final_ohlc_report_path",
            "ohlc_report_path",
        )
        if report_path is None:
            skipped_no_report += 1
            continue
        try:
            report = parse(report_path)
            end_date = str(_row_value(row, "final_tick_to_date", "to_date", default=""))
            positives = recent_positive_month_count(
                report.monthly,
                end_date or report.period_end,
                window_months=window_months,
            )
        except Exception:
            skipped_parse += 1
            continue
        if positives >= min_positive_months:
            filtered.append(row)
        else:
            skipped_months += 1

    warnings: list[str] = []
    if skipped_months or skipped_no_report or skipped_parse:
        warnings.append(
            f"Filtro {min_positive_months}/{window_months} meses positivos: "
            f"{skipped_months} omitido(s) por meses insuficientes"
            + (f", {skipped_no_report} sin reporte Final Tick 6M" if skipped_no_report else "")
            + (f", {skipped_parse} con reporte ilegible" if skipped_parse else "")
            + "."
        )
    return filtered, warnings


def select_top_k_per_symbol(
    sets: list[RobustStrategySet],
    top_k_per_symbol: int = 3,
    max_total_candidates: int | None = 30,
    *,
    min_trades_2020_2026: int = 100,
) -> list[RobustStrategySet]:
    grouped: dict[str, list[RobustStrategySet]] = {}
    for strategy in sets:
        grouped.setdefault(portfolio_symbol_key(strategy.symbol), []).append(strategy)

    selected: list[RobustStrategySet] = []
    for group in grouped.values():
        ordered = sorted(
            group,
            key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
            reverse=True,
        )
        selected.extend(ordered[:top_k_per_symbol])

    selected = sorted(
        selected,
        key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
        reverse=True,
    )
    if max_total_candidates is not None:
        selected = _limit_candidates_with_group_reserve(
            selected,
            max_total_candidates,
            min_trades_2020_2026,
        )
    return selected


def _limit_candidates_with_group_reserve(
    candidates: list[RobustStrategySet],
    max_total_candidates: int,
    min_trades_2020_2026: int,
) -> list[RobustStrategySet]:
    if max_total_candidates <= 0:
        return []
    if len(candidates) <= max_total_candidates:
        return candidates

    ordered = sorted(
        candidates,
        key=lambda item: score_set_for_portfolio(item, min_trades_2020_2026),
        reverse=True,
    )
    symbols: dict[str, list[RobustStrategySet]] = {}
    for candidate in ordered:
        symbols.setdefault(portfolio_symbol_key(candidate.symbol), []).append(candidate)

    selected: list[RobustStrategySet] = []
    selected_ids: set[str] = set()
    ordered_symbols = sorted(
        symbols.values(),
        key=lambda group: score_set_for_portfolio(group[0], min_trades_2020_2026),
        reverse=True,
    )
    for group in ordered_symbols:
        if len(selected) >= max_total_candidates:
            break
        candidate = group[0]
        selected.append(candidate)
        selected_ids.add(candidate.set_id)

    for candidate in ordered:
        if len(selected) >= max_total_candidates:
            break
        if candidate.set_id in selected_ids:
            continue
        selected.append(candidate)
        selected_ids.add(candidate.set_id)
    return selected
