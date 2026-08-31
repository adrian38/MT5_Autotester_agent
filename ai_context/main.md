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
   files, backtests variants, scores reports, tests accepted candidates through
   an optional out-of-sample robustness window and a Final Tick dual-model
   comparison, an optional backward OHLC regression holdout, and stores all
   results in SQLite.

The main user-facing entry point is the Tkinter desktop app in
[`app_ui.py`](../app_ui.py). The same functionality is also available through
CLI scripts (`compile_mq5.py`, `run_tests.py`, `compile_and_backtest.py`) and
batch wrappers.

## Must-Know Rules

- This is a Windows/MT5 automation project. Do not assume Linux paths or a
  headless MT5 runtime.
- Keep paths robust for both source execution and PyInstaller frozen
  execution. Most scripts redefine `BASE_DIR` from `sys.executable` when
  `sys.frozen` is true. Modules inside `ui/` must use
  `Path(__file__).resolve().parent.parent` (not `.parent`) to reach the
  project root.
- `tester_template.ini` is the base Strategy Tester configuration. Scripts
  generate per-run `.ini` files under `configs/`; do not hand-edit generated
  configs as source of truth.
- `reports/`, `logs/`, `configs/`, `outputs/`, `build_installer/`, and
  `dist_installer/` are generated/runtime areas. Avoid committing generated
  churn unless the user explicitly wants artifacts.
- MT5 can silently ignore `/config` if the terminal is already open. The code
  has running-process checks; preserve them unless intentionally changing MT5
  launch behavior.
- Backtests launched through `terminal64.exe /config:<ini>` are intentionally
  one MT5 process per job. Do not keep a terminal open and push multiple configs
  into it unless replacing the `/config` runner with a proven queue/control
  integration. In multiterminal mode, each worker may open/close its assigned
  terminal per job; the UI should only block terminals that were already open
  before the batch starts or left behind by a previous failed job.
- Portfolio parsing must support both English and Spanish MT5 HTML reports.
  Recent MT5 reports in this workspace use English labels (`Results`,
  `Orders`, `Deals`, `Symbol`, `Time`, etc.).
- UBS agent results must validate the actual MT5 report `Symbol`/`Period`
  against the intended candidate target. Do not trust set/report filenames
  alone; stale reports and broker symbol aliases can otherwise poison memory.
- `report_mismatch` is an intentional UBS candidate state. It means the
  report was parsed, but MT5 executed a different symbol/timeframe than the
  candidate target after applying the configured `symbol_map`.
- `symbol_map` is broker-scoped. The legacy `symbol_map` setting belongs to
  RoboForex compatibility; current UI settings use `symbol_map_roboforex`,
  `symbol_map_ictrading`, and `symbol_map_axi`.
- UBS parameter mutability is defined by `is_agent_mutable_key()` in
  `ubs_agent.py` — NOT by the Y/N flag in `.set` files (that is the MT5
  optimizer flag, a different thing) and NOT by `is_mutable_key()` in
  `ubs_generate_sets.py` (which has different constants). Always use
  `is_agent_mutable_key()` when reasoning about what the agent will mutate.
- UBS stage `.set` copies enforce `UseEveryTick`: `false` for generated/base
  results, OOS robustness, backward regression, and the OHLC copies in short
  Final Tick and Final Tick 6M; `true` only for their real-tick copies. Stage
  copies must not modify the candidate source `.set`.
- `app_ui.py` is the composition/layout root. Screen mixins live in `ui/`
  (`ui/dashboard_view.py`, `ui/dashboard_logic.py`, etc.) and UBS support
  modules in `ubs/` (`ubs/memory.py`, `ubs/score.py`, `ubs/account.py`,
  `ubs/manual_status.py`, etc.). Do NOT grow `app_ui.py` or `ubs_agent.py`
  with logic that belongs in a domain module.
- **Broker/account isolation**: RoboForex ECN/PRO, ICTrading STANDARD, and AXI
  STANDARD/PREMIUM use separate SQLite DBs, seed dirs, output dirs, and results.
  Asset universes are broker-scoped; disabled-symbol GEN/SEEDS policies are
  broker/account-scoped. Timeframe universe configuration is the only shared
  universe for now. All path resolution goes through `ubs/account.py` helpers.
  Never hardcode `ubs_memory.sqlite`; always call `account_memory_path()`.
- For every substantial UI screen/tab use a view/logic pair inside `ui/`:
  `ui/<screen>_view.py` for widgets/layout and `ui/<screen>_logic.py` for
  behavior/state/persistence. This is the mandatory structure for all tabs.
- **UI design rules are in `09-design-system.md`. Read it before touching any
  widget.** Three button types, action-bar pattern, Treeview standard, and
  input sizes are all defined there. Using a wrong button type or omitting
  `stretch=False` on a Treeview column is a bug.
- **Not everything under `assets/` belongs to this project.**
  `MT5_Autotester_agent_manager` reads this directory (node `axi` in its
  `manager.json`) and is the *only* consumer of
  `assets/<broker>_symbol_specs.json` (margin per minimum position, minimum lot,
  contract size) and `assets/<broker>_max_product_leverage.json` (product
  leverage caps). `assets/<broker>_normalization.json` is shared: this project
  scores with it, the manager inverts it into notional per position. The
  dependency is one-way — nothing here reads the manager. Before "fixing" one of
  those files, check who consumes each field; see § 1.16 of `requirements.md`.

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
| [09-design-system.md](09-design-system.md) | UI design rules: button types, input sizes, Treeview standard, spacing, colours. |
| [10-regression-validation.md](10-regression-validation.md) | Backward 2017-2019 OHLC validation: rationale, scoring, statuses, CLI, and official MT5 sources. |
| [11-live-audit.md](11-live-audit.md) | Embedded manager-node live-account audit, A/M/C variants, MT5 history synchronization, and diagnostics. |
| [12-manager-universe-sync.md](12-manager-universe-sync.md) | Remote symbol sync, history probe, no_history policy, and node process control. |
| [13-manager-status-during-repair.md](13-manager-status-during-repair.md) | Nonblocking status/log snapshots during bulk repair and stale manager cards. |

## Common Entry Points

- Desktop UI: `python app_ui.py`
- Compile EA(s): `python compile_mq5.py`
- Run backtests: `python run_tests.py`
- Compile then backtest: `python compile_and_backtest.py`
- UBS agent: `python ubs_agent.py`
- Build installer: `powershell -ExecutionPolicy Bypass -File tools/build_installer.ps1`
- Portfolio workbook generation: functions in `portfolio_manager/generator.py`

## UI Sidebar Tabs (section keys)

| Key | Spanish label | View module | Logic module |
|-----|--------------|-------------|--------------|
| `panel` | Panel | `ui/dashboard_view.py` | `ui/dashboard_logic.py` |
| `multiterminal` | MT5 Multiterminales | `ui/multiterminal_view.py` | `ui/multiterminal_logic.py` |
| `portfolio` | Portfolio | `ui/portfolio_view.py` | `ui/portfolio_logic.py` |
| `configuracion` | Configuracion | `ui/settings_view.py` | `ui/settings_logic.py` |
| `archivos` | Archivos | `ui/files_view.py` | `ui/files_logic.py` |
| `logs` | Logs | (part of files) | (part of files) |
| `agente_ubs` | UBS Agente UBS | `ui/ubs_agent_view.py` | `ui/ubs_agent_logic.py` |
| `ubs_seeds` | UBS Seeds | `ui/ubs_seeds_view.py` | `ui/ubs_seeds_logic.py` |
| `ubs_resultados` | UBS Resultados | `ui/ubs_results_view.py` | `ui/ubs_results_logic.py` |
| `ubs_robustez` | UBS Robustez | `ui/ubs_robustness_view.py` | `ui/ubs_robustness_logic.py` |
| `ubs_final_tick` | UBS Final Tick | `ui/ubs_final_tick_view.py` | `ui/ubs_final_tick_logic.py` |
| `ubs_final_tick_6m` | UBS Final Tick 6M | `ui/ubs_final_tick_6m_view.py` | `ui/ubs_final_tick_6m_logic.py` |
| `ubs_regression` | UBS Regresiva | `ui/ubs_regression_view.py` | `ui/ubs_regression_logic.py` |
| `ubs_historico` | UBS Historico | (part of ubs_results) | (part of ubs_results) |
| `ubs_universo` | UBS Universo | `ui/ubs_universe_view.py` | `ui/ubs_universe_logic.py` |
| `ubs_comparar` | UBS Comparar | (part of ubs_results) | (part of ubs_results) |
| `ubs_params` | UBS Parámetros | `ui/ubs_params_view.py` | `ui/ubs_params_logic.py` |
| `portafolio_ubs` | UBS Portafolio | `ui/ubs_portfolio_view.py` | `ui/ubs_portfolio_logic.py` |
| `portafolio_ubs_mensual` | UBS Portafolio Mensual | `ui/ubs_monthly_portfolio_view.py` | `ui/ubs_monthly_portfolio_logic.py` |
| `buscador` | UBS Buscador | `ui/ubs_search_view.py` | `ui/ubs_search_logic.py` |

## Recent Important Changes

### Manager node — retry a failed resumed stage

`manager_node_runtime/node.py` treats `failed` as resumable only when the saved
job still has a valid `current_step_index` inside a non-empty `pipeline` and a
non-empty `log_path`. This covers a resumed `run_tests.py` stage that exits with
code 1 (for example because an MT5 terminal was still open): the watcher keeps
the pipeline position, so the operator can close the terminal and retry the
same stage. Failures without complete resume context remain non-resumable. The
manager UI must carry the matching `failed` predicate; changing only one side
either hides the button or makes the node reject `/api/v1/jobs/resume`. Ported
nodes advertise `capabilities.failed_resume`, which lets the manager hide the
failed-state action for older broker runtimes during a rolling deployment.

### Manager card historical cleanup

