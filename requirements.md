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
- **FR-1.2.8** When a `.set` contains `ForceSymbol`, the tester `Symbol` MUST
  preserve that literal broker symbol and casing unless an explicit symbol map
  rewrites it. UBS-generated variant backtests MUST prefer the generated target
  timeframe from the set path/name over inherited timeframe hints from the
  source seed.
- **FR-1.2.9** Symbol inference MUST recognise broker/index names such as
  `.JP225Cash` / `JP225Cash` before broad aliases such as `GOLD -> XAUUSD`.
  A generated path like `JP225Cash/H4/...GOLD...set` MUST run on `.JP225Cash`,
  not on `XAUUSD`.

### 1.3 Multiterminal execution

- **FR-1.3.1** When `--multi-terminal` is passed, backtest jobs MUST be
  distributed across all enabled terminal profiles defined in `ui_settings.ini`
  `[Multiterminal]` / `[Terminal.N]` sections.
- **FR-1.3.2** The concurrency limit MUST be `min(max_workers, enabled_terminal_count,
  job_count)`. The runner MUST never spawn more workers than there are jobs.
- **FR-1.3.3** Each terminal profile MAY override: `enabled`, `name`, `mt5_path`,
  `data_dir`, `experts_root`, `ubs_ex5_file`, `portable`.
- **FR-1.3.4** Compilation MUST remain sequential even in multiterminal mode.
- **FR-1.3.5** In UBS multiterminal mode, every enabled profile MUST point
  `ubs_ex5_file` to a UBS / Ultimate Breakout System `.ex5`. Profiles that
  point to another EA MUST fail validation before MT5 is launched.
- **FR-1.3.6** Multiterminal workers MUST keep the same `/config` execution
  contract as single-terminal mode: launch one MT5 process for one generated
  `.ini`, wait for that process to exit, collect fresh reports, then move to the
  next job. The runner MUST NOT assume a running MT5 instance can receive a new
  `/config` reliably.
- **FR-1.3.7** Running-terminal checks MUST block MT5 instances that are already
  open before a batch starts, including every active multiterminal profile. The
  UI MUST show a specific MT5-open alert for exit code
  `RUNNING_TERMINAL_EXIT_CODE`. Checks MUST NOT interrupt terminal processes
  opened by the currently active batch itself.

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
- **FR-1.6.8** Before mutating a variant, the agent MUST apply any values from
  `outputs/ubs_global_params.json` for keys listed in
  `outputs/ubs_mutation_overrides.json` `frozen_override`. This injects the
  globally configured fixed value into every generated variant regardless of
  what value the seed file holds for that key.
- **FR-1.6.9** When `--force-unseeded-universe` is enabled, target selection
  MUST reserve exploration for universe assets and timeframes not represented
  by the current seed pool. The forced branch MUST prefer assets/TFs with no
  feedback yet, MUST remain disabled by default, and MUST continue excluding
  disabled universe symbols.
- **FR-1.6.10** Every generated UBS variant MUST contain
  `ForceSymbol=<target_symbol>`. If the source seed lacks `ForceSymbol`, the
  agent MUST add it to the generated `.set` so tester symbol inference cannot
  fall back to inherited source-seed aliases.

### 1.7 UBS agent — scoring

- **FR-1.7.1** Score computation MUST use `ubs_score.ScoreResult` with these
  configurable thresholds (CLI flags, overridable in the UI):

  | Metric | Default | Direction |
  |--------|---------|-----------|
  | Net profit | 100.0 | > |
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
- **FR-1.7.5** After each MT5 batch, reports older than the batch start time MUST
  be ignored. History-cache failures or stale files MUST NOT be scored as if
  they belonged to the current backtest.

### 1.8 UBS agent — candidate lifecycle

- **FR-1.8.1** Candidate statuses in SQLite `candidates` table MUST be one of:
  `generated` → `accepted` | `rejected` | `no_report` | `parse_error` |
  `report_mismatch` | `no_trades`.
- **FR-1.8.2** `accepted`, `rejected`, and `no_trades` candidates MUST
  contribute to Universe asset/timeframe feedback. `accepted` rows contribute
  score plus accepted bonus. `rejected` rows contribute score minus a fixed
  rejection penalty and per-cause penalties from `metrics_json.reasons`.
  `no_trades` contributes a fixed negative execution/reliability penalty.
  `report_mismatch`, `no_report`, and `parse_error` MUST NOT contribute to
  weights.
