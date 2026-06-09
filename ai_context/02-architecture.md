# Architecture & Source Layout

## Source Layout

```text
.
|-- app_ui.py                    # Tkinter app entry point and composition root
|-- ubs_agent.py                 # UBS set-generation/scoring agent (CLI)
|-- ubs_generate_sets.py         # UBS set mutation/generation helpers (standalone CLI)
|-- ubs_prepare_sets.py          # UBS source-set normalization/import helper (CLI)
|-- compile_mq5.py               # MetaEditor compile runner (CLI)
|-- compile_and_backtest.py      # Orchestrates compile then backtest (CLI)
|-- run_tests.py                 # Batch Strategy Tester runner (CLI)
|-- mt5_env.py                   # .env/env-var path resolution helpers
|-- telegram_notify.py           # Optional Telegram notification helper
|-- pyproject.toml               # Project metadata and dependencies
|-- tester_template.ini          # Base MT5 Strategy Tester config
|-- ui_settings.ini              # UI runtime settings, paths, theme
|-- experts_list.txt             # Optional explicit .ex5 list
|-- experts_root.txt             # Optional root folder for .ex5 discovery
|-- compile_root.txt             # Optional root folder for .mq5 discovery
|
|-- ui/                          # UI screen mixin modules (view/logic pairs per tab)
|   |-- __init__.py
|   |-- dashboard_view.py        # Dashboard widget/layout mixin
|   |-- dashboard_logic.py       # Dashboard compile/backtest action logic
|   |-- files_view.py            # Files/Logs widget/layout mixin
|   |-- files_logic.py           # Files/Logs refresh logic mixin
|   |-- multiterminal_view.py    # Multiterminal widget/layout mixin
|   |-- multiterminal_logic.py   # Multiterminal profiles/state/validation mixin
|   |-- portfolio_view.py        # Portfolio tab widget/layout mixin
|   |-- portfolio_logic.py       # Portfolio tab actions/workbook execution mixin
|   |-- ubs_portfolio_view.py    # UBS Portfolio tab widget/layout mixin
|   |-- ubs_portfolio_logic.py   # UBS Portfolio build/persist/export logic mixin
|   |-- settings_view.py         # Settings widget/layout mixin
|   |-- settings_logic.py        # Settings/template/path action logic
|   |-- ubs_agent_view.py        # UBS Agent widget/layout mixin
|   |-- ubs_agent_logic.py       # UBS Agent launch/continue logic
|   |-- ubs_params_view.py       # UBS Parameters widget/layout mixin
|   |-- ubs_params_logic.py      # UBS Parameters/global params logic
|   |-- ubs_results_view.py      # UBS Results/History/Compare layouts
|   |-- ubs_results_logic.py     # UBS Results/History/Compare logic
|   |-- ubs_robustness_view.py   # UBS Robustness OOS widget/layout mixin
|   |-- ubs_robustness_logic.py  # UBS Robustness OOS launch/table logic
|   |-- ubs_final_tick_view.py   # UBS Final Tick widget/layout mixin
|   |-- ubs_final_tick_logic.py  # UBS Final Tick launch/table logic
|   |-- ubs_seeds_view.py        # UBS Seeds widget/layout mixin
|   |-- ubs_seeds_logic.py       # UBS Seeds tab/evaluation logic
|   |-- ubs_universe_view.py     # UBS Universe widget/layout mixin
|   `-- ubs_universe_logic.py    # UBS Universe enable/weights logic
|
|-- ubs/                         # UBS agent support library
|   |-- __init__.py
|   |-- models.py                # Shared Seed/Variant dataclasses
|   |-- db.py                    # SQLite connection defaults for UBS memory
|   |-- memory.py                # SQLite persistence and row mapping
|   |-- score.py                 # Report scoring and pass/fail metrics
|   |-- seeds.py                 # Seed file discovery/naming/hash helpers
|   |-- set_utils.py             # .set parsing/writing/lot normalization
|   |-- universe.py              # Asset universe, aliases, disabled symbols
|   `-- params_catalog.py        # Parameter labels/descriptions/format helpers
|
|-- portfolio_manager/           # MT5 HTML parsing, Excel workbooks, UBS portfolio math
|   |-- __init__.py
|   |-- generator.py             # Public workbook generator functions
|   |-- mt5_report.py            # MT5 HTML parser and report dataclasses
|   |-- excel.py                 # ALL_STRATEGIES workbook builder
|   |-- dd_excel.py              # Drawdown/portfolio workbook builders
|   `-- ubs_portfolio.py         # UBS Portfolio lot-calibration math (pure, no Tkinter/sqlite)
|
|-- configs/                     # Generated tester .ini files
|-- logs/                        # Run and compile logs
|-- reports/                     # Copied MT5 report files and images
|-- outputs/                     # Portfolio Excel outputs + UBS runtime files
|   |-- ubs_memory.sqlite        # UBS agent SQLite memory
|   |-- ubs_global_params.json   # Global UBS EA parameter values (UI-editable)
|   |-- ubs_mutation_overrides.json  # User-defined mutability overrides
|   `-- ubs_agent/               # Generated UBS variants and accepted sets
|-- sets/                        # User .set files
|-- live_sets/                   # Runtime/live .set files
|-- ai_context/                  # AI agent context documents
|   |-- main.md                  # Entry point and index
|   |-- 01-overview.md           # Project overview and workflows
|   |-- 02-architecture.md       # This file
|   |-- 03-mt5-workflow.md       # Compile/backtest pipeline
|   |-- 04-portfolio-manager.md  # Portfolio parsing and generation
|   |-- 05-environment.md        # Runtime files and env vars
|   |-- 06-conventions.md        # Coding conventions
|   |-- 07-development.md        # Development workflow
|   `-- 08-ubs-parameters.md     # UBS EA parameter reference
|-- tools/
|   |-- build_installer.ps1      # PyInstaller packaging script
|   `-- installer_app.py         # Tkinter installer UI
|-- assets/                      # App icons and RoboForex asset universe
`-- scripts/                     # PowerShell cleanup helpers for MT5 data
```

## Composition Root

`app_ui.py` is the user-facing composition root. It owns the app shell
(window, sidebar, section registry, shared custom Tk widgets, theme/style,
common dialogs/process plumbing) and composes per-screen mixins. It should not
own tab-specific widget trees or tab-specific business behavior.

Each substantial screen/tab uses a view/logic pair:

- `app_ui_<screen>_view.py`: Tk widgets, layout, Treeview columns/tags, button
  wiring, and visual-only setup.
- `app_ui_<screen>_logic.py`: state transitions, validation, persistence,
  database queries, path/report calculations, and long-running actions.

Current pairs: Dashboard, Files/Logs, Multiterminal, Portfolio, UBS Portfolio,
Settings, UBS Agent, UBS Parameters, UBS Results/History/Compare, UBS Robustness,
UBS Final Tick, UBS Seeds, and UBS Universe. Shared cross-screen behavior may stay in `app_ui.py`
only when it is genuinely shell-level or generic infrastructure.

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
- Support multiterminal execution with `--multi-terminal`,
  `--terminals-config`, and `--max-workers`. In this mode, jobs are queued and
  distributed across enabled terminal profiles. Each profile can have its own
  `terminal64.exe`, data directory, Experts root, UBS `.ex5`, and portable
  flag. Report names remain compatible with the existing UBS stem-based
  convention.

### `ubs_agent.py`

Owns the UBS agent workflow:

- Load source `.set` seeds from `sets/ubs_ready` or a supplied source folder.
- Generate variants via `create_variant()`: reads seed, applies symbol/TF,
  injects global frozen values from `ubs_global_params.json` for any key listed
  in `ubs_mutation_overrides.json` `frozen_override`, then mutates the remaining
  mutable keys using weighted random sampling.
- Mutability is determined by `is_agent_mutable_key(key)` — checks
  `ubs_mutation_overrides.json` overrides first, then falls back to the
  hardcoded constant sets `FROZEN_KEYS`, `FROZEN_PREFIXES`,
  `ALLOWED_MUTATION_KEYS`, `ALLOWED_MUTATION_PREFIXES`.
- Explore related assets and timeframes (`M15`, `M30`, `H1`, `H4`, `D1`) using
  SQLite feedback from prior scored candidates.
- Run MT5 backtests through `run_tests.py` when `--execute-backtests` is set.
- Score reports via `ubs_score.py` and store candidates in
  `outputs/ubs_memory.sqlite`.
- Validate parsed report `Symbol`/`Period` against the intended target after
  applying `symbol_map`; invalid executions become `report_mismatch`.
- Evaluate out-of-sample robustness with `--evaluate-robustness` for accepted
  candidates from a run. It copies candidate `.set` files into
  `outputs/ubs_agent/<run>/robustness/...`, forwards robustness dates/criteria
  to `run_tests.py`, validates symbol/timeframe again, and stores results in
  `candidate_robustness` without overwriting the base candidate score.
- Evaluate Final Tick with `--evaluate-final-tick` for robustness-accepted
  candidates from a run. It runs OHLC (`Model=1`) and real-tick (`Model=4`)
  reports over the same explicit final date range, compares similarity and
  `History Quality`, and stores results in `candidate_final_tick`.
- Evaluate original UBS seeds with `--evaluate-seeds`. Seed scores are stored
  in `seed_scores`, feed asset/timeframe feedback at the same base strength as
  generated candidates when they have valid scored reports, and are surfaced in
  the UI. Seeds do not receive robustness/date bonus unless a separate seed
  bonus rule is explicitly added.
- Store manual seed symbol/timeframe corrections in `seed_overrides`. Overrides
  are applied before seed evaluation and before normal generation.
- Hard UBS seed rule: if a seed cannot infer both symbol and timeframe after
  applying overrides, it is recorded as `report_mismatch` and must not be
  backtested.
- Retry a single candidate with `--retry-candidate-id`.
- Retry all `report_mismatch` candidates in a run with `--retry-run-id` and
  `--retry-mismatch-run`.

UBS support code lives in the `ubs/` package:

- `ubs/models.py`: shared `Seed` and `Variant` dataclasses.
- `ubs/db.py`: shared SQLite connection helper for UBS memory. It applies a
  longer busy timeout, and `AgentMemory` enables WAL mode so UI reads and agent
  writes are less likely to collide.
- `ubs/memory.py`: SQLite schema, `AgentMemory`, seed/candidate persistence,
  and conversion from candidate rows to `Variant`.
- `ubs/weights.py`: shared weight formula for `AgentMemory` and `UBS Universo`.
  It applies accepted bonuses, rejected/cause penalties, no-trades penalty,
  robustness OOS adjustments, correlated-group averaging, and shrinkage toward
  zero for small samples. Seed rows with valid scored reports use the same base
  formula as candidates.
- `ubs/seeds.py`: seed `.set` discovery, manifest handling, seed report copy
  names, and file hashing used to reconcile interrupted seed evaluations.
- `ubs/universe.py`: RoboForex universe parsing, common alias canonicalisation,
  disabled symbol JSON persistence, and disabled-seed filtering.
- `ubs/normalization.py`: RoboForex-only score normalization helpers. It loads
  `assets/roboforex_normalization.json` and returns the net-profit factor,
  group, and basis used by scoring.
- `ubs/score.py`: `ScoreConfig`, `ScoreResult`, scoring formula. Raw
  `net_profit` is preserved, while `normalized_net_profit` drives net pass/fail
  and the profit score component.
- `ubs/set_utils.py`: shared `.set` parsing/writing/lot normalization.
- `ubs/params_catalog.py`: parameter labels, section names, format helpers.

**Key constants** (all in `ubs_agent.py`):

| Constant | Purpose |
|----------|---------|
| `FROZEN_KEYS` | Keys never mutated (EA_MagicNumber, Risk, StartLots, …) |
| `FROZEN_PREFIXES` | Key prefixes never mutated (Broker_GMT, NFP_, Grid, …) |
| `ALLOWED_MUTATION_KEYS` | Specific keys always mutable (SpreadFilter, MaxSpread, …) |
| `ALLOWED_MUTATION_PREFIXES` | Key prefixes always mutable (ST1_, Exit_, Vol) |
| `CORE_MUTATION_KEYS` | Per-strategy preferred keys weighted 4× in sampling |
| `MUTATION_OVERRIDES_FILE` | `outputs/ubs_mutation_overrides.json` |
| `GLOBAL_PARAMS_FILE` | `outputs/ubs_global_params.json` |

**Important candidate states:**

- `generated`: `.set` exists but no backtest result is stored yet.
- `accepted`: report passed configured filters and matches target.
- `rejected`: report matches target but failed score filters.
- `no_report`: MT5 did not produce a report for the expected name.
- `parse_error`: report exists but could not be parsed.
- `report_mismatch`: report parsed, but actual symbol/timeframe does not match
  the candidate target. For `seed_scores`, this also covers UBS seeds that lack
  a resolvable symbol/timeframe before execution.

**Important robustness states (`candidate_robustness`):**

- `accepted`: OOS report passed robustness thresholds; adds positive bonus to
  asset/timeframe/mutation feedback.
- `rejected`: OOS report matched target but failed robustness thresholds; adds
  negative bonus to asset/timeframe/mutation feedback.
- `no_report`, `parse_error`, `report_mismatch`, `no_trades`: stored for
  diagnosis, but neutral for weights.

### `ubs_score.py`

Scores MT5 reports for UBS:

- Net profit, profit factor, trade count, drawdown %, recovery factor.
- Monthly stability metrics are score inputs, not a hard filter by default.
- Default pass config: `min_net_profit=100`, `min_profit_factor=1.20`,
  `min_trades=50`, `max_drawdown_pct=25`, `min_recovery_factor=1.0`.
- `ScoreResult.reasons` is a tuple of failing metric names. Empty = accepted.
- All results serialised via `ScoreResult.to_json()` into `metrics_json` column.

### `ubs_generate_sets.py`

Standalone CLI for simple set mutation without agent memory. Has its own
`is_mutable_key()` with different constants than `ubs_agent.py`. **Do not use
`ubs_generate_sets.is_mutable_key()` to reason about agent behavior** — use
`ubs_agent.is_agent_mutable_key()` instead.

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

## Runtime Files

| File / Dir | Purpose |
|-----------|---------|
| `configs/` | Generated Strategy Tester INI files (one per backtest job) |
| `logs/` | Compile and backtest logs plus `last_*` pointers |
| `reports/` | Copied MT5 HTML reports, `.set` files, chart images |
| `outputs/` | Generated Excel workbooks |
| `outputs/ubs_agent/` | Generated UBS variants and copied accepted sets |
| `outputs/ubs_memory.sqlite` | UBS agent SQLite: candidates, runs, seed_scores, seed_overrides, candidate_robustness, portfolios, portfolio_members |
| `outputs/ubs_global_params.json` | Global EA parameter values edited in the UBS Parámetros tab |
| `outputs/ubs_mutation_overrides.json` | User mutability overrides: `frozen_override` and `mutable_override` |

`ubs_global_params.json` and `ubs_mutation_overrides.json` are runtime files
produced by the UI. They are gitignored but are critical for agent behavior —
back them up when changing machine.

## External Dependencies

Runtime third-party packages are intentionally minimal:

- `lxml`: MT5 HTML report parsing.
- `openpyxl`: Excel workbook generation and image embedding.
- `Pillow`: optional; improves rounded UI widget rendering when available.

Packaging additionally needs `PyInstaller`. Tkinter, SQLite, urllib, and winreg
are from Python/Windows standard libraries and should not be treated as pip
dependencies.