The IC manager node advertises `historical_cleanup` when both
`scripts/cleanOldTest.ps1` and `scripts/cleanOlddata.ps1` exist. The manager can
enqueue `POST /api/v1/jobs/cleanup`; generation cycles clean automatically when
`cleanup_after_run` is enabled, and the manual Repair and Regression jobs
interleave cleanup after every selected run. Cleanup always runs the two agent
scripts in order and then verifies that the MetaQuotes historical trees are
empty. A cleanup failure stops the job before the next run or cycle.

### UBS backward regression validation (2017-2019 OHLC)

Final Tick 6M accepted candidates can now enter an independent fifth evidence
stage: `ubs_agent.py --evaluate-regression`. It uses MT5 `Model=1`, validates
the exact configured report dates, stores rows in `candidate_regression`, and
applies configurable audit points (`+80` accepted; `-100` plus capped cause
penalties on strategy failure). Missing history/report, parse, symbol/TF, and
date mismatches are neutral technical states. The new `UBS Regresiva` tab can
run, resume, rescore, inspect, or manually classify the stage and optionally
starts automatically after Final Tick 6M. See `10-regression-validation.md`.

### Package reorganisation (refactor branch)

All UI screen mixins moved from root to `ui/` package with shorter names:
`app_ui_dashboard_view.py` → `ui/dashboard_view.py`. UBS support modules moved
to `ubs/` package: `ubs_memory.py` → `ubs/memory.py`, etc. Root now only
contains entry-point CLIs and `app_ui.py`. `pyproject.toml` added.

**Import consequences**: `from ui.dashboard_logic import DashboardLogicMixin`,
`from ubs.memory import AgentMemory`, etc.

### Independent date ranges per process

`run_tests.py` accepts `--from-date` / `--to-date` (format `YYYY.MM.DD`);
these override `FromDate`/`ToDate` in the generated `.ini`, leaving the global
template untouched. `ubs_agent.py` accepts the same flags and forwards them to
`run_tests.py`. The UI exposes four new `StringVar`:

| Var | Scope |
|-----|-------|
| `self.ubs_agent_from_date` / `ubs_agent_to_date` | UBS Agent generation runs |
| `self.ubs_seed_from_date` / `ubs_seed_to_date` | Seed evaluation runs |
| `self.ubs_robust_from_date` / `ubs_robust_to_date` | UBS Robustness OOS runs |

All six are persisted in `ui_settings.ini` `[General]`. Empty = uses template dates.

### UBS Robustez OOS

Robustness is a second-stage UBS test for candidates that already passed normal
generation scoring:

- CLI: `ubs_agent.py --evaluate-robustness --robust-run-id <id>`.
- UI:
  - `UBS Agente UBS` has a **Robustez OOS** config block with separate dates,
    separate scoring thresholds, degradation thresholds against Resultados,
    positive/negative bonus values, and an auto-run toggle.
  - `UBS Agente UBS` also has a **Final Tick (Every Tick)** config block
    mirroring the Final Tick tab settings (dates, OHLC retry dates, min history
    quality, min OHLC trades, delta tolerances) plus an **Auto Final Tick**
    toggle (`ubs_final_tick_auto` in `ui_settings.ini`). When enabled, finishing
    a robustness OOS evaluation successfully auto-launches Final Tick
    pending-only for the latest visible run
    (`_maybe_auto_run_ubs_final_tick()` in `ui/ubs_final_tick_logic.py`,
    chained after `_maybe_auto_run_ubs_robustness` in the process-finished
    hook). Full auto chain: generation -> Auto robustez -> Auto Final Tick.
  - `UBS Resultados` has **Continuar a robustez** for the latest visible run.
    This is incremental and passes `--robust-pending-only`, so it only sends
    accepted candidates without OOS already stored. **Reprobar robustez**
    reruns all accepted candidates and overwrites their OOS row.
  - `UBS Robustez` shows accepted candidates from the visible run plus their
    OOS status, cause, score, bonus, report, OOS metrics, and the four
    degradation ratios. Its table has a
    `SEL` checkbox column and a `CAUSA` column derived from OOS
    `metrics_json.reasons`.
- Acceptance is `absolute OOS pass AND degradation pass`. Defaults compare the
  construction result with OOS using annualized normalized-net retention
  `>= 0.50`, PF edge retention `(PF_OOS-1)/(PF_IS-1) >= 0.50`, Recovery
  retention `>= 0.50`, and DD inflation `DD_OOS/max(DD_IS, 2%) <= 2.0`.
  Setting an individual limit to `0` disables it. Missing comparison data is
  auditable but neutral; it does not fabricate a rejection.
- SQLite: results are stored in `candidate_robustness`, separate from base
  `candidates` scores. `degradation_json` stores formula version, windows,
  thresholds, values, availability, pass/fail flags, and final acceptance.
- Selection feedback lives in `ubs/weights.py` and is shared by
  `AgentMemory.asset_feedback()`, `timeframe_feedback()`, `mutation_feedback()`,
  and `UBS Universo`. Discovery estimates the smoothed four-stage probability
  `P(base) * P(OOS|base) * P(probe eligible|OOS) * P(6M accepted|probe)`,
  grouped by correlated source. Its bounded relative log-odds score is centred
  on the global probability, so unknown evidence is neutral. The UI exposes
  probability, confidence and effective 6M trials separately. Mutations use
  relative percentile multipliers `0.5..1.5`; timeframe patch keys are excluded.
  Regression is a Production-only fifth stage and cannot change Discovery
  generation feedback.
- Report score and evolutionary fitness are separate. In Discovery, source
  seeds are ranked by a Beta-smoothed prior-run estimate of whether their child
  variants produced any accepted Final Tick 6M result; each `(run, generation,
  seed)` is one correlated trial and unseen seeds remain neutral. Survivors
  between generations use the regularized candidate-metric model. Production
  retains that metric model for both decisions. All models target Final Tick 6M
  acceptance, exclude technical outcomes and exclude the current run. The
  source and survivor models are recorded in run metadata, and
  probability/weight/evidence are persisted in `generation_seed_selection`.
  Fitness remains a soft weight with applied scale `0.15`, so it cannot replace
  the base report score or the diversity budget.
- The following additive row utility is retained only for legacy audit detail;
  it is no longer used by asset/TF/mutation selection:
  - base `accepted`: score plus accepted bonus (`+20` asset, `+15` TF/mutation).
  - base `rejected`: score minus `REJECTED_BASE_PENALTY` and per-cause
    penalties from `metrics_json.reasons`, capped so rejected rows never add
    positive weight.
  - base `no_trades`: fixed negative reliability penalty (`NO_TRADES_WEIGHT = -40`),
    applied only when the row has a stored `report_path` (a real verified report);
    manual or orphaned `no_trades` rows without report contribute no weight.
  - `report_mismatch`, `no_report`, and `parse_error`: no weight.
  - robust `accepted`: add `positive_bonus` (default `+70`).
  - robust `rejected`: add `negative_bonus` (default `-70`) plus OOS
    per-cause penalties from `ROBUST_REASON_PENALTIES`.
  - **final_tick `accepted`**: add `DEFAULT_FINAL_TICK_ACCEPTED_BONUS` (`+120`).
  - **final_tick `rejected`**: add `DEFAULT_FINAL_TICK_REJECTED_PENALTY` (`−160`)
    minus per-cause penalties from `FINAL_TICK_REASON_PENALTIES`
    (`history_quality: 60`, `drawdown_pct: 55`, `profit_factor: 45`, `trades: 45`).
  - final_tick `pending_*` or no row: `+0` (neutral, no penalty).
  - weights are grouped by correlated candidate source before averaging and
    shrunk toward zero for small samples.
  - active seed scores with scored reports contribute at full base strength,
    the same as generated candidates. Seeds do not receive robustness bonus
    unless a separate seed-date/robustness bonus is explicitly added.
- UBS scoring keeps raw report `net_profit` in metrics, but pass/fail and the
  profit score component use `normalized_net_profit`. Net normalization is
  broker-scoped through `assets/<broker>_normalization.json` plus the active
  broker asset universe. RoboForex keeps the existing factors; brokers without
  a normalization file use neutral factor `1.0`. Metrics JSON includes raw net,
  normalized net, factor, basis, and group.
- **Notional (contract-value) normalization** — the correct way to build a
  broker normalization file. Backtests force `StartLots=0.01`, but the broker
  clamps each order up to the symbol's `volume_min`, so share CFDs run at 1.0
  lot on a tiny per-share notional while forex runs at 0.01 lot (~1000 units).
  A flat per-group factor cannot compare them because stock prices span ~20x.
  `ubs/normalization_gen.py` computes a **per-symbol** factor
  `reference_notional / (lot_used * price * tick_value / tick_size)` from live
  MT5 specs, anchored so 0.01-lot forex ≈ 1.0. Generate/refresh with
  `tools/gen_axi_normalization.py` (reads `volume_min`, `trade_contract_size`,
  `trade_tick_value`, `trade_tick_size`, price via
  `ubs/mt5_symbol_extract.extract_symbol_specs_from_mt5`, or `--specs-json` with
  any spec dump); it is dry-run by default and backs up the old file on
  `--write`. The legacy hand-tuned AXI factors
  (`group_suffix Stocks "+" = 0.01`) wrongly rejected genuinely profitable
  share strategies (e.g. Costco+ +26.9% acct → normalized 2.69 → rejected).

  Three rules exist because breaking any of them silently inflates net profit:

  - **Symbols MT5 cannot convert are rebuilt, not guessed.** `trade_tick_value`
    comes back 0 for every GBX-quoted LSE share (and for any pair whose
    conversion is not loaded when the snapshot is taken). The notional is then
    `price * contract_size * rate`, with the rate implied by the symbols MT5 did
    convert (`implied_currency_rates`). `GBp`/`GBX` are minor units: upper-casing
    `GBp` into `GBP` would undervalue every LSE share by 100x, so
    `currency_key()` keeps them apart.
  - **Unmeasurable ≠ unknown.** A symbol this run could not measure keeps the
    factor of the file being replaced (`carried_symbols`). Only never-measured
    symbols reach `skipped_symbols`.
  - **The group fallback is the group's minimum factor, not its median**
    (`group_factor_policy: min_measured_factor`). The median of AXI Stocks is
    10.0 — the amplification cap of hundreds of cheap US shares — and handing it
    to 102 unmeasured LSE shares inflated their net profit up to 96x
    (RioTinto+: 10.0 applied vs 0.1045 real). Understating an unmeasured symbol
    costs a false reject; amplifying it costs a false accept.

  `--dump-specs <path>` writes the live read as the spec dump the portfolio
  manager consumes (margin per minimum position via `order_calc_margin`, minimum
  lot, contract size, account leverage, all in account currency). It merges with
  the existing file, so a partial read never deletes a measurement; the symbols it
  could not take are reported as `carried_symbols`. Full refresh:

  ```
  py tools/gen_axi_normalization.py --broker AXI --dump-specs assets/axi_symbol_specs.json --write
  py tools/fast_rescore_from_metrics.py --broker AXI --account-type STANDARD
  ```

  After writing, re-apply to stored results with
  `py tools/fast_rescore_from_metrics.py --broker AXI --account-type STANDARD`
  (seconds, no MT5, thresholds read from `ui_settings.ini`). **`ubs_agent.py
  --rescore-*-only` will not do it**: that path deliberately preserves the stored
  factor (`ubs.score.rescore_result`) and only re-applies the gates, which is why
  Final Tick stayed on a July basis for months. The tool re-judges the stages
  whose verdict depends on the net gate (candidates, seeds, robustness) and only
  refreshes the normalization fields of the rest (Final Tick, Final Tick 6M,
  regression, and rows in non-scored states such as `no_trades`).

