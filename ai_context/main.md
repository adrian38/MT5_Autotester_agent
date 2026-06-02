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
- UBS parameter mutability is defined by `is_agent_mutable_key()` in
  `ubs_agent.py` — NOT by the Y/N flag in `.set` files (that is the MT5
  optimizer flag, a different thing) and NOT by `is_mutable_key()` in
  `ubs_generate_sets.py` (which has different constants). Always use
  `is_agent_mutable_key()` when reasoning about what the agent will mutate.

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
| [08-ubs-parameters.md](08-ubs-parameters.md) | UBS EA parameter reference: all keys, sections, mutability, ranges. |

## Common Entry Points

- Desktop UI: `python app_ui.py`
- Compile EA(s): `python compile_mq5.py`
- Run backtests: `python run_tests.py`
- Compile then backtest: `python compile_and_backtest.py`
- UBS agent: `python ubs_agent.py`
- Build installer: `powershell -ExecutionPolicy Bypass -File tools/build_installer.ps1`
- Portfolio workbook generation: functions in `portfolio_manager/generator.py`

## Recent Important Changes

### UBS Parámetros tab and global parameter system

A new UI tab "UBS Parámetros" provides a global view of all UBS EA parameters:

- Values are stored in `outputs/ubs_global_params.json` (not in individual seed
  files). On first launch the tab bootstraps the file from the first available
  seed, then uses the JSON file as the source of truth from that point on.
- Parameters are displayed with their mutability status per the agent's actual
  rules (`is_agent_mutable_key()`). Green = agent may mutate; white = fixed.
- Users can toggle any parameter between frozen/mutable via "Toggle
  inamovible/mutable". Changes are written immediately to
  `outputs/ubs_mutation_overrides.json` and take effect in the next generation
  run without restarting.
- When a parameter is frozen (`frozen_override`), its value from
  `ubs_global_params.json` is injected into every generated variant in
  `create_variant()`, overriding whatever value the individual seed file holds.
- `is_agent_mutable_key()` is the single source of truth for mutability: it
  checks `ubs_mutation_overrides.json` first, then falls back to the hardcoded
  `FROZEN_KEYS`, `FROZEN_PREFIXES`, `ALLOWED_MUTATION_KEYS`,
  `ALLOWED_MUTATION_PREFIXES` constants in `ubs_agent.py`.

### UBS Seeds tab improvements

- MOTIVO column: shows each failed scoring criterion with its actual value
  (e.g. `net profit: -830 | PF: 0.69 | DD: 96.6%`), parsed from `metrics_json`.
- Criteria bar: displays active scoring thresholds above the table, bound live
  to the same `tk.StringVar` instances as the configuration panel.
- Double-click a row to open the HTML report in the system viewer.
- "Eliminar seed" and "Eliminar rechazadas" buttons delete files and DB rows,
  then refresh both the Seeds table and the Universe weights tab.
- "Resetear evaluación" clears active seed scores/reports without deleting
  source `.set` files, then locks Universe weights.
- The Universe tab has "Calcular pesos"; weights remain hidden/blocked after a
  reset until active seeds are evaluated or quarantined and the user applies
  weights explicitly.
- Seed evaluation skip logic now detects symbol/TF override changes: saving a
  `seed_override` that changes symbol or TF triggers re-evaluation.
- Evaluation dialog shows the actual expected backtest count (pre-computed from
  DB state) alongside the total seed count.

### Multiterminal support

- `run_tests.py` accepts `--multi-terminal`, `--terminals-config`, and
  `--max-workers`.
- `ui_settings.ini` stores `[Multiterminal]` and `[Terminal.N]` profiles.
- Compilation remains sequential; multiterminal applies to backtest queues.

### Portfolio parser English support

The portfolio parser was fixed to recognise English MT5 report labels (`Symbol`,
`Period`, `Results`, `Orders`, `Deals`, `Balance Drawdown Maximal`, …). Before
this fix, `ALL_STRATEGIES.xlsx` was generated but contained empty/zero metrics
for English-language reports. Relevant files:
[`portfolio_manager/mt5_report.py`](../portfolio_manager/mt5_report.py),
[`portfolio_manager/excel.py`](../portfolio_manager/excel.py).

## Python Dependencies

The runtime is standard-library-first. Third-party runtime dependencies are
`lxml` for report parsing and `openpyxl` for Excel generation. `Pillow` is
optional for anti-aliased UI widgets. `PyInstaller` is only needed for packaging.
