"""Construcción de portafolios UBS a partir de sets que pasaron robustez.

Módulo puro (sin Tkinter ni sqlite). La mecánica central: ``Trade.profit_loss`` es
lineal con el tamaño de lote (los reportes se generan a 0.01 lotes), de modo que
multiplicar los trades de una estrategia por ``k`` simula un lote de ``k * 0.01``.
El drawdown ($) del portafolio es exactamente lineal en un escalar global ``S``
porque los motores de DD de ``dd_excel`` son sumas de ``profit_loss`` y ``S > 0`` no
reordena los trades. Eso permite calibrar el portafolio a un tope de DD con una sola
multiplicación y luego redondear lotes (el redondeo hacia abajo sólo REDUCE el DD, así
que nunca supera el tope).
"""

from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .dd_excel import max_portfolio_drawdown_day, top_portfolio_valley_drawdowns
from .mt5_report import StrategyReport, parse_report


# Suelo en USD para el DD individual: evita divisiones por cero y que una estrategia
# con DD ~0 (degenerada / pocos trades) acapare todo el reparto de lotes.
DV_FLOOR = 0.5
# Estrategias con menos trades que esto se consideran no fiables para el portafolio.
MIN_TRADES = 10
# Exponente del sesgo por calidad según el tipo de portafolio. gamma=0 => risk parity
# puro (lote inverso al DD); a mayor gamma, más se concentran los lotes en las
# estrategias con mejor ratio retorno/DD.
GAMMA_BY_TYPE = {"conservative": 0.0, "balanced": 1.0, "aggressive": 2.0}
# Tope del profit factor para que estrategias sin pérdidas no exploten la calidad.
_PF_CAP = 10.0


ProgressCallback = Callable[[str], None]


@dataclass
class SelectedSet:
    candidate_id: int
    symbol: str
    period: str
    set_path: str
    is_report_path: str
    oos_report_path: str
    report: StrategyReport  # reporte combinado 2020-2026, a 0.01 lotes
    quality: float
    standalone_dd: float    # DD de equity (flotante) de MT5 @0.01, máx de IS/OOS


@dataclass
class StrategyAllocation:
    candidate_id: int
    symbol: str
    period: str
    set_path: str
    is_report_path: str
    oos_report_path: str
    standalone_dd: float    # DD de equity (flotante) de MT5 @0.01 lotes
    quality: float
    multiplier: float          # m_i (forma continua, unidades de 0.01 lote, mínimo 1)
    units: int                 # n_i = floor(S * m_i)
    lot: float                 # units * 0.01
    lot_size_step: float | None  # capital / units (EA Risk=2); None si excluida
    net_profit: float          # net profit combinado al lote asignado
    excluded: bool
    note: str


@dataclass
class AllocationResult:
    strategies: list[StrategyAllocation]
    scale_factor: float
    binding_constraint: str        # "valle" | "puntual" | "ninguno"
    target_valley_dd: float
    target_point_dd: float
    continuous_valley_dd: float
    continuous_point_dd: float
    actual_valley_dd: float
    actual_point_dd: float
    total_net_profit: float
    account_capital: float
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Métricas y escalado por estrategia
# --------------------------------------------------------------------------------------

def _trade_total(report: StrategyReport) -> float:
    return sum(trade.profit_loss for trade in report.trades)


def portfolio_valley_dd(reports: list[StrategyReport]) -> float:
    """DD del valle más grande del portafolio (pico-valle de la curva combinada)."""
    valleys = top_portfolio_valley_drawdowns(reports, 1)
    return valleys[0].drawdown if valleys else 0.0


def portfolio_point_dd(reports: list[StrategyReport]) -> float:
    """DD puntual: peor día agregando los cierres de todas las estrategias."""
    return max_portfolio_drawdown_day(reports).total_drawdown


def closed_valley_dd(report: StrategyReport) -> float:
    """DD del valle por operaciones CERRADAS, a 0.01 lotes (sólo fallback)."""
    return portfolio_valley_dd([report])