Current local memory was migrated in June 2026 from old robustness bonus
defaults `+30/-30` to `+70/-70` for rows that still had the old exact defaults.
It was also rescored on 2026-06-06 after adding RoboForex net normalization:
303 active seeds with reports (`accepted=215`, `rejected=88`), 1200 base
candidates (`accepted=719`, `rejected=382`, `no_trades=99`), and 661 OOS
robustness rows (`accepted=500`, `rejected=161`). The audit still reports
accepted candidates pending robustness because normalization promoted new base
accepted rows that have not yet been sent to OOS.

### UBS Final Tick

Final Tick is the stage after robustness. Only rows with
`candidates.status='accepted'` and `candidate_robustness.status='accepted'`
enter this queue.

- CLI: `ubs_agent.py --evaluate-final-tick --final-tick-run-id <id>`.
  Additional flags:
  - `--final-tick-pending-only` — only tests candidates without a stored Final Tick row.
  - `--final-tick-retry-pending-quality` — retries only `pending_history_quality` rows
    (UI button "Reintentar calidad baja"). Implies `--final-tick-skip-ohlc`.
  - `--final-tick-skip-ohlc` — skips the OHLC backtest and reuses `ohlc_metrics_json`
    already stored in `candidate_final_tick`; only re-runs the Every Tick (Model=4).
    When set, dates are read from the OHLC report file on disk (ground truth) rather
    than from `args.from_date`, so the tick always runs against the correct period.
  - `--final-tick-min-history-quality` — minimum acceptable history quality % (default `80`).
  - `--final-tick-min-ohlc-trades` — minimum OHLC trades required before attempting real tick (default `5`).
  - `--final-tick-ohlc-from-date` / `--final-tick-ohlc-to-date` — alternative date range to
    retry the OHLC batch when the primary range yielded too few trades.
  - `--final-tick-max-net-delta-pct`, `--final-tick-max-pf-delta-pct`,
    `--final-tick-max-dd-delta-pct`, `--final-tick-max-trades-delta-pct` — tolerances for
    comparing real-tick metrics against OHLC control metrics (all default `35.0`).
- The agent copies each robust-accepted `.set` twice under
  `outputs/ubs_agent/{BROKER}/{ACCOUNT}/<run>/final_tick/...`: one OHLC batch with `Model=1` and
  one real-tick batch with `Model=4` (`Every tick based on real ticks`).
- Final Tick requires explicit `--from-date` and `--to-date`; the UI defaults to
  `2026.05.01 -> 2026.05.31` as the last robustness segment example.
- Results are stored in `candidate_final_tick` with both report paths, valid
  metrics JSON blobs, `history_quality`, date range, and `similarity_json`.
- **Pending states** (intermediate, not final): `pending_history_quality` — the
  real-tick report has quality below threshold, or Model=4 ended with an empty
  tester context because tick download/synchronization failed;
  `pending_ohlc_trades` — the OHLC batch produced too few trades to make a valid
  comparison, so OHLC retry dates may be used before re-running real tick.
- An empty Model=4 HTML (`Bars=0`, `Ticks=0`, empty symbol/M0) is a technical
  result, not evidence that the broker lacks historical ticks. Its displayed
  History Quality is not trusted. The row stays `pending_history_quality`,
  `similarity_json` records `technical_failure=true` plus retry metadata, and
  invalid Tick score/metrics remain NULL while the report path is preserved.
- On memory initialization, `AgentMemory` idempotently upgrades only the legacy
  signature `rejected + real_tick_no_history + tick_download_failed=true` in
  both `candidate_final_tick` and `candidate_final_tick_6m`. It records a
  `status_audit` object in `similarity_json`; real PF/DD/trades rejections are
  not changed.
- A row is final `accepted` when history quality ≥ threshold AND the three active
  similarity checks pass (see below). `net_profit` is **not** an active check.
- Percentage deltas use a symmetric max-denominator difference:
  `abs(OHLC - tick) / max(abs(OHLC), abs(tick), 1) * 100`. Therefore a configured
  `35%` tolerance is intentionally symmetric and is not the classic percentage
  change measured only from the OHLC value.
- UI: `UBS Final Tick` shows robust-accepted candidates, final status, cause,
  history quality %, OHLC metrics, real-tick metrics, date range, set path, and
  "Abrir set" / "Abrir OHLC" / "Abrir Real Tick" report actions.
  Buttons: **Continuar Final Tick** (pending-only, incremental),
  **Reprobar Final Tick** (replaces all existing rows for the visible run), and
  **Reintentar calidad baja** (re-runs only `pending_history_quality` rows with
  `--final-tick-retry-pending-quality --final-tick-skip-ohlc`).
  A criteria configuration block exposes all thresholds (quality, min OHLC trades,
  delta tolerances, primary and OHLC-retry date ranges) and a **Guardar config** button.
  `UBS Robustez` also exposes `Continuar Final Tick`.
- The UI variables `ubs_final_tick_from_date`, `ubs_final_tick_to_date`,
  `ubs_final_tick_ohlc_from_date`, `ubs_final_tick_ohlc_to_date`,
  `ubs_final_tick_min_history_quality`, `ubs_final_tick_min_ohlc_trades`,
  and the four `ubs_final_tick_max_*_delta_pct` variables are persisted in
  `ui_settings.ini`.

#### Final Tick similarity logic (`final_tick_similarity()` in `ubs_agent.py`)

The same strategy is run twice (OHLC Model=1 and Every Tick Model=4) with the same
date range. The goal is to verify the results are similar enough, not to determine
which is better. All three active checks are **symmetric**.

| Check | Formula | Notes |
|-------|---------|-------|
| `profit_factor` | `\|tick_pf − ohlc_pf\| / max(ohlc_pf, 1.0) × 100` | PF capped [0,10]; floor 1.0 |
| `drawdown_pct` | `\|tick_dd − ohlc_dd\| / max(ohlc_dd, tick_dd, 2.0) × 100` | 2 pp floor prevents false fails on tiny DDs |
| `trades` | `\|tick_tr − ohlc_tr\| / max(ohlc_tr, 1.0) × 100` | Symmetric count |

`net_profit` is stored in `checks["net_profit"]` with `"checked": false` and
`"accepted": true` always — visible in UI/DB for inspection only. It was removed from
active criteria because `normalized_net_profit` depends on the normalization group and
produces false failures when absolute values are small (e.g. BA: −7.1 vs −17.6 → 148%
delta, but PF/DD/trades practically identical).

The short Final Tick probe is a discard filter, not the final live-use gate.
`accepted` advances to Final Tick 6M; `rejected` is terminal and invalidates any
downstream 6M evidence. `pending_ohlc_trades` may also advance because the longer
6M window supplies the missing sample, while `pending_history_quality` and
technical/error states must be resolved first. Neither short `accepted` nor
`pending_ohlc_trades` authorizes live use by itself: only a later Final Tick 6M
`accepted` result makes the strategy portfolio/export eligible. Weight feedback
only teaches future exploration and never overrides candidate-level rejection.

#### `from_date` / `to_date` consistency guard

When the disk-based `skip_ohlc=True` optimization is active (resume pending dir, sets
unchanged, stored dates match), the code now verifies that the first OHLC report on disk
has dates matching `args.from_date` before committing to `skip_ohlc=True`.
`_read_ohlc_report_cfg_dates(path)` parses the Period cell `(YYYY.MM.DD - YYYY.MM.DD)`
from the MT5 HTML (UTF-16-LE encoding). If dates differ, it forces OHLC re-run and
removes stale reports. This prevents the corruption scenario where a previous interrupted
run overwrote the OHLC file with new dates but never updated the DB entry.

Visible-run behavior:

- `UBS Resultados`, `UBS Robustez`, and `UBS Final Tick` use the latest visible run:
  `runs where hidden=0 order by id desc limit 1`.
- New UBS generation runs are inserted with `hidden=0`, so they become the
  visible run immediately.
- `UBS Historico` lists all runs and its candidate table includes a `ROBUST`
  column (`OK +bonus`, `FAIL -bonus`, neutral statuses, or `pendiente`).
- `UBS Comparar` lists visible runs and auto-selects a newly created latest run
  when it appears; if no newer run exists, it preserves the user's manual run
  selection.

### UBS Unseeded Universe Exploration

`UBS Agente UBS` has an explicit generation mode selector:

