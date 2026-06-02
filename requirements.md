# Requirements & Technical Debt — MT5 Autotester Agent

This document captures the **functional requirements** the application must satisfy
and the **technical debt** observed in the current codebase. Update it whenever a
requirement changes or a debt item is opened/closed.

---

## 1. Functional requirements

### 1.1 Compilation

- **FR-1.1.1** The compiler MUST invoke `MetaEditor64.exe /compile:<source>` for
  each `.mq5` file and verify the resulting `.ex5` exists and has an mtime later
  than the start of the run.
- **FR-1.1.2** MetaEditor path resolution MUST follow this priority: CLI
  `--metaeditor-path` → env `MT5_METAEDITOR_PATH` / `METAEDITOR_PATH` →
  same directory as resolved `terminal64.exe` → known install defaults →
  `shutil.which("MetaEditor64.exe")`.
- **FR-1.1.3** Source discovery for batch compilation MUST follow: CLI
  `--source-file` (single) → `--source-dir` → first active line of
  `compile_root.txt` → current directory.
- **FR-1.1.4** Compilation MUST always be sequential, never concurrent, regardless
  of multiterminal settings.
- **FR-1.1.5** Per-file compile logs MUST be written under `logs/` as
  `<stem>_compile.log`. A summary `last_compile.log` MUST also be written.

### 1.2 Backtesting

- **FR-1.2.1** The runner MUST generate one `.ini` file per backtest job under
  `configs/`, derived from `tester_template.ini`, and launch
  `terminal64.exe /config:<ini>`.
- **FR-1.2.2** MT5 terminal path resolution MUST follow: CLI `--mt5-path` → env
  `MT5_TERMINAL_PATH` / `MT5_PATH` → known install defaults →
  `shutil.which("terminal64.exe")`.
- **FR-1.2.3** Expert path resolution MUST follow: CLI `--expert` → `.ex5` files
  in `--experts-dir` → first active line of `experts_root.txt` →
  `experts_list.txt` entries.
- **FR-1.2.4** Before each real backtest the runner MUST delete any stale report
  artifacts (HTML + images + `.set`) that share the same report name, to prevent
  stale-report reads.
- **FR-1.2.5** When MT5 is already running, the launcher MUST detect the process
  and handle it (wait, warn, or skip) rather than silently failing because MT5
  ignores `/config` when a terminal is open.
- **FR-1.2.6** Symbol/timeframe inference from `.set` filenames MUST prioritize
  the first exact token match. A name like `XAGUSD__MIX__XAUUSD.set` MUST infer
  `XAGUSD`, not `XAUUSD`.
- **FR-1.2.7** Broker symbols with a leading dot (e.g. `.US30Cash`) MUST be kept
  intact. Only trailing broker suffixes (e.g. `EURUSD.a`) MAY be stripped during
  normalization.

### 1.3 Multiterminal execution

- **FR-1.3.1** When `--multi-terminal` is passed, backtest jobs MUST be
  distributed across all enabled terminal profiles defined in `ui_settings.ini`
  `[Multiterminal]` / `[Terminal.N]` sections.
- **FR-1.3.2** The concurrency limit MUST be `min(max_workers, enabled_terminal_count,
  job_count)`. The runner MUST never spawn more workers than there are jobs.
- **FR-1.3.3** Each terminal profile MAY override: `enabled`, `name`, `mt5_path`,
  `data_dir`, `experts_root`, `ubs_ex5_file`, `portable`.
- **FR-1.3.4** Compilation MUST remain sequential even in multiterminal mode.

### 1.4 MT5 report parsing

- **FR-1.4.1** The parser MUST support both English and Spanish MT5 HTML reports.
  English labels (`Symbol`, `Period`, `Results`, `Orders`, `Deals`, `Balance
  Drawdown Maximal`, `Balance Drawdown Relative`, …) and Spanish equivalents
  (`Símbolo`, `Período`, `Resultados`, `Reducción máxima del balance`, …) MUST
  both be recognised.