- **FR-1.8.3** `report_mismatch` and `no_report` rows MUST be retryable:
  - Single candidate: UI "Reprobar mismatch" → copies `.set` to
    `outputs/ubs_agent/<run>/retry_mismatch/`, re-evaluates, updates the
    original DB row.
  - Run-level: "Reprobar run" → copies all mismatches from the run, evaluates
    all produced reports. Partial failures leave failed candidates as `no_report`.
  After a retry updates the original row to `accepted` or `rejected`, that row
  MUST enter the normal weight pool.

- **FR-1.8.4** Accepted candidates MAY be evaluated in a separate OOS robustness
  pass with `ubs_agent.py --evaluate-robustness --robust-run-id <id>`.
  Robustness MUST copy accepted candidate `.set` files into
  `outputs/ubs_agent/<run>/robustness/...`, run `run_tests.py` on that folder,
  validate report symbol/timeframe using the same `symbol_map` rules, and store
  results in `candidate_robustness` without overwriting base `candidates.score`.
- **FR-1.8.5** Robustness statuses MUST be one of: `accepted`, `rejected`,
  `no_report`, `parse_error`, `report_mismatch`, `no_trades`.
- **FR-1.8.6** All base `accepted`/`rejected`/`no_trades` candidates MUST
  continue contributing to weights through the shared `ubs.weights` formula.
  Robustness adds an OOS adjustment only for evaluated rows: `accepted` adds
  `positive_bonus`; `rejected` adds `negative_bonus` plus per-cause OOS
  penalties; `no_report`, `parse_error`, `report_mismatch`, and OOS
  `no_trades` add no robustness bonus. `AgentMemory` feedback methods and the
  `UBS Universo` UI MUST use identical logic.

### 1.9 UBS agent — seed evaluation

- **FR-1.9.1** `--evaluate-seeds` MUST run a dedicated backtest for each seed
  that is new, modified (different mtime/size), has a changed symbol/TF (via
  override), or has a retryable status (`pending`, `no_report`, `parse_error`).
  Seeds already evaluated without changes MUST be skipped. `report_mismatch` is
  a quarantined ready state and MUST NOT be re-run unless the seed file or its
  symbol/TF override changes.
- **FR-1.9.1a** If `_manifest.csv` exists in the seed directory, it MAY provide
  metadata for listed seeds, but it MUST NOT hide additional `.set` files present
  under the source directory. Unlisted `.set` files MUST still be loaded,
  registered in `seed_scores`, and evaluated normally.
- **FR-1.9.2** A seed whose symbol or timeframe cannot be determined (both
  `UNKNOWN`) after applying `seed_overrides` MUST be marked `report_mismatch`
  before launching any backtest. No backtest job MUST be created for it.
- **FR-1.9.3** Seed statuses in `seed_scores` table MUST be one of:
  `pending` | `accepted` | `rejected` | `report_mismatch` | `no_report` |
  `parse_error` | `no_trades`.
- **FR-1.9.4** `accepted`, `rejected`, and `no_trades` seeds with stored reports
  MUST contribute to Universe weights at full base strength, the same as
  generated candidates. Seeds MUST NOT receive robustness/date bonus unless a
  separate seed bonus rule is explicitly configured.
  `report_mismatch` is ready for the purpose of pending counts, but it MUST NOT
  contribute to weights.
- **FR-1.9.4a** A parsed MT5 report with zero closed trades MUST be stored as
  `no_trades`, not as ordinary `rejected`. `no_trades` is retryable and MUST
  contribute only the shared fixed negative execution/reliability penalty.
- **FR-1.9.5** Seeds deleted from the source directory MUST be marked `active=0`
  in the DB and excluded from the UI active count, but their rows MUST be kept
  for historical reference.
- **FR-1.9.6** When a `seed_override` changes the symbol or timeframe of a seed
  that was previously evaluated, the seed MUST be re-evaluated on the next
  `--evaluate-seeds` run.
- **FR-1.9.7** Resetting seed evaluation from the UI MUST delete stored seed
  report files where possible, reset active `seed_scores` rows to `pending`, and
  clear `score`, `accepted`, `metrics_json`, `report_path`, and `evaluated_at`.
  Source `.set` files MUST NOT be deleted by this reset.
- **FR-1.9.8** After seed evaluation is reset, Universe weights MUST be hidden or
  blocked until the user completes seed evaluation and explicitly applies weights
  with the UI "Calcular pesos" action.