- `production`: no forced unseeded quota; prioritizes existing evidence.
- `discovery`: enables adaptive unseeded asset/TF coverage.

It is persisted as `ubs_generation_mode` and passed through
`--generation-mode`. The legacy `ubs_force_unseeded_universe` setting/CLI flag
maps to `discovery` for backwards compatibility.

### Timeframe support vs timeframe universe

Two different things, often confused:

- **Supported** = `run_tests.py:TIMEFRAME_ENUM`, the MT5 `ENUM_TIMEFRAMES`
  values the app can decode. `KNOWN_TIMEFRAMES` is derived from it, in duration
  order, and is **the canonical list**: `ubs/selection.py:FITNESS_TIMEFRAMES`,
  the Symbol/TF override combobox and validation, and "Limpiar pesos TF" all
  consume it. Adding a timeframe there enables it everywhere at once — do not
  re-hardcode the list.
- **Universe** = which timeframes generation *targets*
  (`DEFAULT_TIMEFRAME_UNIVERSE`, `BASE_TIMEFRAME_UNIVERSE`, and the shared
  `outputs/ubs_timeframes.json`). Supporting a timeframe does **not** add it to
  the universe.

H2 (`16386`) and H3 (`16387`) were added in Aug 2026 to **both**: they are
supported (classified, validated, evaluated, weighted, selectable as a
Symbol/TF override) **and** part of the default generation universe, so the
agent creates variants on them. MT5 encodes hourly timeframes as
`16384 + hours`, so H6/H8/H12 (`16390`/`16392`/`16396`) are equally valid MT5
values that this app still rejects — the rejection message names the supported
universe rather than claiming the value is not valid MT5.

`related_timeframes()` filters through the active universe, so the H1/H2/H3/H4
neighbour band only takes effect while those timeframes stay in it. The
discovery-mode exploratory quotas (`FORCE_UNSEEDED_TIMEFRAME_MIN_RATIOS`) still
cover only M1/M5/M15/M30; H2/H3 get coverage from the generic "reserve a slot
for each allowed timeframe missing from the selected source seeds" rule.

**Scope**: `TIMEFRAME_ENUM` / `TIMEFRAME_PATTERNS` live in `run_tests.py` and
`ubs_timeframes.json` is a single shared file — timeframe support and universe
are **broker-agnostic**, unlike asset universes and disabled-symbol policies.
A timeframe change affects every broker/account in the clone, and reaches the
other broker clones only through a branch merge.

Normal generation targets M1 / M5 / M15 / M30 / H1 / H2 / H3 / H4 / D1. W1 / MN are
available only through the explicit **Experimentar W1/MN** toggle, persisted
as `ubs_experimental_long_timeframes` and passed as
`--experimental-long-timeframes`. MT5 supports W1/MN, but they are kept opt-in
because their trade frequency and validation cadence differ from normal
intraday/daily generation.

The experimental W1/MN row also exposes timeframe-specific minimum trade
counts. Generation/base scoring and robustness use `--min-trades-w1` and
`--min-trades-mn` for W1/MN only; other timeframes continue using the normal
`--min-trades`. Defaults: W1 base/robust = 12, MN base/robust = 4. Final Tick
uses separate short-window minimums through `--final-tick-min-trades-w1` and
`--final-tick-min-trades-mn` for both scoring and the OHLC pre-check; defaults:
W1 Final Tick = 2, MN Final Tick = 1.

The option reserves part of generation for universe coverage:

- Asset target selection gets an adaptive chance to choose a universe symbol
  not represented by the current seed pool, preferring symbols with no feedback:
  generation 1 = 35%, generation 2 = 25%, later generations = 15%.
- Timeframe target selection gets a smaller adaptive chance to choose related
  timeframes not represented by the current seed pool: generation 1 = 20%,
  generation 2 = 12%, later generations = 8%.
- If an explored asset/TF survives into the next generation as an internal
  candidate seed, it is no longer considered unseeded for that generation.
- Disabled universe symbols remain excluded.
- Selected source seeds are persisted in `generation_seed_selection` with rank,
  asset weight, timeframe weight, diversity, Final Tick 6M fitness probability,
  fitness weight/evidence, and total selection score.
- New generation runs persist launch metadata in `runs.config_json`, including
  `mode`, the legacy-derived `force_unseeded_universe`,
  `experimental_long_timeframes`, the effective
  `timeframe_universe`, W1/MN base minimum trades, W1/MN Final Tick minimum
  trades, score thresholds, execution dates, universe counts, adaptive
  exploration probabilities, and the serialized CLI args. Use this to audit how
  a run was launched instead of inferring the flag from policies.
- Target selection has per-generation anti-concentration caps: group-specific
  max 60% for Forex/Stocks, 40% for Metals, 35% for IndicesEnergies, 25% for
  Crypto, and 40% default for unknown groups; plus max 45% per canonical
  symbol, max 60% per timeframe, and max 30% per canonical symbol+timeframe. Capped
  choices are rerolled and then replaced with an enabled universe fallback when
  needed; policies may include `diversity_reroll`, `diversity_fallback`, or
  `diversity_overflow`.
- Source seed selection and next-generation survivor selection apply the same
  group/symbol/timeframe/symbol+timeframe caps before allowing overflow, so one
  profitable niche cannot monopolize every seed slot when alternatives exist.
- In `discovery` mode, generation reserves exploratory
  target slots for intraday timeframes before normal target creation: M1 2%,
  M5 2%, M15 3%, and M30 5% of planned generation size. It also reserves at
  least one target slot for each allowed timeframe missing from the selected
  source seeds, subject to the normal target diversity caps.
- Generated candidates store true parameter mutations in `mutated_keys` and
  target-timeframe patch keys separately in `timeframe_keys`. The
  `mutation_details_json` payload stores old/new/delta values for future
  directional feedback; timeframe patch keys must not pollute mutation weights.

`UBS Universo` has live search filters for the displayed tables:

- **Buscar activos** filters assets by group, symbol, or aliases only; it does
  not alter the weight calculation.
- **Buscar TF** filters the timeframe table by period only.
- The summary keeps global totals and appends shown/total counts when a search
  filter is active.

### UBS Results tab — new columns, export and retry

- **SEL column** (first): checkbox toggling via `_on_ubs_result_tree_click()`;
  checked set stored in `self.ubs_result_checked`.
- **NET / NET NORM columns**: `NET` is raw report profit; `NET NORM` is the
  broker-normalized net used by pass/fail scoring.
- **MOTIVO column**: shows failing criteria with values (e.g.
  `net profit: -830 | PF: 0.69 | DD: 26.1%`), same format as Seeds.
- **Criteria bar**: read-only display of current agent thresholds above the
  table (reflects `ubs_pass_*` vars in real-time).
- **⬇ Exportar run button**: creates `Run_<id>_<date>/` with:
  - `aceptados/<set_stem>/` — `.set` + `.htm` + all associated `.png`/`.gif`
  - `fallidos/net_profit_positivo/<set_stem>/` — rejected with net_profit > 0
  - `fallidos/otros/<set_stem>/` — everything else
  - Modal progress dialog (blocking, thread-safe queue + `after(40)` poll).
  - `_report_related_files()` uses `REPORT_DIR.glob(f"{stem}*")` to find all
    chart/image files associated with a report.
- **"Repetir sin ops"** button: retries a `no_trades` candidate using
  `--retry-candidate-id`, same mechanism as "Reprobar mismatch". Only
  activates for rows with status `no_trades`. `_retry_no_trades_result()`.
- **"Ejecutar backtests"** button: appears in `UBS Resultados` when the
  visible run has `generated` candidates pending. It validates that the visible
  run matches the latest continuable run, then launches `ubs_agent.py` with
  `--continue-last-run --execute-backtests --backtest-pending-only`. This runs
  only the already-generated pending candidates; it must not advance to new
  generations. If `Auto robustez` is enabled, the normal process-finished hook
  still launches OOS robustness after the pending backtests finish successfully.
- **"Completar run"** handles only already-generated pending backtests and
  planned generations that remain.
- **"Continuar run"** handles only base `report_mismatch`/`no_report` rows.
  Its retries reuse the original base dates from `runs.config_json`; missing
  stored dates block the retry instead of falling back to
  `tester_template.ini`.
- **"Continuar a robustez"** button: sends only accepted candidates that do not
  have a `candidate_robustness` row yet (`--robust-pending-only`).
- **"Reprobar robustez"** button: reruns robustness for all accepted candidates
  in the visible run and replaces existing OOS rows.
- **Retryable problem rows**: `report_mismatch` and `no_report` can be retried
  individually or at run level. Once a retry updates the row to `accepted` or
  `rejected`, it enters the normal weight pool. A `rejected` candidate now
  contributes through `ubs.weights`: raw score minus the base rejection penalty
  and per-cause penalties. A run-level retry does not auto-launch robustness if
  base generations or retryable rows still remain.

### UBS symbol inference / ForceSymbol safety

Generated UBS variants should always carry the intended target symbol:

- `ubs_agent.py:create_variant()` uses `replace_or_add_plain_key()` so
  `ForceSymbol=<target_symbol>` exists even when the source seed lacked that
  key.
- `run_tests.py` recognizes broker/index symbols such as `.JP225Cash` /
  `JP225Cash` before broad aliases such as `GOLD -> XAUUSD`.
- This prevents generated paths such as `JP225Cash/H4/...GOLD...set` from
  being run on `XAUUSD` only because the original seed name contains `GOLD`.

### UBS Portafolio tab / Portfolio Builder

Tab "UBS Portafolio" builds live-trading portfolios from strategy sets that
passed robustness (`candidates.status='accepted'` and
`candidate_robustness.status='accepted'`).

**Inputs**: capital, DD valle %, DD puntual %, portfolio type
(Conservative/Balanced/Aggressive), top-K per symbol, max total candidates, min
trades 2020-2026, optional unit caps (per set, total, per symbol), max sets per
symbol, optional local search, and optional correlation filters (max pair
correlation, max downside correlation, max DD overlap, max portfolio correlation).
The form also exposes a DD safety reserve percentage and deterministic
multi-start search count. All are persisted in `ui_settings.ini` under the
`ubs_portfolio_*` keys.