- **FR-1.4.2** Report HTML files MUST be read with encoding auto-detection.
  MT5 may produce UTF-16 LE; the parser MUST NOT assume UTF-8.
- **FR-1.4.3** The parser MUST extract: `symbol`, `timeframe`, `initial_deposit`,
  all key/value metrics, monthly P/L table, trade list (with profit, type,
  volume, price, commission, swap), and embedded chart images.
- **FR-1.4.4** Drawdown extraction MUST support both `(amount (pct%))` format
  and `pct%` standalone format, for both maximal and relative drawdown fields.

### 1.5 Portfolio workbook generation

- **FR-1.5.1** `generate_workbook()` MUST produce `ALL_STRATEGIES.xlsx` with an
  `INDEX` sheet (one row per strategy, all KPI columns) and one detail sheet per
  strategy (metrics grid, monthly table, chart images, trade list).
- **FR-1.5.2** Drawdown workbooks (`ALL_STRATEGIES_DD.xlsx`, `PORTFOLIO_DD.xlsx`,
  `PORTFOLIO_VALLEY_DD.xlsx`, `PORTFOLIO_TOP5_VALLEYS.xlsx`,
  `DD_THRESHOLD.xlsx`) MUST be generated from the same parsed report set.
- **FR-1.5.3** Portfolio drawdown calculations MUST assume an initial balance
  of `1000.0` (constant `PORTFOLIO_ACCOUNT_BALANCE`) and merge trades
  chronologically across all strategies.
- **FR-1.5.4** `DD_THRESHOLD.xlsx` MUST produce two sheets: `CUMPLEN` (strategies
  passing the configured DD threshold) and `TODAS` (all strategies).

### 1.6 UBS agent — generation

- **FR-1.6.1** Each generation round MUST load `.set` seeds from the configured
  source directory (default `sets/ubs_ready/`), apply any stored `seed_overrides`,
  then mutate them into variant `.set` files.
- **FR-1.6.2** Variant mutation MUST only replace keys that already exist in the
  seed; it MUST NOT add new keys.
- **FR-1.6.3** Lot sizing in every generated variant MUST be normalised via
  `force_fixed_lot_text` before use.
- **FR-1.6.4** Timeframe exploration MUST draw on SQLite feedback
  (`asset_feedback`, `timeframe_feedback`) and limit exploration to
  M15 / M30 / H1 / H4 / D1 unless the seed itself specifies otherwise.
- **FR-1.6.5** When `--execute-backtests` is set, the agent MUST invoke
  `run_tests.py` (or the multiterminal equivalent) for the generated variants.
- **FR-1.6.6** After backtests, every produced report MUST be scored and
  validated. A non-zero exit from `run_tests.py` MUST NOT discard reports that
  were produced; the agent MUST score whatever is available. If zero scorable
  reports are produced the agent MUST exit with an error.
- **FR-1.6.7** Continuation MUST be supported via `--continue-last-run`. The
  agent MUST pick up the pending generation count, variants-per-seed, and
  max-seeds from the last stored run.

### 1.7 UBS agent — scoring

- **FR-1.7.1** Score computation MUST use `ubs_score.ScoreResult` with these
  configurable thresholds (CLI flags, overridable in the UI):

  | Metric | Default | Direction |
  |--------|---------|-----------|
  | Net profit | 100.0 | ≥ |
  | Profit factor | 1.20 | ≥ |
  | Trades | 50 | ≥ |
  | Max drawdown % | 25.0 | ≤ |
  | Recovery factor | 1.0 | ≥ |
  | Positive month ratio | 0.0 | ≥ |

- **FR-1.7.2** A candidate or seed is `accepted` if and only if ALL thresholds
  are met (empty `reasons` tuple). Any threshold failure produces `rejected` with
  the failing metric names in `reasons`.
- **FR-1.7.3** The score formula MUST be:
  ```
  score = profit_component + pf_component + recovery_component
        + trades_component + monthly_component + sqn_component
        - dd_penalty - concentration_penalty
  ```
  where each component is capped/floored as defined in `ubs_score._score_formula`.