- **FR-1.9.9** Seed acceptance thresholds MUST be independent from UBS agent
  generation thresholds in the UI. The default seed net-profit threshold MUST be
  `0`, meaning a seed passes net profit only when `net_profit > 0`.
- **FR-1.9.10** Running `--evaluate-seeds` MUST re-score already evaluated
  `accepted`/`rejected` seed rows from their stored reports using the current
  seed thresholds, without requiring another MT5 backtest when the seed file and
  symbol/TF are unchanged.
- **FR-1.9.11** `ubs_agent.py --rescore-seeds-only` MUST re-score existing
  active `accepted`/`rejected` seed rows from stored reports and MUST NOT require
  an MT5 expert path or launch MT5.
- **FR-1.9.12** Before launching new seed backtests, `--evaluate-seeds` MUST
  reconcile reports left by interrupted `outputs/ubs_agent/seed_eval/eval_*`
  batches. It MUST match copied `.set` files back to source seeds by file
  content, validate symbol/TF against the report, and update `seed_scores` so
  completed jobs do not remain stuck as `pending`.
- **FR-1.9.13** `--evaluate-seeds --reconcile-seed-eval-only` MUST perform only
  that interrupted-batch reconciliation and MUST NOT require an MT5 expert path
  or launch MT5.
- **FR-1.9.14** `ubs_agent.py --retry-seed-path <path>` MUST relaunch one UBS
  seed backtest and update its existing `seed_scores` row.

### 1.10 UBS agent — symbol mapping

- **FR-1.10.1** `symbol_map` MUST be applied to the candidate/seed target symbol
  before comparing against the parsed report symbol.
- **FR-1.10.2** The map MUST be stored as a whitespace-separated list of
  `BROKER_SYMBOL=CANONICAL_SYMBOL` pairs passed via `--symbol-map`.
- **FR-1.10.3** Symbol normalisation MUST strip only trailing broker suffixes
  (e.g. `.a`, `.b`). Symbols starting with a dot (e.g. `.US30Cash`) MUST be
  preserved intact.

### 1.11 UBS agent — parameter mutability overrides

- **FR-1.11.1** Key mutability MUST be determined by `is_agent_mutable_key(key)`
  in `ubs_agent.py`. This function checks `ubs_mutation_overrides.json` first,
  then the hardcoded `FROZEN_KEYS`, `FROZEN_PREFIXES`, `ALLOWED_MUTATION_KEYS`,
  and `ALLOWED_MUTATION_PREFIXES` constants.
- **FR-1.11.2** `outputs/ubs_mutation_overrides.json` MUST support two override
  types:
  - `frozen_override`: `{key: ""}` — normally mutable keys the user has frozen.
    The agent will NOT mutate these keys.
  - `mutable_override`: `["key"]` — normally frozen keys the user has made
    mutable. The agent MAY mutate these keys.
- **FR-1.11.3** `outputs/ubs_global_params.json` MUST store the canonical global
  value for every EA parameter. When a key appears in `frozen_override`, its
  value from this file MUST be injected into every generated variant, overriding
  whatever the seed file holds.
- **FR-1.11.4** On first launch of the UBS Parámetros tab, if
  `ubs_global_params.json` does not exist, it MUST be bootstrapped from the
  first available seed file and saved immediately.
- **FR-1.11.5** Any edit made in the UBS Parámetros tab MUST be persisted to
  `ubs_global_params.json` immediately (no separate save required for individual
  edits, though a bulk "Guardar" button also exists).

### 1.12 Desktop UI

- **FR-1.12.1** The Tkinter UI MUST expose all core workflows: compile, backtest,
  compile-and-backtest, portfolio workbook generation, and UBS agent operations.
- **FR-1.12.2** Long-running operations (compile, backtest, agent) MUST run in
  background threads. Output MUST be streamed line-by-line to the log panel via
  a thread-safe queue and `after()` polling. The UI MUST NOT freeze.
- **FR-1.12.3** The UBS Seeds tab MUST display: status, symbol, TF, score, OK,
  override flag, rejection motivo (criteria that failed with their actual values),
  and seed filename. The motivo format is `metric: value | metric: value`.
- **FR-1.12.4** The UBS Seeds tab MUST expose editable seed-only scoring
  thresholds above the table. These controls MUST be persisted in
  `ui_settings.ini` separately from the UBS Agent thresholds.