**Optimizer** (pure math in `portfolio_manager/ubs_portfolio.py`):

1. Load only robustness-accepted sets and exclude sets already present in saved
   portfolios.
2. Parse the 2020-2024 base report (`candidates.report_path`) and 2025-2026
   robustness report (`candidate_robustness.report_path`); these are two parts
   of one historical curve, not IS/OOS filters inside this module.
3. Reconstruct accumulated P/L curves from closed trades, validate the curve net
   against the report net profit when present, and merge into a 2020-2026 curve.
4. Filter only on operational eligibility: accepted, unused, curve present,
   minimum combined trades, and positive combined net. Do not add degradation or
   OOS-style filters.
5. Rank candidates, select top-K per symbol, then optimize integer units where
   `1 unit = 0.01 lot`.
6. Greedy loop: for every possible +0.01 increment, recalculate the whole
   portfolio curve and reject increments that exceed DD valle or DD puntual.
   Choose the valid increment with the best marginal score.
7. Optional local search swaps one unit between selected strategies only when it
   increases net profit and keeps both DD constraints valid.
8. Optional deterministic multi-start search perturbs the local optimum, runs
   local improvement again, and keeps a restart only when it improves net while
   preserving every configured constraint.

Do not reintroduce global scaling (`S = target_dd/current_dd`), risk-parity lot
calibration, StartLots validation, or automatic lot normalization in this module.

**AXI cash/future symbol unification**: `portfolio_symbol_key()` maps both legs
of every `AXI_CASH_FUTURE_SYMBOL_FAMILIES` pair (e.g. `USTECH.sa` and
`NAS100.fs`) to one family key, so top-K per symbol and per-symbol unit caps
treat cash and future of the same underlying as one symbol. Only the suffixed
AXI broker names (`.sa`/`.fs`) are remapped; other brokers' keys are unchanged.

**Broker scope**: UBS full-history and monthly portfolios read candidate pools
only from the accounts belonging to the currently selected broker. RoboForex can
combine ECN+PRO; AXI and ICTrading remain separate pools. Used-set locks,
saved-curve comparisons, quarantine, and repair are scoped to that active
broker's memories.

**Persistence**: `portfolios`, `portfolio_allocations`,
`portfolio_decision_log`, plus legacy-compatible `portfolio_members` in
`outputs/ubs_memory_{BROKER}_{ACCOUNT}.sqlite` (broker/account-scoped; resolved via
`account_memory_path()`). Used-set locks are portfolio-class scoped:
Conservative/Balanced share one pool, while Aggressive only conflicts with
other Aggressive portfolios. Deleting a portfolio frees its locks.

**Quarantine and repair**: `portfolio_quarantine` is stored in the source
candidate's source broker/account memory and is a hard cross-account exclusion
inside the active broker's portfolio eligibility. The Portfolio Builder shows
the quarantined-set table and allows
explicit reinstatement. Double-clicking a saved portfolio opens a member window;
quarantining a member removes it and recalculates the remaining saved metrics.
"Completar portafolio" preserves all remaining members, fills the missing active
strategy slots where constraints permit, and freezes their existing units/lots.
If the remaining allocation exceeds DD after quarantine, it greedily removes
only the minimum existing units needed to make a valid replacement feasible;
only the replacement receives newly optimized units. It then recalculates DD,
correlations, curve, and decision log. A failed repair does not replace the
incomplete saved portfolio. A successful calculation first opens a before/after
preview; SQLite changes only after explicit confirmation. Before applying, a
compressed `portfolio_versions` snapshot is written, and the detail window can
restore the latest version.

The detail window also exposes **Revalidar / optimizar** for already-complete
portfolios. It runs a full candidate reoptimization with the portfolio's saved
constraints and the reserve/restart values currently shown in the form, then
uses the same preview/version/apply workflow.

New generation and reoptimization calculate three proposals from the same pool:
**Maximo beneficio** (configured type/reserve), **Equilibrada** (Balanced with
at least 15% reserve), and **Maximo margen DD** (Conservative with at least 25%
reserve). The proposal window compares net, DD, nominal margin, reserve, units,
strategy count, maximum group concentration, and changed allocations. Selecting
a row refreshes the exact set/unit diff. New generation sends the selected
proposal to the existing Guardar portafolio step; reoptimization applies it to
the existing ID after snapshotting.

Every proposal now includes a deterministic 1,000-simulation circular
moving-block bootstrap of portfolio P/L increments. The comparison shows
valley-DD P50/P95 and probabilities of exceeding the nominal and effective DD
limits. P95 above the effective limit marks the proposal red as `ALERTA`, but
does not block selection. The complete `stress_bootstrap` audit payload
(method, seed, sample/block sizes, thresholds, percentiles, probabilities, and
alert state) is persisted in `portfolios.metrics_json` and recalculated after
portfolio mutations.

**Export sets**: patches each .set with `Risk=2` + integer
`LotPerBalance_step`, writes a human-readable `PORTAFOLIO_<id>_resumen.txt`,
and opens the folder.

### Design system

`ai_context/09-design-system.md` defines three button types, the action-bar
pattern, input field sizes, the Treeview standard, and the spacing/colour
system. All UI code must follow it. Key rules:

- **Type A** (CTA): `RoundedButton`, accent/primary bg, radius=12, pady=10.
- **Type B** (bar compact): `tk.Button` themed, in `panel_alt` frames only.
- **Type C** (card content): `ttk.Button` with a named style.
- All `ttk.Treeview` columns: `stretch=False`, `_attach_tree_scrollbars`,
  `_make_tree_sortable`, explicit `height=`.
- Spinboxes: `width=8`; date entries: `width=14`; criteria entries: `width=8`.

### Shared widget helpers in `app_ui.py`

- `self._tooltip_cls = ToolTip` — attach hover tooltips in any view mixin.
- `self._tooltip_cls(widget, "text")` — call after creating the widget.

### Progress dialog pattern (reusable)

Modal blocking dialog used in Export and Import Seeds. Canonical example:
`ui/ubs_results_logic.py:_export_ubs_results_run` and
`ui/ubs_seeds_logic.py:_import_ubs_seeds`.

```python
dlg = tk.Toplevel(self); dlg.grab_set(); dlg.protocol("WM_DELETE_WINDOW", lambda: None)
bar = ttk.Progressbar(body, mode="determinate", maximum=100, ...)
q: queue.Queue = queue.Queue()
threading.Thread(target=_worker, daemon=True).start()
# _worker sends ("progress", idx, total, label) and ("done", ...) into q
dlg.after(40, _poll)  # poll queue, update bar, call _finish when done
```

### Automatic Universe weight refresh

`_refresh_all()` (called whenever any `_run_script` process finishes) already
includes `"ubs_universe"`. Any direct DB operation that modifies weights must
also call `self._safe_refresh("ubs_universe", self._refresh_ubs_universe)`.
Operations already covered: seed deletion, run deletion, candidate-set deletion,
limpiar-pesos buttons, reset seed evaluation.

### UBS memory audit and SQLite defaults

`ubs/db.py` centralizes UBS SQLite connections. `AgentMemory` enables WAL mode
and UI memory reads/writes use the shared helper with a longer busy timeout.
Use `python .\tools\ubs_memory_audit.py` after UBS runs, seed evaluation,
robustness, or weight formula changes to verify run counts, seed readiness,
stale/missing reports, robustness bonuses, JSON metrics, and current weights.

### UBS Seeds tab — new features

- **SEL checkbox column** + `self.ubs_seed_checked`.
- **Criteria bar**: editable seed-only thresholds (net profit, PF, trades, DD,
  recovery). Persisted separately from UBS Agent thresholds.
- **Fechas Seeds bar**: `ubs_seed_from_date` / `ubs_seed_to_date` override
  `FromDate`/`ToDate` for seed evaluation only.
- **"⬆ Importar seeds"**: folder picker → normalises lot size (lote fijo 0.01
  via `force_fixed_lot_text`) → deduplicates → copies to configured seeds
  folder. Modal progress popup + summary dialog. Implemented in
  `ui/ubs_seeds_logic.py:_import_ubs_seeds`.
  - Dedup lives in `ubs/seed_dedup.py` (`SeedDuplicateIndex`). The import
    **first indexes the `.set` files already in the destination**, then checks
    every incoming file against that index and re-adds each copied file, so the
    batch also dedupes against itself.
  - Seed identity is `(symbol, timeframe, params)`. Two matches are reported
    separately: `exact` (identical normalised content) and `equivalent` (same
    symbol/TF and identical values on **every shared key**, with ≥100 shared
    keys and ≥60% overlap).
  - The `equivalent` pass exists because the EA keeps gaining parameters: the
    same seed re-exported from a newer UBS build carries extra keys, so its
    SHA256 differs and a hash-only dedup reimports the whole pool. This is not
    hypothetical — the `UPDATED SETS UBS_V6.4` import (Aug 2026) copied 292
    files of which 176 were already in the pool.
  - Values are compared after stripping optimisation ranges (`value||min||…`),
    so toggling a parameter's optimise flag does not create a false new seed.
  - Sets whose symbol/TF cannot be inferred **are still imported** (they land in
    `UNKNOWN/`), because "Guardar Symbol/TF" is the supported way to rescue
    them: with an override saved they stop being `invalid_seed` and become
    evaluable. The summary warns how many arrived that way and names the first
    few, so they are a visible decision rather than silent pool noise.
