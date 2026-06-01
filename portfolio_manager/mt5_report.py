from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from lxml import html


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class Deal:
    timestamp: datetime
    direction: str
    commission: float
    swap: float
    profit: float

    @property
    def net_profit(self) -> float:
        return self.commission + self.swap + self.profit


@dataclass
class RawDeal:
    timestamp: datetime
    ticket: str
    symbol: str
    trade_type: str
    direction: str
    volume: float
    price: float
    order: str
    commission: float
    swap: float
    profit: float
    balance: float
    comment: str

    @property
    def net_profit(self) -> float:
        return self.commission + self.swap + self.profit


@dataclass
class Trade:
    ticket: str
    trade_type: str
    open_time: datetime
    open_price: float
    size: float
    close_time: datetime
    close_price: float
    profit_loss: float
    comment: str


@dataclass
class StrategyReport:
    path: Path
    name: str
    expert: str
    symbol: str
    timeframe: str
    period_start: str
    period_end: str
    initial_deposit: float
    metrics: dict[str, str]
    monthly: dict[int, dict[int, float]]
    trades: list[Trade]
    raw_deals: list[RawDeal]
    image_paths: dict[str, Path]
    set_path: Path | None


def parse_report(path: Path) -> StrategyReport:
    doc = html.parse(str(path))
    rows = [_row_cells(row) for row in doc.xpath("//tr")]

    config = _parse_config(rows)
    metrics = _parse_results(rows)
    raw_deals = _parse_raw_deals(rows)
    deals = _raw_to_deals(raw_deals)
    trades = _build_trades(raw_deals)

    monthly: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for deal in deals:
        if deal.direction.lower() == "out":
            monthly[deal.timestamp.year][deal.timestamp.month] += deal.net_profit

    symbol = _clean_symbol(_first_value(config, "Symbol", "Simbolo", "Símbolo"))
    timeframe, cfg_start, cfg_end = _parse_period(_first_value(config, "Period", "Periodo", "Período"))
    first_trade = next((d.timestamp for d in deals if d.direction.lower() == "out"), None)
    last_trade = next((d.timestamp for d in reversed(deals) if d.direction.lower() == "out"), None)

    start = _format_date(first_trade) if first_trade else cfg_start
    end = _format_date(last_trade) if last_trade else cfg_end

    return StrategyReport(
        path=path,
        name=path.stem,
        expert=config.get("Experto", ""),
        symbol=symbol,
        timeframe=timeframe,
        period_start=start,
        period_end=end,
        initial_deposit=_initial_deposit(deals),
        metrics=metrics,
        monthly={year: dict(months) for year, months in monthly.items()},
        trades=trades,
        raw_deals=raw_deals,
        image_paths=_find_images(path),
        set_path=path.with_suffix(".set") if path.with_suffix(".set").exists() else None,
    )


def _row_cells(row) -> list[str]:
    return [" ".join(cell.itertext()).replace("\xa0", " ").strip() for cell in row.xpath("./td|./th")]


def _parse_config(rows: list[list[str]]) -> dict[str, str]:
    config: dict[str, str] = {}
    for cells in rows:
        if len(cells) < 2:
            continue
        key = cells[0].rstrip(":").strip()
        value = cells[1].strip()
        if key in {"Expert", "Symbol", "Period", "Experto", "Símbolo", "Simbolo", "Período", "Periodo"}:
            config[key] = value
    return config


def _parse_results(rows: list[list[str]]) -> dict[str, str]:
    metrics: dict[str, str] = {}
    in_results = False
    for cells in rows:
        if cells in (["Results"], ["Resultados"]):
            in_results = True
            continue
        if in_results and cells in (["Orders"], ["Órdenes"]):
            break
        if not in_results:
            continue
        for i in range(0, len(cells) - 1, 2):
            key = cells[i].rstrip(":").strip()
            value = cells[i + 1].strip()
            if key and value:
                metrics[key] = value
    return metrics


def _parse_deals(rows: list[list[str]]) -> list[Deal]:
    return _raw_to_deals(_parse_raw_deals(rows))


