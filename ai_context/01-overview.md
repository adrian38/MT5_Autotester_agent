# Project Overview

## What This Project Does

`MT5 Autotester` automates repetitive MetaTrader 5 testing tasks for Expert
Advisors:

- Locate MT5 and MetaEditor executables.
- Compile `.mq5` source files to `.ex5`.
- Generate Strategy Tester `.ini` files from a reusable template.
- Run MT5 backtests one by one or distribute queues across configured
  multiterminal profiles.
- Collect generated `.htm/.html` reports into the project `reports/` folder.
- Parse reports and export portfolio analysis workbooks under `outputs/`.
- Generate, backtest, score, and retain UBS `.set` variants through an agent
  with SQLite memory.
- Optionally send Telegram notifications after backtests.
- Package the UI and helper CLIs into a Windows installer/portable ZIP.

The project is not a trading engine and does not execute live trades. It wraps
MetaTrader tools and processes their local outputs.

## Primary User Workflows

### 1. Desktop Workflow

Users normally run:

```powershell
python .\app_ui.py
```

The UI exposes:

- Configuration of MT5, MetaEditor, source folders, templates, set files, and
  broker symbol mappings.
- Compile-only actions.
- Compile-and-backtest actions.
- Backtest execution with live logs.
- Portfolio Manager Excel generators.
- UBS agent tabs for configuration, results, robustness OOS, SQLite history,
  asset/timeframe universe weights, seed evaluation/overrides, and
  accepted-vs-seed comparison.
- Multiterminal profile configuration for multiple MT5 installations/data
  folders.
- Runtime file/log browsing.
- Light/dark theme selection.

### 2. CLI Workflow

The CLI scripts are the automation core:

- `compile_mq5.py`: compile `.mq5` files with MetaEditor.
- `run_tests.py`: generate Strategy Tester configs and launch MT5.
- `compile_and_backtest.py`: orchestrate compile first, then backtest.
- `ubs_agent.py`: generate UBS variants, run backtests, score reports, evaluate
  robustness OOS for accepted candidates, and update `outputs/ubs_memory.sqlite`.

Batch files call those scripts for double-click usage:

- `compile_mq5.bat`
- `run_backtests.bat`
- `compile_and_backtest.bat`
- `dry_run.bat`
- `run_ui.bat`

### 3. Portfolio Workflow

The Portfolio Manager reads MT5 report files from `reports/` and creates Excel
workbooks in `outputs/`:

- `ALL_STRATEGIES.xlsx`
- `ALL_STRATEGIES_DD.xlsx`
- `PORTFOLIO_DD.xlsx`
- `PORTFOLIO_VALLEY_DD.xlsx`
- `PORTFOLIO_TOP5_VALLEYS.xlsx`
- `DD_THRESHOLD.xlsx`

See [04-portfolio-manager.md](04-portfolio-manager.md).

### 4. UBS Agent Workflow

The UBS agent starts from known-good UBS `.set` files, generates variants, runs
Strategy Tester backtests, scores the resulting reports, and saves candidate
history in SQLite.

Main UI areas:

- `UBS Agente UBS`: generation/pass configuration and launch/continue actions.
- `UBS Seeds`: original seed scoring state, manual symbol/timeframe overrides,
  seed evaluation actions, reset of seed scores/reports, and seed deletion.
- `UBS Resultados`: latest run candidates, including `report_mismatch` rows,
  single-candidate retry, and run-level mismatch retry.
- `UBS Robustez`: out-of-sample robustness results for candidates accepted by
  the visible UBS run.
- `UBS Historico`: SQLite run/candidate history.
- `UBS Universo`: asset/timeframe weights used by the agent. After seed
  evaluation reset, weights stay blocked until the user presses `Calcular pesos`.
- `UBS Comparar`: accepted set vs seed differences and HTML comparison report.

Weight semantics:

- Original seeds with valid scored reports (`accepted`, `rejected`, or
  `no_trades`) contribute to asset/timeframe weights at the same base strength
  as generated candidates.
- Generated candidates can receive robustness OOS adjustments through
  `candidate_robustness` (`+70/-70` defaults plus OOS cause penalties).
- Seeds do not receive robustness/date bonus unless a future explicit seed bonus
  rule is added.
- `report_mismatch`, `no_report`, `parse_error`, `pending`, and disabled-symbol
  rows do not contribute to weights.

## High-Level Data Flow

```text
.mq5 source files
    -> compile_mq5.py
    -> MetaEditor64.exe
    -> .ex5 Expert Advisors
    -> run_tests.py
    -> generated configs/*.ini
    -> terminal64.exe /config:<ini>
    -> MT5 Strategy Tester report files
    -> reports/*.htm + images + .set files
    -> portfolio_manager parser
    -> outputs/*.xlsx
```

UBS agent flow:

```text
sets/ubs_ready/*.set
    -> optional seed evaluation / seed_overrides
    -> seed_scores rows at full base weight when reports are valid
    -> ubs_agent.py
    -> generated outputs/ubs_agent/<run>/gen_*/**/*.set
    -> run_tests.py + terminal64.exe
    -> reports/*.htm
    -> ubs_score.py
    -> outputs/ubs_memory.sqlite
    -> accepted_gen_* or report_mismatch/rejected state
    -> optional robustness OOS for accepted candidates
    -> candidate_robustness rows + robustness bonus/penalty in weights
    -> shared ubs.weights formula for agent feedback and UBS Universo
```

## Tech Stack

- Python 3, standard library-first.
- Tkinter / ttk for the desktop UI.
- Pillow is optional and used by the UI for anti-aliased rounded controls and
  icon handling.
- `lxml.html` parses MT5 HTML reports.
- `openpyxl` writes Excel workbooks and embeds report images.
- PyInstaller builds Windows `.exe` artifacts and the installer payload.
- PowerShell scripts clean MT5 data and build installer packages.