- **"Revisar duplicados"**: audits the seeds already in the pool with the same
  rules and offers to retire the redundant ones. Independent of import, so a
  pool polluted by an older import can be cleaned at any time.
  `ubs/seed_dedup.py:scan_duplicates` groups them; the keeper is chosen by
  `priority` — already evaluated first, then the richest parameter schema, then
  the shortest path (deterministic tie-break).
  - Retiring **moves** the `.set` to
    `outputs/seeds_retiradas/<BROKER>/<ACCOUNT>/<timestamp>/` (never deletes)
    with a `_motivo.txt` recording, per file, why it went and which seed it
    duplicated. That tree is under `outputs/` on purpose: no `.set` scanner
    walks it, so a retired seed cannot re-enter generation or evaluation.
  - It then drops the `seed_scores` / `seed_overrides` rows via
    `_cleanup_seed_db()` and refreshes the Universe. There is no persisted
    weights table — weights derive from `seed_scores` on read — so removing the
    rows is what actually undoes the double-counted weight.
  - `ui/ubs_seeds_logic.py:_ubs_seed_progress_dialog` is the shared modal
    progress popup used by both Importar seeds and Revisar duplicados.
- **"Eliminar todas"**: deletes all `.set` files + their `seed_scores` /
  `seed_overrides` DB rows. `_cleanup_seed_db()` helper used by all three
  delete methods.
- Deleting seeds does NOT clear candidate scores from `candidates` — those
  remain and continue contributing to Universe weights.
- **Date fields auto-fill**: `ubs_agent_from_date`, `ubs_agent_to_date`,
  `ubs_seed_from_date`, `ubs_seed_to_date` are pre-filled with the template's
  `FromDate`/`ToDate` when empty (via `trace_add` on `template_path`).
  Implemented in `ui/ubs_agent_view.py` and `ui/ubs_seeds_view.py`.

### UBS Universe tab — new features

- **SEL column in Timeframes tree** + `self.ubs_timeframe_checked`.
- **"Limpiar marcados"**: `score=NULL` in `candidates` + `seed_scores` for
  checked assets and/or TFs → their weights drop to 0.
- **"Reset pesos activos"**: `score=NULL` for ALL assets.
- **"Reset pesos TF"**: `score=NULL` for all 9 known TF values.
- **PanedWindow horizontal**: Activos | Timeframes drag-resizable.

### UBS Histórico tab — new features

- **PanedWindow vertical**: Runs | Candidatos drag-resizable.
- **SEL column** on both Runs tree and Candidatos tree.
- **ROBUST column** on Candidatos: shows `OK +bonus`, `FAIL -bonus`, neutral
  robustness states, or `pendiente` for accepted rows not yet tested OOS.
- **"Eliminar run"**: deletes run + ALL its candidates from DB + their `.set`
  files + report files (.htm + images). Also sets `seed_scores.score=NULL`
  for all active seeds so Universe weights drop to 0. Refreshes Universe.
- **"Eliminar set"**: for selected/checked candidate(s) — deletes `.set` from
  disk + sets `score=NULL` (weight removed). Candidate row kept in DB.

### UBS Comparar tab — new features

- **PanedWindow horizontal**: Resultados | Diff parámetros drag-resizable.
- **SEL column** on Resultados tree + `self.ubs_compare_checked`.
- Run selector lists visible runs and automatically switches to a newly created
  latest visible run; manual selection is preserved while no newer run exists.

### Multiterminal tab — refactor

- **PanedWindow horizontal**: table | editor drag-resizable.
- **Editor** has horizontal scrollbar via Canvas (long paths fully visible).
- **Portable checkbox removed** from UI (kept in data for compatibility).
- **"Principal"** (was "Habilitada"): only ONE terminal can be principal per
  broker at a time. Clicking SEL unmarks all others visually. "Aplicar fila"
  enforces exclusivity by setting `enabled=False` on other profiles for the same
  broker when Principal=ON.
- Each profile has a `broker` field. Legacy profiles without it are treated as
  `ROBOFOREX`; the active UBS broker filters which terminals are validated,
  cleaned, and passed to `run_tests.py`.
- **SEL column** on Multiterminal tree + `self.multiterminal_checked`.
- Toolbar bar buttons converted to Type B (`tk.Button` themed) — Validar and
  Guardar now follow the action-bar pattern.

### Configuration tab — paths cleaned up

Removed duplicate paths from Config Rutas (they exist in other tabs):

| Removed | Already in |
|---|---|
| Terminal MT5 | Multiterminal profiles |
| Carpeta datos MT5 | Multiterminal profiles |
| MetaEditor | Auto-detected from terminal dir |
| Archivo .ex5 UBS | UBS Agent tab |
| Carpeta .set | UBS Agent tab |

Config Rutas now only shows: MetaEditor (compilation), Carpeta/Archivo .mq5,
Carpeta .ex5, Archivo .set UBS (single-set mode), Template tester.

The Settings tab also has two maintenance actions:
- **Borrar reportes locales**: deletes all `.htm`/`.html` files from the project
  `reports/` directory (quick cleanup without touching MT5).
- **Eliminar datos históricos MT5**: runs `scripts/cleanOldTest.ps1` then
  `scripts/cleanOlddata.ps1` in sequence with a progress bar. Closes MT5, clears
  tester cache, history, bases, and `.fxt`/`.tick` files in configured terminals
  for the active broker,
  and also deletes local project reports. Use before switching brokers or when
  history is corrupted. Blocked while any process is running.

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
- Criteria bar: exposes editable seed-only scoring thresholds above the table.
  These are persisted separately from UBS Agent thresholds; seed net profit
  defaults to strict `net_profit > 0`.
- Running seed evaluation re-scores existing `accepted`/`rejected` seed reports
  with the current seed thresholds, without rerunning MT5 when files and
  symbol/timeframe are unchanged.
- Interrupted seed evaluations are resumable: before launching new MT5 jobs,
  `--evaluate-seeds` reconciles completed reports from `seed_eval/eval_*` by
  matching copied `.set` file content back to source seeds. The CLI also has
  `--evaluate-seeds --reconcile-seed-eval-only` for report/SQLite recovery
  without opening MT5.
- "Aplicar criterios" in the Seeds tab persists seed thresholds and runs
  `ubs_agent.py --rescore-seeds-only`, so existing reports are reclassified
  without launching MT5.
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
- `no_trades` and `report_mismatch` seed rows are treated as ready/quarantined
  for pending counts; `no_trades` contributes the fixed negative reliability
  weight, while `report_mismatch` contributes no weight. They are not re-run
  until the seed file or symbol/TF override changes, except via explicit retry.
- Empty MT5 seed reports (`Symbol` empty and/or `Period=M0`) use the separate
  retryable `pending_tester_context` state. Interrupted-run reconciliation must
  ignore those artifacts so they cannot consume the pending job before MT5 is
  launched again.
- MT5 seed reports with zero closed trades are classified as `no_trades`; the
  Seeds tab exposes "Repetir backtest" to relaunch one selected seed directly.
- Seeds and Universe tables have a SEL checkbox column. Seed actions use checked
  rows when present, otherwise the selected row. Universe checked symbols can be
  disabled/enabled; disabled symbols are persisted per broker/account in
  `outputs/ubs_disabled_symbols_{BROKER}_{ACCOUNT}.json`, remain visible, and are excluded
  from agent target-symbol exploration for that account. The same JSON can store
  `seed_enabled_when_disabled`: these symbols remain `GEN=no` for generation but
  `SEEDS=si` lets their seeds run, score, contribute weights, and act as
  mutation sources. Disabled symbols without `SEEDS=si` remain excluded from
  weights, seed backtest execution, pending counts after reset, and source-seed
  selection. Seed evaluation records skipped disabled symbols as
  `disabled_symbol` without launching MT5.
- Evaluation dialog shows the actual expected backtest count (pre-computed from
  DB state) alongside the total seed count.
- Refresh buttons now refresh full panel state, and `_refresh_all()` isolates
  section errors so one broken view does not block every tab.

### Broker/account system

`ubs/account.py` centralises broker/account path resolution. Each broker/account
pair gets separate SQLite storage, ready-seed directories, generated output
directories, result/weight history, and disabled-symbol GEN/SEEDS policy files.
Broker-level asset universe files are shared by accounts inside the same broker.
Timeframe universe configuration is shared globally for now.

```
BROKER_ACCOUNTS = {
    "ROBOFOREX": ("ECN", "PRO"),
    "ICTRADING": ("STANDARD",),
    "AXI": ("STANDARD", "PREMIUM"),
}
DEFAULT_BROKER = "ROBOFOREX"
DEFAULT_ACCOUNT_TYPE = "ECN"
```

| Helper | Returns |
|--------|---------|
| `normalize_broker(value)` | `"ROBOFOREX"`, `"ICTRADING"`, or `"AXI"` |
| `normalize_account_type(value, broker)` | valid account for that broker |
| `account_memory_path(base_dir, account_type, broker)` | `outputs/ubs_memory_{BROKER}_{ACCOUNT}.sqlite` |
| `account_output_dir(base_dir, account_type, broker)` | `outputs/ubs_agent/{BROKER}/{ACCOUNT}/` |
| `account_seed_dir(base_dir, account_type, broker)` | `sets/ubs_ready/{BROKER}/{ACCOUNT}/` |
| `broker_asset_universe_path(base_dir, broker)` | `assets/{broker_lower}_assets.ini` |
| `account_disabled_symbols_path(base_dir, account_type, broker)` | `outputs/ubs_disabled_symbols_{BROKER}_{ACCOUNT}.json` |
| `account_timeframe_universe_path(base_dir, account_type, broker)` | `outputs/ubs_timeframes.json` |

**CLI**: `ubs_agent.py --broker ROBOFOREX|ICTRADING|AXI --account-type ...`.
When `--source-dir`, `--output-dir`, and `--memory` are not explicitly provided,
`ubs_agent.py` auto-derives them from the broker/account pair using the helpers
above.

**UI**: "UBS Agente UBS" has **Broker** and **Cuenta** comboboxes in the paths
block. Changing either triggers `_on_ubs_broker_changed()` or
`_on_ubs_account_type_changed()` in `ui/ubs_agent_logic.py`, which:
1. Calls `_sync_ubs_account_paths()` — updates seed/output/memory path vars if
   they still point to legacy account-only paths or another broker/account
   default.
2. Saves settings.
3. Calls `_refresh_all()` to reload all panels with the new account's data.