- **FR-1.12.5** Double-clicking a seed row MUST open its HTML report in the
  system default viewer if a report exists; otherwise show an informative message.
- **FR-1.12.6** The UI MUST allow the user to delete a single selected seed file
  from disk (with confirmation), delete all checked seed files, and bulk-delete
  all rejected seeds (with confirmation showing the count). Both operations MUST
  remove the corresponding `seed_scores` and `seed_overrides` DB rows and
  refresh the seeds table AND the Universe weights table.
- **FR-1.12.7** Symbol/TF overrides saved via the UI MUST be persisted in
  `seed_overrides` and applied both at seed evaluation time and at UBS generation
  time.
- **FR-1.12.8** The UI MUST support light and dark themes. All input widgets
  (Entry, Combobox, Spinbox, Radiobutton) MUST use the theme foreground/background
  colours defined in `COLORS` — no system-default white backgrounds on dark mode.
- **FR-1.12.9** UI state (paths, thresholds, theme, multiterminal profiles) MUST
  be persisted in `ui_settings.ini` and restored on startup.
- **FR-1.12.10** The evaluation confirmation dialog MUST show both the total seed
  count AND the expected backtest count (seeds that will actually run), computed
  locally by comparing DB state against the seed files before launching the agent.
- **FR-1.12.11** The UBS Parámetros tab MUST show all EA parameter keys grouped
  by section, with columns: CLAVE, DESCRIPCIÓN, VALOR, RANGO, AGENTE. Values
  are loaded from `ubs_global_params.json`. The AGENTE column indicates `✓ mutable`,
  `— fijo`, `✦ fijo global` (user-frozen with injected value), or
  `✦ forzado mutable` (user-unlocked).
- **FR-1.12.12** The UBS Parámetros tab MUST allow the user to toggle any
  parameter between mutable/frozen via a "Toggle inamovible/mutable" button.
  The change MUST be written immediately to `ubs_mutation_overrides.json` and
  reflected in the table without restart.
- **FR-1.12.13** Treeview column values across all tabs MUST be center-aligned.
- **FR-1.12.14** The UBS Seeds tab MUST expose "Resetear evaluación". It MUST
  confirm the action, reset active seed DB rows to pending, delete stored report
  files when present, refresh Seeds/summary/Universe views, and lock Universe
  weights until recalculation.
- **FR-1.12.15** The UBS Universo tab MUST expose "Calcular pesos". It MUST
  refuse to unlock weights while active seeds remain in a non-ready state. Ready
  states for applying weights are `accepted`, `rejected`, `no_trades`,
  `report_mismatch`, and `disabled_symbol`; other active seed states require
  another evaluation pass or manual triage.
- **FR-1.12.16** Every visible "Actualizar" button MUST refresh the full related
  panel state, not just one tree widget. A failure in one refresh section MUST
  not prevent other sections from refreshing.
- **FR-1.12.17** The UBS Seeds tab MUST expose an "Aplicar criterios" action
  that persists seed thresholds and re-scores existing seed reports without
  launching MT5.
- **FR-1.12.18** The UBS Seeds tab MUST expose a SEL checkbox column. Buttons
  that normally act on one seed (`Abrir seed`, `Abrir reporte`, `Repetir
  backtest`, `Guardar Symbol/TF`, `Eliminar seed`) MUST apply to checked rows
  when any are checked, and fall back to the selected row otherwise.
- **FR-1.12.19** The UBS Universo tab MUST expose a SEL checkbox column and
  controls to disable/enable checked symbols. Disabled symbols MUST be persisted
  in `outputs/ubs_disabled_symbols.json`, remain visible as disabled in the UI,
  be excluded from Universe weights, and be excluded from UBS agent target-symbol
  exploration.
- **FR-1.12.20** UBS seed evaluation MUST skip any seed whose inferred or
  manually overridden symbol maps to a disabled Universe symbol. Skipped seeds
  MUST be recorded as `disabled_symbol`, MUST NOT launch MT5, MUST NOT count as
  pending after a reset, and MUST NOT contribute to weights.
- **FR-1.12.21** The UI MUST expose robustness configuration in `UBS Agente UBS`:
  independent OOS dates, independent thresholds, positive/negative bonus values,
  and an auto-run toggle. Defaults: robust thresholds copy agent thresholds when
  no saved setting exists; positive bonus `+70`; negative bonus `-70`; dates
  empty = template dates.