- **FR-1.7.4** After scoring, the agent MUST validate the parsed report's
  `symbol`/`timeframe` against the candidate target (after applying `symbol_map`).
  A mismatch MUST set status `report_mismatch` regardless of score.

### 1.8 UBS agent — candidate lifecycle

- **FR-1.8.1** Candidate statuses in SQLite `candidates` table MUST be one of:
  `generated` → `accepted` | `rejected` | `no_report` | `parse_error` |
  `report_mismatch`.
- **FR-1.8.2** Only `accepted` and `rejected` candidates MUST contribute to
  Universe asset/timeframe weights.
- **FR-1.8.3** `report_mismatch` and `no_report` rows MUST be retryable:
  - Single candidate: UI "Reprobar mismatch" → copies `.set` to
    `outputs/ubs_agent/<run>/retry_mismatch/`, re-evaluates, updates the
    original DB row.
  - Run-level: "Reprobar run" → copies all mismatches from the run, evaluates
    all produced reports. Partial failures leave failed candidates as `no_report`.

### 1.9 UBS agent — seed evaluation

- **FR-1.9.1** `--evaluate-seeds` MUST run a dedicated backtest for each seed
  that is new, modified (different mtime/size), has a changed symbol/TF (via
  override), or has a non-terminal status (not `accepted`/`rejected`).
  Seeds already evaluated without changes MUST be skipped.
- **FR-1.9.2** A seed whose symbol or timeframe cannot be determined (both
  `UNKNOWN`) after applying `seed_overrides` MUST be marked `report_mismatch`
  before launching any backtest. No backtest job MUST be created for it.
- **FR-1.9.3** Seed statuses in `seed_scores` table MUST be one of:
  `pending` | `accepted` | `rejected` | `report_mismatch` | `no_report` |
  `parse_error`.
- **FR-1.9.4** Only `accepted` and `rejected` seeds MUST contribute to Universe
  weights.
- **FR-1.9.5** Seeds deleted from the source directory MUST be marked `active=0`
  in the DB and excluded from the UI active count, but their rows MUST be kept
  for historical reference.
- **FR-1.9.6** When a `seed_override` changes the symbol or timeframe of a seed
  that was previously evaluated, the seed MUST be re-evaluated on the next
  `--evaluate-seeds` run.

### 1.10 UBS agent — symbol mapping

- **FR-1.10.1** `symbol_map` MUST be applied to the candidate/seed target symbol
  before comparing against the parsed report symbol.
- **FR-1.10.2** The map MUST be stored as a whitespace-separated list of
  `BROKER_SYMBOL=CANONICAL_SYMBOL` pairs passed via `--symbol-map`.
- **FR-1.10.3** Symbol normalisation MUST strip only trailing broker suffixes
  (e.g. `.a`, `.b`). Symbols starting with a dot (e.g. `.US30Cash`) MUST be
  preserved intact.

### 1.11 Desktop UI

- **FR-1.11.1** The Tkinter UI MUST expose all core workflows: compile, backtest,
  compile-and-backtest, portfolio workbook generation, and UBS agent operations.
- **FR-1.11.2** Long-running operations (compile, backtest, agent) MUST run in
  background threads. Output MUST be streamed line-by-line to the log panel via
  a thread-safe queue and `after()` polling. The UI MUST NOT freeze.
- **FR-1.11.3** The UBS Seeds tab MUST display: status, symbol, TF, score, OK,
  override flag, rejection motivo (criteria that failed with their values),
  and seed filename.
- **FR-1.11.4** The UBS Seeds tab MUST show the active scoring thresholds above
  the table so the user can see at a glance what criteria are in effect.
- **FR-1.11.5** Double-clicking a seed row MUST open its HTML report in the
  system default browser/viewer if a report exists; otherwise show an
  informative message.
- **FR-1.11.6** The UI MUST allow the user to delete a single selected seed file
  from disk (with confirmation) and to bulk-delete all rejected seeds (with
  confirmation showing the count). Both operations MUST remove the corresponding
  `seed_scores` and `seed_overrides` DB rows and refresh the table.