`app_ui.py` holds `self.ubs_broker` and `self.ubs_account_type`, both persisted
in `ui_settings.ini` under `[General]`.

Legacy RoboForex data is preserved by `migrate_legacy_roboforex_storage()`:
on UI startup (and in CLI tools before opening the default memory), old
account-only ECN/PRO files and folders are copied into the new
`ROBOFOREX/{ECN|PRO}` layout only when the new destination does not already
exist. Legacy ECN/PRO disabled-symbol JSON files are copied into
`outputs/ubs_disabled_symbols_ROBOFOREX_{ECN|PRO}.json`. The migration is
non-destructive; it never deletes or overwrites the legacy paths.

### Manual status override

`ubs/manual_status.py` allows operators to manually force `accepted` or `rejected`
on any UBS pipeline row without re-running MT5. `MANUAL_STATUSES = {"accepted", "rejected"}`.

| Function | Table updated | Called from |
|----------|--------------|-------------|
| `mark_seed_scores(conn, seed_paths, status)` | `seed_scores` | Seeds tab |
| `mark_candidates(conn, candidate_ids, status)` | `candidates` | Results tab |
| `mark_candidate_robustness(conn, candidate_ids, status, *, from_date, to_date, positive_bonus, negative_bonus)` | `candidate_robustness` | Robustness tab |
| `mark_candidate_final_tick(conn, candidate_ids, status, *, min_history_quality, from_date, to_date, max_*_delta_pct)` | `candidate_final_tick` | Final Tick tab |

All functions use `INSERT … ON CONFLICT DO UPDATE` for robustness/final_tick rows
(upsert preserves existing report paths, scores, metrics, and dates). For `candidates`
and `seed_scores` they use `UPDATE`. All set `accepted=1|0` to match the new status.

UI buttons: each relevant tab has **Aceptar seleccionadas** / **Rechazar seleccionadas**
(Type B bar buttons) that act on checked rows and then call
`self._safe_refresh("ubs_universe", ...)` so weights update immediately.

### `run_tests.py` — all-model watchdog and Model=4 recovery

When running `Model=4` (Every Tick based on real ticks), MT5 can get stuck
indefinitely waiting to download tick history. The stuck-detection subsystem
automatically kills and retries MT5 in this case.

**`TESTER_STUCK_MARKERS`**: tuple of journal log substrings that indicate MT5 is
stuck. Currently:
```python
TESTER_STUCK_MARKERS = (
    "preliminary downloading of history ticks started",
)
```

**Model=4 detection logic** (`wait_for_mt5_process`):
- Every 10 s the tester journal (`Tester.log` in MT5 data dir) is read.
- If the last line matches a `TESTER_STUCK_MARKERS` entry AND the file has not
  grown since the previous check → stuck counter increments.
- After 2 consecutive stuck checks (≈20 s idle), the process is killed and the
  run is retried once. On the second attempt the kick timeout is doubled before
  giving up.
- `kick_after_seconds` remains specific to `real_tick_model=True`.

**All-model watchdog**:
- Every model, including regression `Model=1`, monitors tester-journal and fresh
  report progress every 10 seconds.
- After `tester_stall_after` seconds without either signal, two consecutive
  checks are required before the MT5 process tree is killed and retried once.
  Default: `300` seconds.
- Watchdog process actions are rate-limited across multiterminal workers:
  process-tree closures reserve slots at least `0.75` seconds apart, and
  watchdog retry launches reserve slots at least `3` seconds apart. This keeps
  confirmed stalls from turning into simultaneous `taskkill`/`Popen` storms;
  it does not change retry count or terminal-profile selection.
- `tester_max_runtime` is an independent hard per-job ceiling even when the
  journal cannot be located. Default: `1800` seconds.
- A watchdog termination writes
  `reports/<report>.watchdog_attempt_N.mt5log.txt` even when MT5 never generated
  an HTML report. Missing journals are recorded explicitly in that snapshot.
- Tester-report discovery accepts only `.htm`, `.html`, and `.xml`; watchdog
  snapshots are diagnostic evidence, never parser input.
- Regression without a fresh report but with a fresh watchdog snapshot records
  neutral `watchdog_timeout` and retains the snapshot path. This state is
  intentionally excluded from pending/automatic continuation so a broken
  symbol history cannot be relaunched forever. Use a full or selected rerun
  explicitly after repairing history.
- Set either value to `0` to disable that layer. The existing completed-report
  stability guard remains active independently.

**Empty Model=4 false-success guard**:
- A tester process can exit normally while its fresh HTML contains
  `Bars=0` and `Ticks=0`; this commonly follows a connection loss during the
  first tick-history synchronization.
- `run_tests.py` treats that report as a technical failure even with exit code
  zero, retries once independently of the kick-timeout setting, and preserves
  the final empty report plus journal sidecar for diagnosis.
- If the second attempt is also empty, the runner returns exit code `4`.
  `ubs_agent.py` stores the candidate as `pending_history_quality`, never as a
  final rejection.
- Before each Model=4 attempt, the runner temporarily rotates every HCC year
  for the target symbol. Rotating only `ToDate` can be rebuilt too quickly from
  adjacent cache on some terminal profiles. The full-symbol rotation forces
  MT5's reconnect-tolerant M1 synchronization to happen before real-tick
  download. Newly generated HCC files replace the temporary copies; any year
  MT5 fails to recreate is restored.
- Final Tick disk reconciliation receives broker, symbol suffix, and W1/MN
  trade thresholds explicitly. It never relies on a global CLI `args` object,
  so both CLI and in-app refresh can parse and persist completed reports.
- In multiterminal mode, `enabled` selects the profile used with one worker.
  With more than one worker, every configured profile for the active broker is
  eligible up to the `--max-workers` concurrency limit.

**Runner/UI output backpressure**:
- `RunLogger` uses one bounded asynchronous writer queue and keeps both log
  files open for the run. Worker threads no longer perform stdout and two file
  writes while holding a shared lock. `close()` drains the queue; process exit
  registers it through `atexit` so early-return errors are flushed too.
- The desktop stdout queue is bounded to 5,000 items. Tk drains at most 200
  items or 20 ms per callback, batches adjacent console tags, and yields before
  continuing when output remains. The visible console keeps the newest 10,000
  lines.

**New CLI arguments for `run_tests.py`**:

| Flag | Config key | Default | Description |
|------|-----------|---------|-------------|
| `--model N` | — | from INI | Override `Model` in the generated INI |
| `--tester-kick-after N` | `[Multiterminal] tester_kick_after` | `30` | Seconds before killing a stuck Model=4 process |
| `--tester-stall-after N` | `[Multiterminal] tester_stall_after` | `300` | Journal/report inactivity window for every model |
| `--tester-max-runtime N` | `[Multiterminal] tester_max_runtime` | `1800` | Absolute per-backtest ceiling for every model |
| `--terminal-cooldown N` | `[Multiterminal] terminal_cooldown` | `0` | Pause in seconds after MT5 exits (prevents rapid restart) |

`load_runner_tuning(...)` reads all four tuning values from `[Multiterminal]`
in `ui_settings.ini`, overridden by explicit CLI flags.

`ubs_agent.py` forwards these flags to `run_tests.py` for Final Tick (`--model 4`)
and seed evaluation runs.

### Manual status buttons in UI tabs

All manual status changes use `ubs/manual_status.py` helpers and immediately
refresh the Universe tab (weights change when `accepted` flips):

- **UBS Seeds**: Aceptar / Rechazar selected seeds via `mark_seed_scores()`.
- **UBS Resultados**: Aceptar / Rechazar selected candidates via `mark_candidates()`.
- **UBS Robustez**: Aceptar / Rechazar selected OOS rows via `mark_candidate_robustness()`.
  Preserves existing `positive_bonus`/`negative_bonus` from the DB row.

`UBS Universo` now includes `final_tick_status` and `final_tick_similarity_json` in
its asset/timeframe weight queries so final-tick data is reflected in exploration weights.

### Fresh MT5 report guard

`run_tests.py` and `ubs_agent.py` ignore reports older than the current batch
start time. This prevents MT5 history-cache failures or stale files from being
scored as if they belonged to the current backtest.

Additionally, `delete_existing_report_files()` in `run_tests.py` pre-clears
stale `.htm`/`.html`/`.xml`/`.png`/`.set` files from the MT5 report directories
before each job. The active `.set` file (`protected_set_name`) is always preserved.
This prevents a stale report from a previous crashed run from being picked up by
the fresh-report filter if it happens to have a newer mtime than the batch start.

### MT5 no-report retry and reproducible generation

- Every tester model now retries once when MT5 exits without producing a fresh
  report. The retry uses the shared restart rate limiter, so a multiterminal
  batch does not turn a broker-wide false-success wave into simultaneous
  relaunches. Model 4 empty-history and watchdog retries keep their existing
  behaviour.
- `--random-seed` is forwarded by `manager_node_runtime/node.py` and can be set
  from the manager API/UI. A blank or `null` value preserves random execution.
- Fixed-seed generation uses versioned, generation-scoped RNG streams. Routing
  (seed/asset/timeframe selection) is isolated from mutation, and each
  `(generation, seed_index, variant_index)` mutation has its own stream. A
  mutation implementation changing its number of random draws therefore cannot
  shift the targets or adjacent variants in a paired cohort.
- Alias resolution preserves the exact broker casing from the active universe;
  canonical upper-case keys are only for identity, never for `ForceSymbol`.
- Discovery reserves at least 60% of a bounded seed cohort for sources whose
  current symbol resolves to an enabled broker target. The remaining budget is
  retained for cross-asset exploration; shortages on either side are backfilled.
- That discovery source budget is broker-adaptive once both source buckets have
  at least 20 trials. It uses the latest 10 broker-local runs, counts one trial
  per selected source (success only when any descendant reaches accepted Final
  Tick 6M), propagates later-generation outcomes through candidate
  `seed_path -> set_path` lineage to every selected source ancestor,
  ignores technical outcomes, applies a Beta(2,2) prior, and clamps the
  exploitable share to 60..85%. The full evidence and applied ratio are stored
  in `generation.seed_selection_diversity_caps.discovery_source_mix_feedback`;
  resumed runs reuse their persisted ratio.