- **FR-1.12.22** The `UBS Resultados` tab MUST expose `Continuar a robustez`
  for the latest visible run and must confirm the number of accepted candidates
  before launching MT5.
- **FR-1.12.23** The UI MUST include a dedicated `UBS Robustez` tab showing
  accepted candidates from the visible run, SEL checkbox, OOS status, OOS
  rejection cause, OOS score, applied bonus, OOS metrics, date range, set path,
  and report path.
- **FR-1.12.24** If the robustness auto-run toggle is enabled, a successful
  normal UBS agent run with backtests MUST launch robustness automatically for
  accepted candidates. Auto-run MUST NOT trigger after seed evaluation, seed
  rescoring, retry actions, or another robustness run.
- **FR-1.12.25** The UI MUST expose a `Poblar universo sin seed` toggle in
  `UBS Agente UBS`. It MUST persist as `ubs_force_unseeded_universe` and pass
  `--force-unseeded-universe` to normal and continuation UBS agent runs.
- **FR-1.12.26** `UBS Resultados` and `UBS Robustez` MUST display the latest
  visible run (`hidden=0 order by id desc limit 1`). New UBS generation runs
  MUST become visible immediately because `runs.hidden` defaults to `0`.
- **FR-1.12.27** `UBS Historico` MUST list all runs and its candidate table MUST
  include a `ROBUST` column showing robustness status/bonus (`OK +N`,
  `FAIL -N`, neutral status, or `pendiente`).
- **FR-1.12.28** `UBS Comparar` MUST list visible runs and auto-select a newly
  created latest run when it appears. If no newer run exists, it MUST preserve
  the user's manual run selection.

### 1.13 Packaging & runtime

- **FR-1.13.1** The app MUST run both from source (`python app_ui.py`) and as a
  PyInstaller-frozen executable. All `BASE_DIR` / `DATA_DIR` path logic MUST
  branch on `sys.frozen`.
- **FR-1.13.2** The installer MUST be buildable via
  `tools/build_installer.ps1` and produce a self-contained `.exe` and a
  portable `.zip` under `dist_installer/`.
- **FR-1.13.3** Generated/runtime directories (`configs/`, `logs/`, `reports/`,
  `outputs/`, `build_installer/`, `dist_installer/`) MUST NOT be committed to
  version control.

### 1.14 Python dependencies

- **FR-1.14.1** Runtime code MUST remain standard-library-first. Required
  third-party packages are:
  - `lxml` for MT5 HTML parsing.
  - `openpyxl` for Excel workbook generation and image embedding.
- **FR-1.14.2** `Pillow` is optional at runtime. When installed, the UI uses it
  for anti-aliased rounded widgets; without it, the UI MUST fall back to plain
  Tk drawing.
- **FR-1.14.3** Packaging requires `PyInstaller`, but normal source execution
  MUST NOT depend on PyInstaller being installed.
- **FR-1.14.4** `tkinter`, `sqlite3`, `winreg`, `urllib`, and other Windows/Python
  standard library modules MUST NOT be listed as pip dependencies.

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

### 2.2 Seed management

- **TD-2.2.1 — Deleting a seed file doesn't remove its override.**
  If the user deletes a seed manually (outside the UI delete button), the
  `seed_overrides` row persists indefinitely. Clean up orphan overrides when
  seeds are marked `active=0`.

- **TD-2.2.2 — No bulk override editor.**
  Overriding symbol/TF requires selecting each seed row individually. A CSV-import
  or bulk-edit dialog would save time when correcting many mismatched seeds at
  once.

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
  is cut off when the window is narrow. The description bar in UBS Parámetros
  provides a tooltip-style workaround; a similar hover tooltip on the Seeds tree
  would help.

- **TD-2.6.4 — Global params bootstrap uses only first seed.**
  `ubs_global_params.json` is seeded from the first `.set` found alphabetically.
  If that seed has non-representative values (e.g. MaxSpread=5 while most seeds
  use 100), the user must manually correct them. A smarter bootstrap (e.g. median
  across all seeds) would produce a better starting point.

- **TD-2.6.5 — UBS Parámetros tab has no "reset all overrides" button.**
  Removing all user-defined frozen/mutable overrides requires deleting
  `ubs_mutation_overrides.json` manually. A one-click reset would reduce friction.