# Claves de MT5 para la "Reducción máxima de la equidad" (DD real con flotante, tick a
# tick). Es el único drawdown realista disponible: los reportes no traen el precio entre
# apertura y cierre, así que la equity flotante NO se puede reconstruir desde las
# operaciones (se verificó: el error es de 7x a 100x). Este escalar lo calcula MT5.
EQUITY_DD_KEYS = (
    "Equity Drawdown Maximal",
    "Reducción máxima de la equidad",
    "Reduccion maxima de la equidad",
    "Reducción máxima del capital",
    "Reduccion maxima del capital",
)


def _parse_amount(text: str) -> float:
    """Extrae el importe de una cadena tipo '10.76 (1.05%)' -> 10.76."""
    head = str(text or "").split("(")[0].strip().replace(" ", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", head)
    return float(match.group()) if match else 0.0


def extract_equity_dd(report: StrategyReport) -> float:
    """DD máximo de equity (con flotante) que reportó MT5, a 0.01 lotes."""
    for key in EQUITY_DD_KEYS:
        value = report.metrics.get(key)
        if value:
            amount = _parse_amount(value)
            if amount > 0:
                return amount
    return 0.0


def scale_report(report: StrategyReport, k: float) -> StrategyReport:
    """Devuelve una copia del reporte con el P/L de cada trade multiplicado por ``k``.

    ``k`` se expresa en unidades de 0.01 lote (la base de los reportes). No muta el
    reporte original (los reportes parseados se reutilizan a varias escalas).
    """
    scaled_trades = [
        dataclasses.replace(trade, profit_loss=trade.profit_loss * k)
        for trade in report.trades
    ]
    return dataclasses.replace(report, trades=scaled_trades)


def quality_score(report: StrategyReport, risk_dd: float) -> float:
    """Puntuación de calidad (>= 1) de los trades combinados, usando el DD de equity.

    ``risk_dd`` es la "Reducción máxima de la equidad" de MT5 (riesgo real). Forma
    multiplicativa para mantener el valor >= 1 (estable al elevar a ``gamma`` y al
    normalizar por el mínimo).
    """
    profits = [trade.profit_loss for trade in report.trades]
    if not profits:
        return 0.0
    total = sum(profits)
    gross_profit = sum(p for p in profits if p > 0)
    gross_loss = abs(sum(p for p in profits if p < 0))
    if gross_loss:
        profit_factor = min(gross_profit / gross_loss, _PF_CAP)
    else:
        profit_factor = _PF_CAP if gross_profit > 0 else 0.0
    return_dd = max(total, 0.0) / max(risk_dd, DV_FLOOR)
    return (
        (1.0 + return_dd)
        * (1.0 + 0.5 * max(profit_factor - 1.0, 0.0))
        * (1.0 + 0.25 * max(total, 0.0) / 1000.0)
    )


# --------------------------------------------------------------------------------------
# Combinación de los dos reportes (IS 2020-2024 + OOS 2025-2026)
# --------------------------------------------------------------------------------------

def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y")
    except (ValueError, TypeError):
        return None


def _min_date(a: str, b: str) -> str:
    da, db = _parse_date(a), _parse_date(b)
    if da and db:
        return a if da <= db else b
    return a or b


def _max_date(a: str, b: str) -> str:
    da, db = _parse_date(a), _parse_date(b)
    if da and db:
        return a if da >= db else b
    return a or b


def build_combined_report(is_report: StrategyReport, oos_report: StrategyReport) -> StrategyReport:
    """Une el reporte IS (2020-2024) y el OOS (2025-2026) en uno solo de 2020-2026.

    Los motores de DD reordenan por ``close_time``, así que basta con concatenar los
    trades. El periodo se mantiene en formato ``dd.mm.YYYY`` (lo que esperan los
    consumidores de ``period_start``/``period_end``).
    """
    trades = list(is_report.trades) + list(oos_report.trades)

    monthly: dict[int, dict[int, float]] = {}
    for source in (is_report.monthly, oos_report.monthly):
        for year, months in source.items():
            bucket = monthly.setdefault(year, {})
            for month, value in months.items():
                bucket[month] = bucket.get(month, 0.0) + value

    return dataclasses.replace(
        is_report,
        name=f"{is_report.name}__combined",
        trades=trades,
        monthly=monthly,
        period_start=_min_date(is_report.period_start, oos_report.period_start),
        period_end=_max_date(is_report.period_end, oos_report.period_end),
    )


# --------------------------------------------------------------------------------------
# Selección de sets robustos (auto top-N)
# --------------------------------------------------------------------------------------

def _normalize_symbol(symbol: str) -> str:
    return re.sub(r"(?<=[A-Za-z0-9])\.[A-Za-z0-9]+$", "", (symbol or "").strip()).upper()


def _logical_stem(set_path: str) -> str:
    stem = Path(set_path).stem
    return re.sub(r"^robust_\d{6}_", "", stem)


def _norm_path(value: str) -> str:
    try:
        return str(Path(value)).casefold()
    except (TypeError, ValueError):
        return str(value or "").casefold()


def select_robust_sets(
    rows: list,
    num_symbols: int,
    used_set_paths,
    *,
    parse: Callable[[Path], StrategyReport] = parse_report,
    progress: ProgressCallback | None = None,
) -> tuple[list[SelectedSet], list[str]]:
    """Elige el mejor set por símbolo entre los que pasaron robustez, top-N símbolos.

    ``rows`` son filas dict-like con: ``candidate_id``, ``set_path``, ``symbol``,
    ``target_symbol``, ``period``, ``is_report_path``, ``oos_report_path``.
    Devuelve ``(seleccionados, avisos)``.
    """
    warnings: list[str] = []
    used = {_norm_path(p) for p in used_set_paths}

    # Dedup por stem lógico (mismo set en varias runs): se queda el candidate_id mayor.
    by_stem: dict[str, object] = {}
    for row in rows:
        set_path = str(row["set_path"])
        if _norm_path(set_path) in used:
            continue
        stem = _logical_stem(set_path)
        current = by_stem.get(stem)
        if current is None or int(row["candidate_id"]) > int(current["candidate_id"]):
            by_stem[stem] = row

    candidates = list(by_stem.values())
    total = len(candidates)
    skipped_missing = 0
    skipped_trades = 0
    parsed: list[SelectedSet] = []
    for index, row in enumerate(candidates, start=1):
        if progress:
            progress(f"Analizando set {index}/{total}")
        is_path = Path(str(row["is_report_path"] or ""))
        oos_path = Path(str(row["oos_report_path"] or ""))
        if not is_path.is_file() or not oos_path.is_file():
            skipped_missing += 1
            continue
        try:
            is_report = parse(is_path)
            oos_report = parse(oos_path)
        except Exception:
            skipped_missing += 1
            continue
        combined = build_combined_report(is_report, oos_report)
        if len(combined.trades) < MIN_TRADES:
            skipped_trades += 1
            continue
        # Riesgo real = DD de equity (flotante) que reportó MT5; peor de los dos tramos.
        # Como cada backtest arranca de cero, el DD del periodo completo no es continuo:
        # el máximo de IS/OOS es la base prudente. Fallback al valle cerrado si falta.
        equity_dd = max(extract_equity_dd(is_report), extract_equity_dd(oos_report))
        if equity_dd <= 0:
            equity_dd = closed_valley_dd(combined)
        symbol = _normalize_symbol(
            str(row["target_symbol"] or row["symbol"] or combined.symbol)
        )
        parsed.append(
            SelectedSet(
                candidate_id=int(row["candidate_id"]),
                symbol=symbol,
                period=str(row["period"] or combined.timeframe),
                set_path=str(row["set_path"]),
                is_report_path=str(is_path),
                oos_report_path=str(oos_path),
                report=combined,
                quality=quality_score(combined, equity_dd),
                standalone_dd=equity_dd,
            )
        )

    if skipped_missing:
        warnings.append(f"{skipped_missing} candidato(s) omitido(s): reporte IS/OOS no encontrado o ilegible.")
    if skipped_trades:
        warnings.append(f"{skipped_trades} candidato(s) omitido(s): menos de {MIN_TRADES} operaciones combinadas.")

    # Mejor set por símbolo.
    best_by_symbol: dict[str, SelectedSet] = {}
    for item in parsed:
        current = best_by_symbol.get(item.symbol)
        if current is None or item.quality > current.quality:
            best_by_symbol[item.symbol] = item

    ranked = sorted(best_by_symbol.values(), key=lambda s: s.quality, reverse=True)
    selected = ranked[:num_symbols]
    if len(selected) < num_symbols:
        warnings.append(
            f"Sólo {len(selected)} símbolo(s) robusto(s) disponible(s); se pidieron {num_symbols}."
        )
    return selected, warnings


# --------------------------------------------------------------------------------------
# Reparto y calibración de lotes
# --------------------------------------------------------------------------------------

def compute_allocation(
    selected: list[SelectedSet],
    portfolio_type: str,
    capital: float,
    valley_pct: float,
    point_pct: float,
) -> AllocationResult:
    """Asigna lotes a cada estrategia cumpliendo ambos topes de DD.

    1. Peso de forma (risk parity sesgado por calidad): ``raw_w = (1/equityDD) * q**gamma``,
       donde equityDD es la "Reducción máxima de la equidad" de MT5 (riesgo REAL).
    2. Normaliza al mínimo => la estrategia más pequeña recibe 1 unidad de 0.01.
    3. Escala global ``S = min(Tv/Dv1, Tp/Dp1)``:
       - Tope de VALLE: sobre la SUMA de los DD de equity escalados. Es una cota superior
         garantizada del DD de equity de la cartera (el DD combinado nunca supera la suma
         de los individuales), así que el DD real NUNCA rebasa el tope.
       - Tope PUNTUAL: peor día de P/L de operaciones CERRADAS combinadas (realizado).
    4. Redondea hacia abajo a múltiplos de 0.01 y recalcula los DD reales (siempre <= tope).
    """
    warnings: list[str] = []
    gamma = GAMMA_BY_TYPE.get(portfolio_type, 1.0)

    raw_weights = [
        (1.0 / max(item.standalone_dd, DV_FLOOR)) * (max(item.quality, 1e-9) ** gamma)
        for item in selected
    ]
    min_weight = min(raw_weights)
    multipliers = [weight / min_weight for weight in raw_weights]

    target_valley = capital * valley_pct / 100.0
    target_point = capital * point_pct / 100.0

    # Valle = suma de los DD de equity (MT5) escalados por la forma (cota superior segura).
    valley_at_shape = sum(item.standalone_dd * m for item, m in zip(selected, multipliers))
    # Puntual = peor día de operaciones cerradas combinadas, a la forma S=1.
    shape_reports = [scale_report(item.report, m) for item, m in zip(selected, multipliers)]
    point_at_shape = portfolio_point_dd(shape_reports)

    ratio_valley = target_valley / valley_at_shape if valley_at_shape > 0 else math.inf
    ratio_point = target_point / point_at_shape if point_at_shape > 0 else math.inf
    scale = min(ratio_valley, ratio_point)
    if math.isinf(scale):
        scale = 1.0
        binding = "ninguno"
        warnings.append("DD no vinculante (sin drawdown medible); se usa escala S=1.")
    elif ratio_valley <= ratio_point:
        binding = "valle"
    else:
        binding = "puntual"

    strategies: list[StrategyAllocation] = []
    floored_reports: list[StrategyReport] = []
    equity_dd_scaled = 0.0
    for item, multiplier in zip(selected, multipliers):
        target_units = int(math.floor(scale * multiplier))
        if target_units < 1:
            strategies.append(
                StrategyAllocation(
                    candidate_id=item.candidate_id,
                    symbol=item.symbol,
                    period=item.period,
                    set_path=item.set_path,
                    is_report_path=item.is_report_path,
                    oos_report_path=item.oos_report_path,
                    standalone_dd=item.standalone_dd,
                    quality=item.quality,
                    multiplier=multiplier,
                    units=0,
                    lot=0.0,
                    lot_size_step=None,
                    net_profit=0.0,
                    excluded=True,
                    note="excluido (lote 0: DD objetivo muy bajo)",
                )
            )
            continue
        # LotPerBalance_step (EA Risk=2): $ de balance por cada 0.01 lote. Lo redondeamos
        # HACIA ARRIBA a 2 decimales para que el EA nunca opere MÁS de lo calibrado
        # (floor(capital/step) <= target_units), de modo que el tope de DD siga a salvo.
        # El lote mostrado y el DD real se derivan de lo que el EA operará a ese capital.
        step = math.ceil(capital / target_units * 100.0) / 100.0
        units = int(math.floor(capital / step))
        if units < 1:
            units = 1
        scaled = scale_report(item.report, units)
        floored_reports.append(scaled)
        equity_dd_scaled += item.standalone_dd * units
        strategies.append(
            StrategyAllocation(
                candidate_id=item.candidate_id,
                symbol=item.symbol,
                period=item.period,
                set_path=item.set_path,
                is_report_path=item.is_report_path,
                oos_report_path=item.oos_report_path,
                standalone_dd=item.standalone_dd,
                quality=item.quality,
                multiplier=multiplier,
                units=units,
                lot=round(units * 0.01, 2),
                lot_size_step=step,
                net_profit=_trade_total(scaled),
                excluded=False,
                note="",
            )
        )

    if floored_reports:
        # Valle = suma de los DD de equity reales escalados (cota superior segura).
        actual_valley = equity_dd_scaled
        # Puntual = peor día de operaciones cerradas combinadas, a los lotes finales.
        actual_point = portfolio_point_dd(floored_reports)
        total_net = sum(_trade_total(report) for report in floored_reports)
    else:
        actual_valley = 0.0
        actual_point = 0.0
        total_net = 0.0
        warnings.append(
            "Capital o % de DD demasiado bajos para fundar ni 0.01 lote por estrategia. "
            "Sube el capital o el % de DD objetivo."
        )

    excluded_count = sum(1 for strategy in strategies if strategy.excluded)
    if excluded_count and floored_reports:
        warnings.append(f"{excluded_count} estrategia(s) excluida(s) por lote 0 (DD objetivo bajo).")

    return AllocationResult(
        strategies=strategies,
        scale_factor=scale,
        binding_constraint=binding,
        target_valley_dd=target_valley,
        target_point_dd=target_point,
        continuous_valley_dd=valley_at_shape * scale,
        continuous_point_dd=point_at_shape * scale,
        actual_valley_dd=actual_valley,
        actual_point_dd=actual_point,
        total_net_profit=total_net,
        account_capital=capital,
        warnings=warnings,
    )


# --------------------------------------------------------------------------------------
# Export de sets con el lotaje calibrado
# --------------------------------------------------------------------------------------

def set_current_value(text: str, key: str, value: object) -> tuple[str, bool]:
    """Reemplaza el valor ACTUAL (primer campo antes de ``||``) de ``key`` en un .set.

    Conserva el resto de campos (rango/paso de optimización). Devuelve (texto, encontrado).
    """
    out: list[str] = []
    found = False
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(";"):
            lhs, rhs = line.split("=", 1)
            if lhs.strip() == key:
                if "||" in rhs:
                    parts = rhs.split("||")
                    parts[0] = str(value)
                    rhs = "||".join(parts)
                else:
                    rhs = str(value)
                line = f"{lhs}={rhs}"
                found = True
        out.append(line)
    return "\n".join(out), found


def apply_portfolio_lot_text(text: str, lot_size_step: float) -> tuple[str, int, bool]:
    """Configura el .set en modo balance (Risk=2) con el LotPerBalance_step calibrado.

    El paso se redondea HACIA ARRIBA a entero (más $ por 0.01 lote => el EA opera <= lo
    calibrado => el tope de DD sigue a salvo). El EA aplicará
    ``Lots = floor(AccountBalance / LotPerBalance_step) * 0.01``.
    Devuelve (texto, step_entero, se_encontró_la_clave).
    """
    step_int = max(1, int(math.ceil(lot_size_step)))
    text, _ = set_current_value(text, "Risk", 2)
    text, found_step = set_current_value(text, "LotPerBalance_step", step_int)
    return text, step_int, found_step
