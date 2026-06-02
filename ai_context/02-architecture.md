# Architecture & Source Layout

## Source Layout

```text
.
|-- app_ui.py                    # Tkinter desktop app and UI orchestration
|-- run_tests.py                 # Batch Strategy Tester runner
|-- ubs_agent.py                 # UBS set-generation/scoring agent
|-- ubs_score.py                 # UBS report scoring and pass/fail metrics
|-- ubs_generate_sets.py         # UBS set mutation/generation helpers (standalone CLI)
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
|-- outputs/                     # Portfolio Excel outputs + UBS runtime files
|   |-- ubs_memory.sqlite        # UBS agent SQLite memory
|   |-- ubs_global_params.json   # Global UBS EA parameter values (UI-editable)
|   |-- ubs_mutation_overrides.json  # User-defined mutability overrides
|   `-- ubs_agent/               # Generated UBS variants and accepted sets
|-- sets/                        # User .set files
|-- live_sets/                   # Runtime/live .set files
|-- portfolio_manager/
|   |-- generator.py             # Public workbook generator functions
|   |-- mt5_report.py            # MT5 HTML parser and report dataclasses
|   |-- excel.py                 # ALL_STRATEGIES workbook builder
|   `-- dd_excel.py              # Drawdown/portfolio workbook builders
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
- Evaluate original UBS seeds with `--evaluate-seeds`. Seed scores are stored
  in `seed_scores`, feed asset/timeframe feedback, and are surfaced in the UI.
- Store manual seed symbol/timeframe corrections in `seed_overrides`. Overrides
  are applied before seed evaluation and before normal generation.
- Hard UBS seed rule: if a seed cannot infer both symbol and timeframe after
  applying overrides, it is recorded as `report_mismatch` and must not be
  backtested.
- Retry a single candidate with `--retry-candidate-id`.
- Retry all `report_mismatch` candidates in a run with `--retry-run-id` and
  `--retry-mismatch-run`.

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
| `outputs/ubs_memory.sqlite` | UBS agent SQLite: candidates, runs, seed_scores, seed_overrides |
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
