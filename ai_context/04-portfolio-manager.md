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

## UBS Portfolio Module (`ubs_portfolio.py`)

Pure math module (no Tkinter, no sqlite) for the "UBS Portafolio" tab.
**Do not add UI or DB code here.**

### Key design decisions

- **DD metric**: uses MT5's precomputed `"Equity Drawdown Maximal"` /
  `"Reducción máxima de la equidad"` from `report.metrics` as per-strategy risk.
  Closed-trade/balance DD understates risk 6–100× for scalper/grid EAs — never
  use it for lot sizing. Falls back to `closed_valley_dd` only when the equity
  scalar is absent.
- **Two-period backtest**: IS 2020-2024 (`candidates.report_path`) + OOS
  2025-2026 (`candidate_robustness.report_path`). Both at 0.01 lots.
  `build_combined_report()` concatenates trades and sums monthly dicts.
- **Lot linearity**: `Trade.profit_loss ∝ lot`. Scaling all trades by `k`
  simulates lot `k×0.01`. DD($) is exactly linear in the scale factor `S`.
- **Risk-parity with quality tilt**: `raw_w_i = (1/equityDD_i) * quality_i^gamma`;
  `gamma` by portfolio type: conservative=0 (pure inverse-DD), balanced=1,
  aggressive=2.
- **EA lot reproduction (`Risk=2` mode)**: EA computes
  `Lots = floor(AccountBalance / LotPerBalance_step) * 0.01`.
  Step is rounded **up** to the nearest cent so the EA never over-trades the
  calibrated lot. Displayed lots are then `floor(capital / step_int) * 0.01`.
- **Valley tope**: sum of equity DDs × units (guaranteed upper bound — combined
  equity DD can never exceed the sum of individual equity DDs).
- **Point tope**: worst closed-day combined P/L via `dd_excel.max_portfolio_drawdown_day`.

### Public API

| Function | Purpose |
|----------|---------|
| `extract_equity_dd(report)` | Parse equity DD scalar from `report.metrics` |
| `scale_report(report, k)` | Deep-copy with all `profit_loss × k` |
| `build_combined_report(is_r, oos_r)` | Concat trades, merge monthly, span period |
| `quality_score(report, risk_dd)` | Multiplicative quality >= 1 |
| `select_robust_sets(rows, N, used)` | Top-N symbols, best-set-per-symbol, deduplicated |
| `compute_allocation(selected, type, capital, valley_pct, point_pct)` | Full lot calibration |
| `apply_portfolio_lot_text(text, step)` | Patch .set: `Risk=2` + integer `LotPerBalance_step` |
| `set_current_value(text, key, value)` | Replace first field (before `||`) of a .set key |

### DB tables (in `outputs/ubs_memory.sqlite`)

- `portfolios`: one row per generated portfolio (inputs, results, `metrics_json`).
- `portfolio_members`: one row per strategy in a portfolio. `set_path` is the
  global exclusion key — queried by `SELECT set_path FROM portfolio_members` to
  prevent reuse. Freed automatically when the portfolio is deleted.

### Export

"Exportar sets" writes each member .set to a user-chosen folder with `Risk=2`
and the integer `LotPerBalance_step` applied, plus a human-readable
`PORTAFOLIO_<id>_resumen.txt`.

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