- **FR-1.11.7** Symbol/TF overrides saved via the UI MUST be persisted in
  `seed_overrides` and applied both at seed evaluation time and at UBS generation
  time.
- **FR-1.11.8** The UI MUST support light and dark themes. All input widgets
  (Entry, Combobox, Spinbox) MUST use the theme foreground/background colours
  defined in `COLORS`.
- **FR-1.11.9** UI state (paths, thresholds, theme, multiterminal profiles) MUST
  be persisted in `ui_settings.ini` and restored on startup.
- **FR-1.11.10** The evaluation confirmation dialog MUST show both the total seed
  count AND the expected backtest count (seeds that will actually run), not just
  the total.

### 1.12 Packaging & runtime

- **FR-1.12.1** The app MUST run both from source (`python app_ui.py`) and as a
  PyInstaller-frozen executable. All `BASE_DIR` / `DATA_DIR` path logic MUST
  branch on `sys.frozen`.
- **FR-1.12.2** The installer MUST be buildable via
  `tools/build_installer.ps1` and produce a self-contained `.exe` and a
  portable `.zip` under `dist_installer/`.
- **FR-1.12.3** Generated/runtime directories (`configs/`, `logs/`, `reports/`,
  `outputs/`, `build_installer/`, `dist_installer/`) MUST NOT be committed to
  version control.

---

## 2. Technical debt backlog

Each item has an ID (`TD-x.y`), a short description, and a recommendation.
Resolved items go to [§ 2.8 Resolved](#28-resolved-debt).

### 2.1 UBS agent / scoring

- **TD-2.1.1 — `min_positive_month_ratio` default is 0.0.**
  The positive-month-ratio threshold defaults to zero, effectively disabling it.
  Decide on a meaningful minimum (e.g. 0.55) and document the rationale; until
  then the criterion silently contributes nothing to filtering.

- **TD-2.1.2 — Score formula not documented as a requirement.**
  The formula in `ubs_score._score_formula` has magic constants (caps, weights,
  penalties) with no written rationale. Any change silently breaks comparability
  of historical scores. Document the intent and add a `score_version` field to
  `seed_scores` / `candidates` so old rows can be re-scored after formula updates.

- **TD-2.1.3 — No re-score after threshold change.**
  If the user changes scoring thresholds in the UI, `accepted`/`rejected` rows in
  the DB are stale. The agent should detect threshold drift and offer a re-score
  pass that doesn't require re-running backtests (scores already exist in
  `metrics_json`).

- **TD-2.1.4 — `report_mismatch` rows re-evaluated every run.**
  Seeds stuck as `report_mismatch` (symbol/TF cannot be inferred, no override)
  are never in `{"accepted","rejected"}` so they are always queued. Add a
  `blocked` terminal status for seeds with no fixable path, or surface them
  distinctly in the UI, to avoid wasting backtest slots.

### 2.2 Seed management

- **TD-2.2.1 — Deleting a seed file doesn't remove its override.**
  If the user deletes a seed manually (outside the UI delete button), the
  `seed_overrides` row persists indefinitely. Clean up orphan overrides when
  seeds are marked `active=0`.

- **TD-2.2.2 — No bulk override editor.**
  Overriding symbol/TF requires selecting each seed row individually. A CSV-import
  or bulk-edit dialog would save time when correcting many mismatched seeds at
  once.

- **TD-2.2.3 — Rejected seeds with 0 trades are not distinguished.**
  Seeds scoring `-55.00` with 0 trades, 0 net profit, 0 PF look identical in the
  table to seeds with real trades that failed a threshold. A `no_trades` indicator
  or separate status would make triaging faster.

### 2.3 Multiterminal

- **TD-2.3.1 — No health check between jobs.**
  If a terminal crashes mid-batch, its jobs are silently lost (no report produced,
  candidate stays `no_report`). Add per-terminal heartbeat detection and re-queue
  failed jobs to another terminal.

- **TD-2.3.2 — Terminal profile validation only at launch.**
  Invalid paths in `[Terminal.N]` profiles are only caught when the user clicks
  "run". Validate profiles when they are saved and surface errors in the profile
  editor.