- Discovery target routing also adapts from the latest 10 broker-local runs.
  The unseeded schedule compares its smoothed base/OOS/probe/6M lifecycle yield
  with the benchmark and can shrink to 25% of its configured budget. Universe
  feedback is bounded to 55..85%, so every exploration route keeps a non-zero
  floor. Feedback versus random exploration adapts only after both have Final
  Tick 6M evidence (at least one effective trial each and four combined);
  otherwise it keeps the 55% default rather than substituting base acceptance.
  Evidence, routing basis and applied probabilities are persisted under
  `generation.target_policy.discovery_adaptive_policy` and restored on resume.
- In Discovery, the probability of keeping the selected seed's current
  broker-resolved asset is broker-adaptive too. `exploit` candidates are
  compared with all cross-asset target policies over the same latest-10-run
  window using the smoothed end-to-end base/OOS/probe/6M lifecycle
  probability. It adapts only after both buckets have at least three grouped
  6M trials; otherwise it keeps the 70% default. The applied probability is
  bounded to 55..85%, preserving at least 15% cross-asset routing. Evidence is
  persisted under `discovery_adaptive_policy.current_target` and restored on
  resume. Production remains on its separate fixed `production_*` routing.
- Discovery applies the same lifecycle model independently to retaining versus
  changing the selected seed's timeframe. It compares the actual target period
  with `generation_seed_selection.period`, groups correlated variants by seed,
  requires at least three grouped 6M trials in both buckets, and otherwise
  keeps the 60% default. The adaptive probability is bounded to 45..80% and is
  persisted under `discovery_adaptive_policy.current_timeframe`. Production's
  `tf_production_*` routing does not consume this probability.

### Multiterminal support

- `run_tests.py` accepts `--multi-terminal`, `--terminals-config`, and
  `--max-workers`.
- `ui_settings.ini` stores `[Multiterminal]` and `[Terminal.N]` profiles.
- `[Multiterminal].broker` selects the active broker for the run, and
  `[Terminal.N].broker` scopes each terminal profile. Legacy terminal profiles
  without a broker are treated as `ROBOFOREX`.
- Compilation remains sequential; multiterminal applies to backtest queues.

### Portfolio parser English support

The portfolio parser was fixed to recognise English MT5 report labels (`Symbol`,
`Period`, `Results`, `Orders`, `Deals`, `Balance Drawdown Maximal`, …). Before
this fix, `ALL_STRATEGIES.xlsx` was generated but contained empty/zero metrics
for English-language reports. Relevant files:
[`portfolio_manager/mt5_report.py`](../portfolio_manager/mt5_report.py),
[`portfolio_manager/excel.py`](../portfolio_manager/excel.py).

### UBS Final Tick — two-stage pipeline (probe + 6M)

Final Tick is now a **two-stage pipeline**:

| Stage | CLI flag | DB table | Purpose |
|-------|----------|----------|---------|
| `probe` (default) | `--final-tick-stage probe` | `candidate_final_tick` | Short-window OHLC vs real-tick similarity filter. Same as before. |
| `six_month` | `--final-tick-stage six_month` | `candidate_final_tick_6m` | 6-month validation for live portfolio eligibility. Requires ≥ 180-day date range. |

The `six_month` stage adds an extra **PF floor check** (`min_model_profit_factor`): both OHLC and real-tick PF must meet the configured minimum profit factor. If either falls short, `reasons` gets `"profit_factor_floor"`. The PF delta tolerance is also tightened to `min(configured, 30.0)%` when running the 6M stage. This stricter check is applied only to `six_month`; the `probe` stage is unchanged.

The `--final-tick-stage` argument normalises `"6m"`, `"sixmonth"`, `"six_month"` → `"six_month"`. The stage controls which output directory (`final_tick/` vs `final_tick_6m/`) and which DB table is used.

The `UBS Final Tick 6M` tab (`ubs_final_tick_6m` key, `ui/ubs_final_tick_6m_view.py` + `ui/ubs_final_tick_6m_logic.py`) exposes:
- **Continuar 6M** (pending-only incremental run for the visible run)
- **Reprobar 6M** (replace all 6M rows for the visible run)
- **Reintentar calidad baja** (retry `pending_history_quality` rows with `--final-tick-skip-ohlc`)
- Date config block for the 6M window (defaults `2026.01.01 → 2026.06.30`)

New `ui_settings.ini` variables for 6M: `ubs_final_tick_6m_from_date`, `ubs_final_tick_6m_to_date`, `ubs_final_tick_6m_ohlc_from_date`, `ubs_final_tick_6m_ohlc_to_date`, `ubs_final_tick_6m_auto` (boolean, auto-run after probe).

The probe stage weight bonus (`DEFAULT_FINAL_TICK_ACCEPTED_BONUS = +120` / `DEFAULT_FINAL_TICK_REJECTED_PENALTY = −160`) is distinct from the 6M stage. The `FINAL_TICK_REASON_PENALTIES` now includes `"profit_factor_floor": 55.0`.

### UBS Portafolio — Final Tick 6M gate

Portfolio candidate eligibility requires: `candidates.status='accepted'` AND
`candidate_robustness.status='accepted'` AND
`candidate_final_tick_6m.status='accepted'` (6M gate). The short probe
(`candidate_final_tick`) is a prior discard filter, not a repeated portfolio
gate: only probe `accepted` or `pending_ohlc_trades` can produce a 6M result,
probe `rejected` invalidates downstream 6M evidence, and a passing 6M resolves a
short probe that lacked enough trades.

New portfolio filter: **"Requerir 3 meses positivos 6M"** checkbox (`ubs_portfolio_require_3_positive_months_6m`, persisted in `ui_settings.ini`). When enabled, the optimizer filters out candidates whose 6M curve has fewer than 3 positive months before optimization.

### UBS Buscador tab (run auditor + set search)

A new tab `buscador` ("UBS Buscador", `ui/ubs_search_view.py` + `ui/ubs_search_logic.py`) has two vertical sections:

**Auditoria de run** — per account within the active broker, per-run pipeline status summary:
- Account and run selectors are limited to the currently selected UBS broker.
- Shows counts for each pipeline stage: seeds, base candidates (generated/accepted/rejected/no_trades/etc.), robustness, Final Tick probe, Final Tick 6M, portfolio membership.
- "Non-final" counts identify how many rows are still in intermediate/pending states.
- Weight breakdown: shows per-asset and per-TF current weight contribution from all stages.

**Buscador de sets** — free-text search across active-broker `.set` files in the UBS pipeline:
- Searches seeds, candidates, robustness sets, final tick sets across the accounts belonging to the active broker only.
- Shows result: set filename, stage, status, symbol, TF, score, account.
- "Abrir set" and "Abrir reporte" actions on selected rows.
- Export found sets to a folder.

State: `self.ubs_search_query`, `self.ubs_search_status`, `self.ubs_search_paths`, `self.ubs_audit_account`, `self.ubs_audit_run_id`.

### Multiterminal — Limpiar Tester button

A **"Limpiar Tester"** danger button was added to the Multiterminal toolbar. It scans configured MT5 data directories and safely removes disposable Tester files:

- Scans `<data_dir>/Tester/` root: `.gif`, `.htm`, `.html`, `.png`, `.set`, `.xml` temp files.
- Recursively clears `Tester/cache/` and `Tester/logs/` subdirectories.
- Clears `MQL5/Profiles/Tester/` profile temp files.
- Shows a pre-deletion preview (count + size) with confirmation dialog.
- Blocks if any process is active or if MT5 terminals are still running.
- Implemented via `build_tester_cleanup_plan()` + `execute_tester_cleanup()` in `ui/multiterminal_logic.py`.

### Manager node — terminales de reparación automática

El historial que consume el manager se pagina de extremo a extremo mediante
`GET /api/v1/runs?limit=100&offset=N`. `completed_runs_snapshot` aplica
`LIMIT/OFFSET` directamente en SQLite y la respuesta incluye
`pagination.has_more` y `pagination.next_offset`; no existe un máximo global de
runs visibles.

Las ejecuciones de generación aceptan `repair_max_workers` como límite
independiente para todas las etapas de la reparación automática posterior al
run. `max_workers` sigue perteneciendo exclusivamente a la generación. Si un
cliente antiguo omite `repair_max_workers`, el nodo hereda `max_workers` para
mantener compatibilidad.

Discovery y Production permanecen separados en el nodo: una generación
Discovery termina en Final Tick 6M aunque el cliente envíe `run_regression`, y
Repair consulta el modo persistido de cada run para añadir regresión únicamente
a runs Production. Modos antiguos o desconocidos no habilitan regresión de
forma implícita; la acción manual Regression continúa siendo independiente.

### Final Tick similarity — `profit_factor_floor` check (6M only)

`final_tick_similarity()` in `ubs_agent.py` accepts an optional `min_model_profit_factor` parameter. When set (only for `six_month` stage), it adds a **symmetric floor check**: both OHLC PF and real-tick PF must be ≥ the minimum. This is separate from the delta check and fires even when the two values are close to each other but both below the threshold. The check appears as `"profit_factor_floor"` in `similarity_json.checks` and in UI `CAUSA` columns.

### UBS Portafolio Mensual

The independent `UBS Portafolio Mensual` screen uses every active-broker
broker/account candidate that passed Final Tick 6M. It intentionally ignores
quarantine and used-set locks. The selected calendar month is extracted from
every available year in the combined base + robustness trade history; all
portfolio metrics and optimization operate on that month-only curve. Saved rows use
`portfolio_scope='monthly'` plus `target_month` and do not block the regular
full-history portfolio pool.

## Python Dependencies

The runtime is standard-library-first. Third-party runtime dependencies are
`lxml` for report parsing and `openpyxl` for Excel generation. `Pillow` is
optional for anti-aliased UI widgets. `PyInstaller` is only needed for packaging.