def _parse_raw_deals(rows: list[list[str]]) -> list[RawDeal]:
    deals: list[RawDeal] = []
    headers: list[str] | None = None
    in_deals = False

    for cells in rows:
        if cells in (["Deals"], ["Transacciones"]):
            in_deals = True
            headers = None
            continue
        if not in_deals:
            continue
        if headers is None:
            headers = cells
            continue
        if len(cells) < 12:
            continue
        if not _looks_like_datetime(cells[0]):
            continue

        row = dict(zip(headers, cells))
        deals.append(
            RawDeal(
                timestamp=_parse_datetime(_first_value(row, "Time", "Fecha/Hora")),
                ticket=_first_value(row, "Deal", "Transacción"),
                symbol=_first_value(row, "Symbol", "Símbolo"),
                trade_type=_first_value(row, "Type", "Tipo"),
                direction=_first_value(row, "Direction", "Dirección"),
                volume=_to_float(_first_value(row, "Volume", "Volumen", default="0")),
                price=_to_float(_first_value(row, "Price", "Precio", default="0")),
                order=_first_value(row, "Order", "Orden"),
                commission=_to_float(_first_value(row, "Commission", "Comisión", default="0")),
                swap=_to_float(row.get("Swap", "0")),
                profit=_to_float(_first_value(row, "Profit", "Beneficio", default="0")),
                balance=_to_float(row.get("Balance", "0")),
                comment=_first_value(row, "Comment", "Comentario"),
            )
        )

    return deals


def _raw_to_deals(raw_deals: list[RawDeal]) -> list[Deal]:
    deals: list[Deal] = []
    for raw in raw_deals:
        if raw.trade_type.lower() == "balance":
            deals.append(
                Deal(
                    timestamp=raw.timestamp,
                    direction="balance",
                    commission=raw.commission,
                    swap=raw.swap,
                    profit=raw.profit,
                )
            )
            continue
        deals.append(
            Deal(
                timestamp=raw.timestamp,
                direction=raw.direction,
                commission=raw.commission,
                swap=raw.swap,
                profit=raw.profit,
            )
        )

    return deals


def _build_trades(raw_deals: list[RawDeal]) -> list[Trade]:
    open_positions: dict[tuple[str, str], list[RawDeal]] = defaultdict(list)
    trades: list[Trade] = []

    for deal in raw_deals:
        trade_type = deal.trade_type.lower()
        direction = deal.direction.lower()
        if trade_type not in {"buy", "sell"}:
            continue
        if direction == "in":
            open_positions[(deal.symbol, trade_type)].append(deal)
            continue
        if direction != "out":
            continue

        open_type = "buy" if trade_type == "sell" else "sell"
        queue = open_positions.get((deal.symbol, open_type), [])
        if not queue:
            continue
        opened = queue.pop(0)
        trades.append(
            Trade(
                ticket=opened.ticket,
                trade_type=opened.trade_type.capitalize(),
                open_time=opened.timestamp,
                open_price=opened.price,
                size=opened.volume,
                close_time=deal.timestamp,
                close_price=deal.price,
                profit_loss=deal.net_profit,
                comment=deal.comment,
            )
        )

    trades.sort(key=lambda trade: trade.open_time)
    return trades


def _find_images(report_path: Path) -> dict[str, Path]:
    base = report_path.with_suffix("")
    candidates = {
        "Balance chart": base.with_suffix(".png"),
        "History": base.parent / f"{base.name}-hst.png",
        "Holding": base.parent / f"{base.name}-holding.png",
        "MFE/MAE": base.parent / f"{base.name}-mfemae.png",
    }
    return {label: path for label, path in candidates.items() if path.exists()}


def _parse_period(value: str) -> tuple[str, str, str]:
    match = re.search(r"(.+?)\s*\((\d{4}\.\d{2}\.\d{2})\s*-\s*(\d{4}\.\d{2}\.\d{2})\)", value)
    if not match:
        return value.strip(), "", ""
    timeframe = _normalize_timeframe(match.group(1).strip())
    return timeframe, _date_text(match.group(2)), _date_text(match.group(3))


def _normalize_timeframe(value: str) -> str:
    mapping = {"Daily": "D1", "Weekly": "W1", "Monthly": "MN1"}
    return mapping.get(value, value)


def _clean_symbol(value: str) -> str:
    value = value.strip()
    return value.split(".")[0] if "." in value else value


def _first_value(values: dict[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        value = values.get(key)
        if value:
            return value
    return default


def _initial_deposit(deals: list[Deal]) -> float:
    for deal in deals:
        if deal.direction == "balance":
            return deal.profit
    return 0.0


def _looks_like_datetime(value: str) -> bool:
    return bool(re.match(r"^\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}$", value or ""))


def _parse_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y.%m.%d %H:%M:%S")


def _date_text(value: str) -> str:
    return datetime.strptime(value, "%Y.%m.%d").strftime("%d.%m.%Y")


def _format_date(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def _to_float(value: str) -> float:
    cleaned = value.replace(" ", "").replace("%", "").replace(",", ".").strip()
    if not cleaned:
        return 0.0
    match = re.match(r"([-+]?\d+(?:\.\d+)?)", cleaned)
    return float(match.group(1)) if match else 0.0
