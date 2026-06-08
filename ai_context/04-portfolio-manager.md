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

- **Robust set input**: only uses rows where the base candidate and robustness
  result are both accepted. The base report (`candidates.report_path`) covers
  2020-2024 and the robustness report (`candidate_robustness.report_path`)
  covers 2025-2026.
- **Historical curve**: both periods are treated as consecutive parts of one
  2020-2026 history. The module reconstructs accumulated P/L from closed trades,
  validates net profit against report metrics when available, and merges the
  two curves.
- **Eligibility filters**: accepted, unused, parseable curve, minimum combined
  trades, and positive combined net. Do not add OOS/degradation filters here.
- **Ranking and selection**: candidates are ranked, then limited by top-K per
  symbol before optimization.
- **Discrete lot model**: `1 unit = 0.01 lot`. The optimizer assigns integer
  units and recalculates the full combined portfolio curve after every proposed
  increment.
- **DD limits**: valley DD and point DD are evaluated on the combined portfolio
  curve. Candidate increments are rejected when either configured DD cap is
  exceeded.
- **Local search**: optional one-unit swaps among selected strategies are kept
  only if they increase net profit and remain inside both DD limits.
- **No global scaling**: do not reintroduce risk-parity allocation, a global
  scale factor (`S = target_dd/current_dd`), StartLots validation, or automatic
  lot normalization.

### Public API

| Function | Purpose |
|----------|---------|
| `parse_mt5_html_report(path)` | Parse MT5 HTML through `mt5_report.parse_report` and build a closed-trade curve |
| `build_robust_strategy_set(base, robust)` | Merge 2020-2024 + 2025-2026 period reports |
| `load_robust_sets_from_rows(rows, used_set_paths, min_trades)` | Convert DB candidate rows into optimizer-ready sets |
| `summarize_availability(sets)` | Count total/eligible candidates by symbol |
| `optimize_portfolio(sets, config)` | Discrete unit optimizer with DD constraints and decision log |
| `calc_valley_dd(curve)` | Maximum peak-to-trough drawdown for a curve |
| `calc_point_dd(curve)` | Worst single-step drop for a curve |
| `apply_portfolio_lot_text(text, step)` | Patch .set: `Risk=2` + integer `LotPerBalance_step` |
| `set_current_value(text, key, value)` | Replace first field (before `||`) of a .set key |

### DB tables (in `outputs/ubs_memory.sqlite`)

- `portfolios`: one row per generated portfolio (inputs, results, `metrics_json`).
- `portfolio_allocations`: canonical per-strategy allocation table.
- `portfolio_decision_log`: optimizer audit trail for accepted/rejected unit
  increments and optional local-search swaps.
- `portfolio_members`: legacy-compatible per-strategy table. `set_path` remains
  part of the global exclusion key and is freed automatically when the portfolio
  is deleted.

### Export

"Exportar sets" writes each member .set to a user-chosen folder with `Risk=2`
and the integer `LotPerBalance_step` derived from the selected units, plus a
human-readable `PORTAFOLIO_<id>_resumen.txt`.

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
