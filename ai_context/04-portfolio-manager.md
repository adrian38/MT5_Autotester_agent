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

- **Final Tick-gated input**: UBS Portafolio reads both ECN and PRO memories
  and only offers rows where the base candidate, robustness result, and Final
  Tick result are all accepted. The portfolio curve still uses the base report
  (`candidates.report_path`) plus the robustness report
  (`candidate_robustness.report_path`) as the 2020-2026 history; Final Tick is
  the eligibility gate, not the curve source.
- **Historical curve**: both periods are treated as consecutive parts of one
  2020-2026 history. The module reconstructs accumulated P/L from closed trades,
  validates net profit against report metrics when available, and merges the
  two curves.
- **Eligibility filters**: Final Tick accepted, not locked by the matching
  portfolio class (Conservative/Balanced together; Aggressive separately),
  parseable curve, minimum combined trades, and positive combined net. Do not
  add OOS/degradation filters here.
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
- **Multi-start search**: deterministic valid perturbations of the local optimum
  are re-optimized; only a strictly better final net result is retained.
- **DD reserve**: a configurable percentage reduces both effective DD budgets,
  leaving explicit operating headroom below the nominal user limits.
- **Stress/bootstrap**: every final allocation receives a deterministic
  1,000-run circular moving-block bootstrap over its chronological P/L
  increments. It stores valley-DD P50/P95 and probabilities of exceeding the
  nominal and effective valley limits. P95 above the effective limit is an
  alert only; it does not invalidate the proposal.
- **No global scaling**: do not reintroduce risk-parity allocation, a global
  scale factor (`S = target_dd/current_dd`), StartLots validation, or automatic
  lot normalization.
- **Quarantine is a hard gate**: rows in `portfolio_quarantine` are excluded
  before parsing or optimization across both ECN and PRO memories.
- **Saved portfolio repair**: repair starts with every remaining member at its
  saved unit count and freezes those allocations. If quarantine removed a
  diversifying curve and the remainder now violates DD, the repair reduces only
  the minimum existing units needed to make a replacement feasible. It restores
  the pre-quarantine strategy count and optimizes units only on the replacement.
  The repaired result replaces saved allocations only after all constraints pass.

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
| `bootstrap_valley_drawdown(curve, ...)` | Deterministic block-bootstrap DD P50/P95 and limit exceedance probabilities |
| `apply_portfolio_lot_text(text, step)` | Patch .set: `Risk=2` + integer `LotPerBalance_step` |
| `set_current_value(text, key, value)` | Replace first field (before `||`) of a .set key |

### DB tables (in `outputs/ubs_memory.sqlite`)

- `portfolios`: one row per generated portfolio (inputs, results, `metrics_json`).
- `portfolio_allocations`: canonical per-strategy allocation table.
- `portfolio_decision_log`: optimizer audit trail for accepted/rejected unit
  increments and optional local-search swaps.
- `portfolio_members`: legacy-compatible per-strategy table. `set_path` remains
  part of the global exclusion key and is freed automatically when the portfolio
  is deleted. Aggressive portfolios use their own lock pool and do not conflict
  with Conservative/Balanced. Conservative and Balanced share a lock pool.
- `portfolio_quarantine`: account-scoped hard exclusion keyed by `set_path`,
  including source candidate, symbol/TF, source portfolio, reason, and date.
- `portfolio_versions`: compressed before-change snapshots of the portfolio row,
  allocations, compatibility members, and optimizer decision log. Used by
  "Deshacer recomposicion".

Completion is non-destructive until confirmation: the UI presents a set-level
before/after units table and applies the result only from "Aplicar cambios".

Generation and full reoptimization expose three comparable profiles over the
same eligible pool: profit, balanced, and DD-margin. Balanced enforces at least
15% DD reserve; DD-margin uses Conservative scoring and at least 25% reserve.
The selected row controls the before/after allocation diff and is the only
proposal eligible for save/apply.

The comparison also shows bootstrap DD P50/P95, `P(> nominal)`,
`P(> effective)`, and an `OK`/`ALERTA` state. Alert rows are red when P95 is
above the effective valley-DD limit. The full `stress_bootstrap` payload is
stored in `portfolios.metrics_json` and refreshed after quarantine-driven
portfolio recalculation, generation, completion, and reoptimization.

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