- **TD-2.6.6 — Weight lock state is session-only.**
  `ubs_weights_locked` is an in-memory Tk variable. If the app restarts after
  "Resetear evaluación" but before "Calcular pesos", the lock state may be lost.
  Persist the lock in `ui_settings.ini` or derive it from pending seed rows.

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

- **2025-06** — UBS Seeds tab: added MOTIVO column showing each rejected
  criterion with its actual value (e.g. `net profit: -830 | PF: 0.69`).
  Column is populated by parsing `metrics_json` from `seed_scores`.

- **2025-06** — UBS Seeds tab: added scoring criteria bar above the table
  with editable seed-only thresholds and `--rescore-seeds-only` reclassification
  without opening MT5.

- **2025-06** — Seed evaluation recovery now reconciles interrupted
  `seed_eval/eval_*` batches by matching copied `.set` file content to source
  seeds and updating `seed_scores` before launching new MT5 jobs.

- **2025-06** — Unchanged `report_mismatch` seed rows are treated as
  ready/quarantined for pending counts and are not re-run until the seed file or
  symbol/TF override changes.

- **2025-06** — Zero-trade MT5 seed reports are classified as `no_trades`
  instead of ordinary rejected rows, and the UBS Seeds tab can relaunch a single
  selected seed backtest.

- **2025-06** — UBS Seeds tab: added "Abrir reporte" button and double-click to
  open the HTML report; "Eliminar seed" and "Eliminar rechazadas" buttons with
  DB cleanup and Universe weight refresh.

- **2025-06** — UBS Seeds tab: added "Resetear evaluación" to clear active seed
  scores/reports without deleting source `.set` files. Universe weights are
  locked after reset.

- **2025-06** — UBS Universo tab: added "Calcular pesos" to explicitly unlock
  and apply weights once active seeds are evaluated or quarantined as mismatch.

- **2025-06** — Fixed `is_agent_mutable_key()` link. The UI previously used
  `is_mutable_key()` from `ubs_generate_sets.py` which has different constants
  from the actual agent mutation logic in `ubs_agent.py`. Now uses
  `is_agent_mutable_key()` defined in `ubs_agent.py` with the correct constants.

- **2025-06** — Added UBS Parámetros tab: global parameter viewer/editor backed
  by `ubs_global_params.json`. Parameters show mutability status per agent rules,
  support inline editing, and allow toggling any key between frozen/mutable via
  `ubs_mutation_overrides.json`.

- **2025-06** — Theme fix: all ttk widgets (Combobox, Radiobutton) now use
  panel background and text colours in dark mode; no system-default white boxes.

- **2026-06** — Package reorganisation: all UI mixins moved to `ui/` package
  (`ui/dashboard_view.py`, etc.) and UBS support modules to `ubs/` package
  (`ubs/memory.py`, etc.). Root has only CLI entry points. `pyproject.toml`
  added. `BASE_DIR` in `ui/` modules correctly uses `.parent.parent`.

- **2026-06** — Independent date ranges: `run_tests.py` and `ubs_agent.py`
  accept `--from-date` / `--to-date` (YYYY.MM.DD) overriding the template.
  UI exposes separate Desde/Hasta fields for UBS Agent and Seeds, persisted
  in `ui_settings.ini`.

- **2026-06** — UBS Results tab: SEL checkbox column, MOTIVO column (same
  format as Seeds), read-only criteria bar showing active thresholds.

- **2026-06** — UBS Results export: `⬇ Exportar run` creates
  `Run_<id>_<date>/aceptados/`, `fallidos/net_profit_positivo/`,
  `fallidos/otros/` with a subfolder per candidate containing `.set` + `.htm`
  + all associated chart images (`stem*.png`, `stem*.gif`). Modal progress
  dialog with determinate progress bar, thread-safe queue.

- **2026-06** — Design system (`ai_context/09-design-system.md`): three button
  types, action-bar pattern, Treeview standard (`stretch=False`, scrollbars,
  sortable, explicit height), input sizes, spacing. Applied consistently
  across all view files.

- **2026-06 (TD-2.6.1 partial)** — Seed evaluation: toolbar redesigned to
  2 rows (primary actions / destructive danger zone). All toolbar buttons now
  follow Type-B style (tk.Button themed in panel_alt bars).

- **2026-06** — SEL checkbox column added to ALL Treeviews (Results,
  History Runs, History Candidates, Compare, Universe Assets, Universe
  Timeframes, Multiterminal). Matching `self.*_checked: set[str]` and
  `_on_*_tree_click()` handlers follow the same pattern as Seeds/Universe.

