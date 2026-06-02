# Architecture & Source Layout

## Source Layout

```text
.
|-- app_ui.py                    # Tkinter desktop app and UI orchestration
|-- run_tests.py                 # Batch Strategy Tester runner
|-- ubs_agent.py                 # UBS set-generation/scoring agent
|-- ubs_score.py                 # UBS report scoring and pass/fail metrics
|-- ubs_generate_sets.py         # UBS set mutation/generation helpers
|-- ubs_prepare_sets.py          # UBS source-set normalization/import helper
|-- ubs_set_utils.py             # Shared .set parsing/writing/lot normalization
|-- compile_mq5.py               # MetaEditor compile runner
|-- compile_and_backtest.py      # Orchestrates compile then backtest
|-- mt5_env.py                   # .env/env-var path resolution helpers
|-- telegram_notify.py           # Optional Telegram notification helper
|-- tester_template.ini          # Base MT5 Strategy Tester config
|-- ui_settings.ini              # UI runtime settings, paths, theme
|-- experts_list.txt             # Optional explicit .ex5 list
|-- experts_root.txt             # Optional root folder for .ex5 discovery
|-- compile_root.txt             # Optional root folder for .mq5 discovery
|-- configs/                     # Generated tester .ini files
|-- logs/                        # Run and compile logs
|-- reports/                     # Copied MT5 report files and images
|-- outputs/                     # Portfolio Excel outputs
|-- sets/                        # User .set files
|-- live_sets/                   # Runtime/live .set files
|-- portfolio_manager/
|   |-- generator.py             # Public workbook generator functions
|   |-- mt5_report.py            # MT5 HTML parser and report dataclasses
|   |-- excel.py                 # ALL_STRATEGIES workbook builder
|   `-- dd_excel.py              # Drawdown/portfolio workbook builders
|-- tools/
|   |-- build_installer.ps1      # PyInstaller packaging script
|   `-- installer_app.py         # Tkinter installer UI
|-- assets/                      # App icons and RoboForex asset universe
`-- scripts/                     # PowerShell cleanup helpers for MT5 data
```

## Composition Root

`app_ui.py` is the user-facing composition root. It imports helper functions
from the CLI scripts instead of duplicating path discovery and inference
logic. Portfolio generators are imported from `portfolio_manager.generator`.

The CLI scripts can still run independently and should remain usable without
the UI.

## Main Module Responsibilities

### `run_tests.py`

Owns the Strategy Tester workflow:

- Resolve MT5 terminal path.
- Resolve terminal data directory.
- Load experts from `experts_list.txt`, `experts_root.txt`, CLI `--experts-dir`,
  or a specific `--expert`.
- Load `.set` files and infer tester `Symbol`/`Period` when requested.
- Generate per-run `.ini` files.
- Launch `terminal64.exe /config:<ini>`.
- Detect and copy report files from MT5 output locations into `reports/`.
- Write logs to timestamped `logs/run_*.log` and `logs/last_run.log`.
- Delete old report artifacts for the same report name immediately before
  real backtests, so stale HTML/images are not mistaken for fresh output.
- When `--infer-tester-from-set` is used, infer exact symbol tokens before
  loose aliases. This matters for UBS names like `XAGUSD__...__XAUUSD_MIX...`.

### `ubs_agent.py`

Owns the UBS agent workflow:

- Load source `.set` seeds from `sets/ubs_ready` or a supplied source folder.
- Generate variants by replacing existing keys only; missing keys are not
  added. Lot sizing is normalized through `ubs_set_utils.force_fixed_lot_text`.
- Explore related assets and timeframes (`M15`, `M30`, `H1`, `H4`, `D1`) using
  SQLite feedback from prior scored candidates.
- Run MT5 backtests through `run_tests.py` when `--execute-backtests` is set.
- Score reports via `ubs_score.py` and store candidates in
  `outputs/ubs_memory.sqlite`.
- Validate parsed report `Symbol`/`Period` against the intended target after
  applying `symbol_map`; invalid executions become `report_mismatch`.
- Retry a single candidate with `--retry-candidate-id`, used by the UI
  `Reprobar mismatch` button.
- Retry all `report_mismatch` candidates in a run with `--retry-run-id` and
  `--retry-mismatch-run`, used by the UI `Reprobar run` button.

Important candidate states:

- `generated`: `.set` exists but no backtest result is stored yet.
- `accepted`: report passed configured filters and matches target.
- `rejected`: report matches target but failed score filters.
- `no_report`: MT5 did not produce a report for the expected name.
- `parse_error`: report exists but could not be parsed.
- `report_mismatch`: report parsed, but actual symbol/timeframe does not match
  the candidate target.

### `ubs_score.py`

Scores MT5 reports for UBS:

- Net profit, profit factor, trade count, drawdown %, recovery factor.
- Monthly stability metrics are score inputs, not a hard filter by default.
- Default pass config currently uses `min_net_profit=100`,
  `min_profit_factor=1.20`, `min_trades=50`, `max_drawdown_pct=25`,
  `min_recovery_factor=1.0`.

### `compile_mq5.py`

Owns MetaEditor compilation:

- Resolve `MetaEditor64.exe`, directly or from the MT5 terminal path.
- Load `.mq5` sources from `compile_root.txt` or CLI `--source-dir`.
- Compile a single file or all root-level `.mq5` files.
- Verify that expected `.ex5` outputs are created or updated.
- Write compile logs under `logs/`.

### `compile_and_backtest.py`

Small orchestrator that launches `compile_mq5.py`, verifies `.ex5` availability,
then launches `run_tests.py` against the same source directory.

### `portfolio_manager`

Pure-ish report parsing and workbook generation. It should not depend on
Tkinter. The UI calls it from a background thread.

### `tools`

Installer-only code. Keep it separate from app runtime code unless a change is
explicitly about packaging.

## Runtime Directories

The scripts create and use these local folders:

- `configs/`: generated Strategy Tester INI files.
- `logs/`: compile/backtest logs plus `last_*` pointers.
- `reports/`: copied MT5 HTML reports, `.set` files, and chart images.
- `outputs/`: generated Excel workbooks.
- `outputs/ubs_agent/`: generated UBS variants and copied accepted sets.
- `outputs/ubs_memory.sqlite`: UBS agent memory and historical candidates.

These are operational outputs, not core source files.
