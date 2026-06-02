# AI Context - MT5 Autotester

This folder is the entry point for AI agents working on this repository. It
keeps project knowledge in focused files so an agent can load only what it
needs for the current task.

Language: English. The application UI and many log messages are Spanish, but
this context is optimized for AI/code tools and cross-project consistency.

## Project Summary

`MT5_Autotester` is a Windows-focused Python desktop/tooling project for
MetaTrader 5. It automates three related workflows:

1. Compile `.mq5` Expert Advisors into `.ex5` with `MetaEditor64.exe`.
2. Run MT5 Strategy Tester backtests in batch by generating tester `.ini`
   files and launching `terminal64.exe /config:<ini>`.
3. Parse MT5 HTML reports and generate Excel portfolio workbooks with
   strategy metrics and drawdown analysis.
4. Run a UBS-specific set-generation agent that mutates known-good `.set`
   files, backtests variants, scores reports, and stores memory in SQLite.

The main user-facing entry point is the Tkinter desktop app in
[`app_ui.py`](../app_ui.py). The same functionality is also available through
CLI scripts (`compile_mq5.py`, `run_tests.py`, `compile_and_backtest.py`) and
batch wrappers.

## Must-Know Rules

- This is a Windows/MT5 automation project. Do not assume Linux paths or a
  headless MT5 runtime.
- Keep paths robust for both source execution and PyInstaller frozen
  execution. Most scripts redefine `BASE_DIR` from `sys.executable` when
  `sys.frozen` is true.
- `tester_template.ini` is the base Strategy Tester configuration. Scripts
  generate per-run `.ini` files under `configs/`; do not hand-edit generated
  configs as source of truth.
- `reports/`, `logs/`, `configs/`, `outputs/`, `build_installer/`, and
  `dist_installer/` are generated/runtime areas. Avoid committing generated
  churn unless the user explicitly wants artifacts.
- MT5 can silently ignore `/config` if the terminal is already open. The code
  has running-process checks; preserve them unless intentionally changing MT5
  launch behavior.
- Portfolio parsing must support both English and Spanish MT5 HTML reports.
  Recent MT5 reports in this workspace use English labels (`Results`,
  `Orders`, `Deals`, `Symbol`, `Time`, etc.).
- UBS agent results must validate the actual MT5 report `Symbol`/`Period`
  against the intended candidate target. Do not trust set/report filenames
  alone; stale reports and broker symbol aliases can otherwise poison memory.
- `report_mismatch` is an intentional UBS candidate state. It means the
  report was parsed, but MT5 executed a different symbol/timeframe than the
  candidate target after applying the configured `symbol_map`.

## Topic Index

| File | When to read it |
|------|-----------------|
| [01-overview.md](01-overview.md) | What the project does, user workflows, high-level data flow. |
| [02-architecture.md](02-architecture.md) | Source layout, main modules, ownership boundaries. |
| [03-mt5-workflow.md](03-mt5-workflow.md) | Compile/backtest pipeline, generated INI files, report copying. |
| [04-portfolio-manager.md](04-portfolio-manager.md) | MT5 HTML parsing and Excel workbook generation. |
| [05-environment.md](05-environment.md) | Runtime files, environment variables, path resolution. |
| [06-conventions.md](06-conventions.md) | Coding style, UI conventions, generated files, packaging notes. |
| [07-development.md](07-development.md) | Common commands, verification steps, debugging guidance. |

## Common Entry Points

- Desktop UI: `python app_ui.py`
- Compile EA(s): `python compile_mq5.py`
- Run backtests: `python run_tests.py`
- Compile then backtest: `python compile_and_backtest.py`
- UBS agent: `python ubs_agent.py`
- Build installer: `powershell -ExecutionPolicy Bypass -File tools/build_installer.ps1`
- Portfolio workbook generation: functions in `portfolio_manager/generator.py`

## Recent Important Change

The UBS agent now has seed-level evaluation and stricter UBS mismatch
protection:

- `run_tests.py` prioritizes exact symbol tokens when inferring tester fields,
  so names like `XAGUSD__...__XAUUSD_MIX...` infer `XAGUSD`, not `XAUUSD`.
- `ubs_agent.py` validates parsed report symbol/timeframe against the
  candidate target after applying `symbol_map`.
- UBS seeds can be evaluated directly with `ubs_agent.py --evaluate-seeds`.
  Results are stored in SQLite `seed_scores` and feed the Universe tab weights
  only when the seed is `accepted` or `rejected`.
- UBS seeds that cannot infer both symbol and timeframe are marked
  `report_mismatch` before MT5 is launched. This is a hard rule: do not run a
  seed backtest when UBS cannot determine the intended symbol/timeframe.
- The UI has a dedicated `UBS Seeds` tab. It lists seed states, lets the user
  open a seed, and stores manual symbol/timeframe corrections in
  `seed_overrides`. These overrides are applied before seed evaluation and
  before normal UBS generation.
- Mismatched rows are stored as `report_mismatch`, excluded from agent
  feedback/universe weights, and can be retried from the UI with
  `Reprobar mismatch` for one row or `Reprobar run` for every mismatch in the
  visible run.
- `run_tests.py` deletes old report artifacts for the same report name before
  real backtests, reducing stale-report reads.
- UBS generation and run-level mismatch retry tolerate partial `run_tests.py`
  failures: they still evaluate reports that were produced, while failed
  candidates remain `no_report`. If nothing puntuable is produced, the agent
  still stops with an error.
- Broker symbols with a leading dot, such as `.US30Cash`, must stay intact in
  the report parser. Only trailing broker suffixes such as `EURUSD.a` should be
  stripped.

Multiterminal support is available for batch backtests and UBS backtest
execution:

- `run_tests.py` accepts `--multi-terminal`, `--terminals-config`, and
  `--max-workers`.
- `ui_settings.ini` stores `[Multiterminal]` and `[Terminal.N]` profiles.
- The UI has `MT5 Multiterminales` for manual terminal profiles and compact
  multiterminal controls near execution buttons.
- Compilation remains sequential; multiterminal applies to backtest queues.

Earlier important change: the portfolio parser was fixed to support English
MT5 reports. Before that, `ALL_STRATEGIES.xlsx` could be generated
successfully but contain empty/zero metrics because the parser only looked for
Spanish section/header names. Relevant files:

- [`portfolio_manager/mt5_report.py`](../portfolio_manager/mt5_report.py)
- [`portfolio_manager/excel.py`](../portfolio_manager/excel.py)