### 2.4 Portfolio manager

- **TD-2.4.1 — `PORTFOLIO_ACCOUNT_BALANCE` is a hardcoded constant.**
  The value `1000.0` is buried in `portfolio_manager/generator.py`. Expose it as
  a UI setting so users with different initial deposits get accurate portfolio DD
  figures.

- **TD-2.4.2 — No incremental workbook updates.**
  Every workbook regeneration parses all HTML reports from scratch. For large
  portfolios this is slow. Cache parsed `StrategyReport` objects (keyed on report
  path + mtime) and only re-parse changed files.

- **TD-2.4.3 — Chart images embedded as raw bytes.**
  Embedded chart images are stored as raw bytes in the report dataclass.
  Large portfolios can exhaust memory. Stream images lazily or write them to a
  temp directory and embed from disk.

### 2.5 Report parser

- **TD-2.5.1 — Encoding detection is brittle.**
  Reports are detected as UTF-16 or UTF-8 by a heuristic. Use `chardet` or the
  BOM (`\xff\xfe`) explicitly, and handle encodings like `windows-1252` that
  some MT5 versions produce.

- **TD-2.5.2 — Spanish label set may be incomplete.**
  The Spanish label mapping was added reactively as missing labels were discovered.
  A comprehensive test with a full Spanish MT5 report (all sections) is needed to
  confirm no labels are silently dropped.

### 2.6 UI / UX

- **TD-2.6.1 — No progress indicator for long seed evaluations.**
  During `--evaluate-seeds`, the log scrolls output but there is no progress bar
  showing `N / total` seeds completed. The user cannot estimate remaining time.

- **TD-2.6.2 — Sorting in the Seeds tree resets after refresh.**
  When the seed table is refreshed (e.g. after delete or evaluate), any active
  column sort is lost. Restore the sort state after `_refresh_ubs_seeds`.

- **TD-2.6.3 — MOTIVO column truncates on narrow windows.**
  The rejection reason string (e.g. `net profit: -830 | PF: 0.69 | DD: 96.6%`)
  is cut off when the window is narrow. Add tooltip support on hover so the full
  reason is always readable.

- **TD-2.6.4 — Combobox arrow colour may not match theme on all Windows versions.**
  `TCombobox` arrow color is set via `arrowcolor` in the style, but some ttk
  themes on Windows 10/11 ignore this. Test and apply a workaround for the native
  dropdown arrow if needed.

### 2.7 Observability / logging

- **TD-2.7.1 — No structured log format.**
  Logs are plain-text lines written to `logs/last_run.log`. There is no JSON
  output, no log level filtering, and no rotation policy. Add level-aware logging
  (e.g. via Python `logging`) with rotation.

- **TD-2.7.2 — No Telegram notification for seed evaluation completion.**
  Normal backtests can optionally notify via Telegram. Seed evaluation runs have
  no such notification even though they can take hours. Extend the notification
  hook to cover `--evaluate-seeds`.

- **TD-2.7.3 — Agent prints in Spanish and English inconsistently.**
  `ubs_agent.py` mixes Spanish (`AVISO:`, `Semillas detectadas:`) and English
  log lines. Pick one language for machine-readable output to simplify log
  parsing.

### 2.8 Resolved debt

_(Move closed items here with a date and brief note.)_

- **2025-06** — Fixed portfolio parser to support English MT5 HTML reports
  (`Symbol`, `Period`, `Results`, `Orders`, `Deals`, `Balance Drawdown …`).
  Previously all metrics were zero for English-language reports.

- **2025-06** — Added `report_mismatch` seed state. Seeds whose inferred
  symbol/TF does not match the executed report are now quarantined before
  feeding Universe weights.

- **2025-06** — Seed evaluation skip logic now includes symbol/TF change
  detection. Saving a `seed_override` on an already-evaluated seed correctly
  triggers re-evaluation on the next `--evaluate-seeds` run.

- **2025-06** — Evaluation confirmation dialog now shows actual backtest count
  (seeds that will run) separately from total seed count.
