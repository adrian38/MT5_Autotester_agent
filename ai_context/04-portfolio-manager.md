# Portfolio Manager

## Purpose

The Portfolio Manager reads MT5 Strategy Tester HTML reports and produces
Excel analysis workbooks. It is exposed in the UI under the `Portfolio` panel
and implemented under `portfolio_manager/`.

## Public Generator Functions

`portfolio_manager/generator.py` exports the functions used by the UI:

- `find_report_files(input_dir)`
- `generate_workbook(...)` -> `ALL_STRATEGIES.xlsx`
- `generate_drawdown_workbook(...)` -> `ALL_STRATEGIES_DD.xlsx`
- `generate_portfolio_drawdown_workbook(...)` -> `PORTFOLIO_DD.xlsx`
- `generate_portfolio_valley_drawdown_workbook(...)` -> `PORTFOLIO_VALLEY_DD.xlsx`
- `generate_top_portfolio_valleys_workbook(...)` -> `PORTFOLIO_TOP5_VALLEYS.xlsx`
- `generate_dd_threshold_workbook(...)` -> `DD_THRESHOLD.xlsx`

The UI calls these in a background thread and passes a progress callback.

## Parser Model

`mt5_report.py` parses each `.htm/.html` file into:

- `StrategyReport`
- `Trade`
- `RawDeal`
- `Deal`

Important parsed fields:

- report path/name
- expert name
- symbol
- timeframe and period
- initial deposit
- MT5 result metrics
- monthly P/L
- reconstructed trades
- raw deals
- related chart images
- adjacent `.set` file when present

## MT5 Report Language Support

The parser must support English and Spanish MT5 report labels. Current reports
in this workspace use English labels:

- Config: `Expert`, `Symbol`, `Period`
- Sections: `Results`, `Orders`, `Deals`
- Deal headers: `Time`, `Deal`, `Symbol`, `Type`, `Direction`, `Volume`,
  `Price`, `Order`, `Commission`, `Swap`, `Profit`, `Balance`, `Comment`
- Drawdown metrics: `Balance Drawdown Maximal`,
  `Balance Drawdown Relative`

Older/localized reports may use Spanish labels:

- `Experto`, `Símbolo`/`Simbolo`, `Período`/`Periodo`
- `Resultados`, `Órdenes`, `Transacciones`
- `Fecha/Hora`, `Transacción`, `Tipo`, `Dirección`, `Volumen`, `Precio`,
  `Orden`, `Comisión`, `Beneficio`, `Comentario`
- `Reducción máxima del balance`, `Reducción relativa del balance`

When changing parser logic, test both label families where possible.

## Workbook Builders

### `excel.py`

Builds `ALL_STRATEGIES.xlsx`:

- `INDEX` sheet with one row per strategy and KPI columns.
- One detailed sheet per strategy.
- KPI grid, monthly performance table, stats, embedded chart images, trades.

KPI helpers are calculated from reconstructed trades and MT5 metrics. Drawdown
helpers read both English and Spanish metric names.

### `dd_excel.py`

Builds drawdown-focused workbooks:

- Per-strategy max daily drawdown sheets.
- Worst portfolio day.
- Portfolio valley drawdown from combined chronological trades.
- Top 5 portfolio valleys.
- DD threshold filter workbook with `CUMPLEN` and `TODAS`.

The module assumes an initial portfolio account balance of `1000.0`
(`PORTFOLIO_ACCOUNT_BALANCE`).

## Verification Pattern

For parser or workbook changes, run a real generation smoke test:

```powershell
python - <<'PY'
from pathlib import Path
from portfolio_manager.generator import generate_workbook

reports = generate_workbook(
    Path("reports"),
    Path("outputs/ALL_STRATEGIES.xlsx"),
    progress=print,
)
print(len(reports))
PY
```

Then inspect `INDEX` with `openpyxl` and confirm rows are not empty/zero:

```powershell
python - <<'PY'
from openpyxl import load_workbook
wb = load_workbook("outputs/ALL_STRATEGIES.xlsx", data_only=True)
ws = wb["INDEX"]
for row in ws.iter_rows(min_row=1, max_row=5, values_only=True):
    print(row[:12])
PY
```

For full Portfolio UI confidence, run all generators and confirm workbook
sheet/row counts.