- **2026-06** — PanedWindow (drag-resizable splits) added to: Comparar
  (horizontal), Universo (horizontal), Histórico (vertical), Multiterminal
  (horizontal). Replaces fixed-weight grid layouts.

- **2026-06** — Config Rutas simplified: removed paths that are duplicates
  of other tabs (Terminal MT5, Carpeta datos MT5, MetaEditor, Archivo .ex5
  UBS, Carpeta .set). Only compilation/template-specific paths remain.

- **2026-06** — Multiterminal: PanedWindow + horizontal scrollbar on editor,
  "Principal" (formerly "Habilitada") enforces radio exclusivity via
  `_apply_multiterminal_editor`, Portable checkbox removed from UI.

- **2026-06** — Universe: SEL in Timeframes table, three weight-reset buttons
  (Limpiar marcados, Reset pesos activos, Reset pesos TF) — set `score=NULL`
  in `candidates` and `seed_scores` without deleting rows.

- **2026-06** — Histórico: Eliminar run (deletes run + all candidates + files
  + reports from disk + sets seed_scores.score=NULL → Universe goes to 0),
  Eliminar set (deletes .set + score=NULL for that candidate).
  Both refresh Universe automatically.

- **2026-06** — Seeds: "Eliminar todas" button. `_cleanup_seed_db()` helper
  used by all three delete methods (deletes seed_scores + seed_overrides;
  does NOT touch candidates generated from those seeds).

- **2026-06** — Seeds: "⬆ Importar seeds" button — folder picker, runs
  `force_fixed_lot_text` on each .set, deduplicates by SHA256 of normalised
  content, copies to configured seeds folder. Modal progress popup +
  summary dialog. Implemented in `ui/ubs_seeds_logic.py:_import_ubs_seeds`.

- **2026-06** — Universe auto-refresh: `_refresh_all()` (called on every
  script completion) already includes `"ubs_universe"`. All direct DB weight
  operations also call `_safe_refresh("ubs_universe", …)` explicitly.

- **2026-06** — Results: "Repetir sin ops" button retries a `no_trades`
  candidate via `--retry-candidate-id`. Mirrors Seeds "Repetir backtest".
  `_retry_no_trades_result()` in `ui/ubs_results_logic.py`.

- **2026-06** — Date fields pre-fill: `ubs_agent_from_date/to_date` and
  `ubs_seed_from_date/to_date` auto-populate from template `FromDate`/`ToDate`
  when empty (via `trace_add` on `template_path`). No_trades on agent runs
  are classified identically to seeds: status `no_trades`, contributing only
  the fixed negative reliability penalty, retryable via "Repetir sin ops".

- **2026-06** — UBS Robustez OOS: `ubs_agent.py --evaluate-robustness` tests
  accepted candidates from a run in a separate date window, stores results in
  `candidate_robustness`, and applies configurable positive/negative weight
  bonuses only for robust `accepted`/`rejected`. UI adds `Robustez OOS`
  configuration in `UBS Agente UBS`, `Continuar a robustez` in `UBS Resultados`,
  a dedicated `UBS Robustez` tab, and an optional auto-run toggle.

- **2026-06** — UBS weight formula moved to shared `ubs.weights`: rejected
  candidates/seeds receive rejection and per-cause penalties, no-trades rows
  receive a fixed reliability penalty, robustness default bonus scale is
  `+70/-70`, correlated candidate groups are averaged before aggregation,
  small samples are shrunk toward zero, and active seed scores with reports
  contribute at the same base strength as generated candidates.

- **2026-06** — Robustness/history polish: `UBS Robustez` gained SEL and CAUSA
  columns; `UBS Historico` candidates gained a ROBUST column; `UBS Comparar`
  auto-selects a newly created latest visible run.

- **2026-06** — Fixed UBS generated symbol safety: generated variants now add
  `ForceSymbol` when missing, and `run_tests.py` recognizes `.JP225Cash` /
  `JP225Cash` before broad aliases like `GOLD -> XAUUSD`.

- **2026-06** — UBS Agent exploration: added `--force-unseeded-universe` and
  the `Poblar universo sin seed` UI toggle. When enabled, generation reserves
  part of asset/TF target selection for universe items not represented in the
  current seed pool, preferring items with no feedback yet.
