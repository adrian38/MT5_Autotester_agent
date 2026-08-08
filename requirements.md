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
- **FR-1.2.10** The backtest runner MUST support a per-run tester model override
  via CLI (`--model`). `--model 1` MUST generate 1 minute OHLC reports and
  `--model 4` MUST generate `Every tick based on real ticks` reports without
  editing `tester_template.ini`.
- **FR-1.2.11** Before each real `Model=4` launch, the runner MUST force a
  recoverable M1-history synchronization for the target symbol. It MUST
  temporarily rotate every HCC year for that symbol, keep each refreshed HCC
  produced by MT5, and restore any previous HCC that MT5 does not replace.
  Rotating only the `ToDate` year is insufficient because some terminal
  profiles rebuild it from adjacent cached years before the startup reconnect.
  This preflight MUST run only while that terminal profile is closed. Its
  purpose is to let MT5 survive the startup reconnect before the less resilient
  real-tick download begins.

### 1.3 Multiterminal execution

- **FR-1.3.1** When `--multi-terminal` is passed, backtest jobs MUST use terminal
  profiles for the active broker defined in `ui_settings.ini`
  `[Multiterminal]` / `[Terminal.N]` sections. With one worker, `enabled`
  selects the profile(s) eligible for that single-terminal run. With more than
  one worker, every configured profile for the active broker is eligible.
  Profiles MAY coexist for multiple brokers, but a run MUST NOT mix terminal
  profiles from different brokers.
- **FR-1.3.2** The concurrency limit MUST be
  `min(max_workers, eligible_broker_terminal_count, job_count)`. The runner MUST
  never spawn more workers than there are jobs.
- **FR-1.3.3** Each terminal profile MAY override: `enabled`, `broker`, `name`,
  `mt5_path`, `data_dir`, `experts_root`, `ubs_ex5_file`, `portable`.
- **FR-1.3.4** Compilation MUST remain sequential even in multiterminal mode.
- **FR-1.3.5** In UBS multiterminal mode, every profile eligible for the active
  worker mode MUST point `ubs_ex5_file` to a UBS / Ultimate Breakout System
  `.ex5`. Profiles that point to another EA MUST fail validation before MT5 is
  launched.
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
- **FR-1.3.8** `enabled` MUST act as the profile selector when `max_workers=1`.
  When `max_workers>1`, the runner MUST use the active broker's configured
  profiles regardless of `enabled`, with `max_workers` acting as the concurrency
  cap.

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

- **FR-1.6.0** UBS storage MUST be isolated by broker and account type. Supported
  broker/account pairs are RoboForex ECN/PRO, ICTrading STANDARD, and AXI
  STANDARD/PREMIUM. SQLite memory files, seed directories, output directories,
  and run results MUST be broker/account-scoped. Asset universes MUST be
  broker-scoped, while disabled-symbol GEN/SEEDS policies MUST be
  broker/account-scoped. Timeframe universe configuration is the only shared
  universe for now and MUST use the same file for every broker/account. All
  paths MUST be resolved through `ubs/account.py` helpers; code MUST NOT assume
  `ECN`/`PRO` are globally unique.
- **FR-1.6.0a** Existing account-only RoboForex ECN/PRO data MUST be preserved.
  On app/CLI startup, legacy files and folders (`ubs_memory_ECN.sqlite`,
  `ubs_memory_PRO.sqlite`, `sets/ubs_ready/ECN|PRO`, `outputs/ubs_agent/ECN|PRO`,
  and old disabled-symbol JSON files) MUST be copied into the new
  `ROBOFOREX/{ECN|PRO}` and account-policy layout only when the destination
  does not already exist or can be migrated safely. Migration MUST be
  non-destructive and MUST NOT overwrite new data.
  Stored seed paths MUST also be reconciled when the complete workspace moves
  to another drive or parent directory, without linking seeds across accounts.
- **FR-1.6.1** Each generation round MUST load `.set` seeds from the configured
  source directory (default `sets/ubs_ready/{BROKER}/{ACCOUNT}/`), apply any
  stored `seed_overrides`, then mutate them into variant `.set` files.
- **FR-1.6.2** Variant mutation MUST only replace keys that already exist in the
  seed; it MUST NOT add new keys.
- **FR-1.6.3** Lot sizing in every generated variant MUST be normalised via
  `force_fixed_lot_text` before use.
- **FR-1.6.4** Timeframe exploration MUST draw on SQLite feedback
  (`asset_feedback`, `timeframe_feedback`) and target the normal generation
  universe M1 / M5 / M15 / M30 / H1 / H4 / D1. W1 / MN MUST remain opt-in
  experimental targets behind `--experimental-long-timeframes`.
- **FR-1.6.4a** When W1/MN experimentation is enabled, accepted/rejected scoring
  in generation and robustness MUST use timeframe-specific minimum trade counts:
  `--min-trades-w1` for W1 and `--min-trades-mn` for MN. Other timeframes MUST
  keep using the normal `--min-trades` threshold.
- **FR-1.6.5** When `--execute-backtests` is set, the agent MUST invoke
  `run_tests.py` (or the multiterminal equivalent) for the generated variants.
- **FR-1.6.6** After backtests, every produced report MUST be scored and
  validated. A non-zero exit from `run_tests.py` MUST NOT discard reports that
  were produced; the agent MUST score whatever is available. If zero scorable
  reports are produced the agent MUST exit with an error.
- **FR-1.6.7** Continuation MUST be supported via `--continue-last-run`. The
  agent MUST pick up the pending generation count, variants-per-seed, and
  max-seeds from the last stored run. UI responsibilities MUST remain separate:
  `Completar run` handles already-generated pending backtests and remaining
  generations; `Continuar run` handles only retryable
  `report_mismatch`/`no_report` rows. Retrying problem rows MUST pass the
  original base dates stored in `runs.config_json`; it MUST NOT silently fall
  back to the current tester template dates.
- **FR-1.6.8** Before mutating a variant, the agent MUST apply any values from
  `outputs/ubs_global_params.json` for keys listed in
  `outputs/ubs_mutation_overrides.json` `frozen_override`. This injects the
  globally configured fixed value into every generated variant regardless of
  what value the seed file holds for that key.
- **FR-1.6.9** `--generation-mode` MUST support `production` and `discovery`.
  `production` MUST use existing evidence without a forced unseeded quota.
  `discovery` MUST enable the existing forced-unseeded policy. The legacy
  `--force-unseeded-universe` flag MUST remain an alias for `discovery`.
  When discovery is enabled, target selection
  MUST reserve exploration for universe assets and timeframes not represented
  by the current seed pool. The forced branch MUST prefer assets/TFs with no
  feedback yet, use an adaptive exploration quota that decreases after early
  generations, MUST remain disabled by default, and MUST continue excluding
  disabled universe symbols.
- **FR-1.6.9a** Disabled universe symbols MUST NOT be selected as generated
  candidate targets. If a disabled symbol has `SEEDS=si`, its `.set` files MAY
  still be used as mutation sources, but generated variants MUST target an
  enabled symbol.
- **FR-1.6.10** Every generated UBS variant MUST contain
  `ForceSymbol=<target_symbol>`. If the source seed lacks `ForceSymbol`, the
  agent MUST add it to the generated `.set` so tester symbol inference cannot
  fall back to inherited source-seed aliases.
- **FR-1.6.11** Generation MUST persist the selected source seeds for each
  generation, including rank, asset/timeframe/diversity components, predicted
  Final Tick 6M fitness probability, fitness weight, and fitness evidence used
  to choose them, so missing or skipped generation slots can be audited.
- **FR-1.6.12** Parameter mutation feedback MUST separate true mutated
  parameters from target-timeframe patch keys (`ST1_Timeframe`, `VolTimeframe`,
  `Entry_Timing`, `ATR_Timeframe`). Timeframe patch keys MAY be stored for
  audit, but MUST NOT pollute parameter-mutation weights.
- **FR-1.6.13** New generation runs MUST persist their launch configuration in
  `runs.config_json`. The JSON MUST include the account type, paths, generation
  mode, legacy-derived flags such as `force_unseeded_universe`, exploration probabilities, execution
  dates, score thresholds, universe counts, and the serialized CLI arguments so
  later audits can verify how the run was created without inferring from
  candidate policies.
- **FR-1.6.14** Target selection MUST apply per-generation diversity caps to
  avoid over-concentrating candidates in one target or correlated universe
  group. Universe groups SHOULD use group-specific caps based on breadth and
  correlation: Forex and Stocks at 60%, Metals at 40%, Indices/Energies at 35%,
  and Crypto at 25%, with unknown groups defaulting to 40%. A single
  symbol+timeframe pair SHOULD be capped at 30%, a single symbol SHOULD be
  capped at 45%, and a single timeframe SHOULD be capped at 60%. When a
  proposed target is capped, the agent MUST reroll and then fall back to an
  enabled universe target before allowing a diversity overflow.
- **FR-1.6.15** Generation run metadata MUST persist the effective target
  timeframe universe and whether W1/MN experimentation was enabled, so audits
  can distinguish normal runs from long-timeframe experiments.
- **FR-1.6.16** Generation run metadata MUST persist W1/MN base/robust minimum
  trades and W1/MN Final Tick minimum trades.
- **FR-1.6.17** Source seed selection and next-generation survivor selection
  MUST apply the same group/symbol/timeframe/symbol+timeframe diversity caps
  before allowing overflow, so a single profitable niche cannot monopolize all
  seeds in later generations when alternatives exist.
- **FR-1.6.18** In `discovery` mode, each generation MUST
  reserve target slots for underrepresented intraday timeframes before normal
  target creation: M1 at 2%, M5 at 2%, M15 at 3%, and M30 at 5% of planned
  generation size. It MUST also reserve at least one target slot for any allowed
  timeframe missing from the selected source seed set, subject to normal target
  diversity caps and enabled universe symbols.
- **FR-1.6.19** Report score and evolutionary selection fitness MUST remain
  separate. The score continues to classify/report base quality. A regularized
  model trained only on finalized candidates from prior runs MUST estimate
  `candidate_final_tick_6m.status='accepted'`, excluding the current run, and
  persist its probability, raw weight and evidence for prospective audit. The
  model MUST operate in `soft_weight` mode with applied weight scale `0.15`:
  source-seed ranking and next-generation survivor selection MAY use the
  scaled weight, but the raw report score MUST remain the base-quality
  classifier and the fitness contribution MUST be visible in run metadata.
- **FR-1.6.20** Mutation sampling MUST convert mutation feedback to relative
  percentile multipliers in the range `0.5..1.5`; missing feedback is neutral
  (`1.0`) and tied values receive the same multiplier. Core parameters retain
  their separate 4x base preference. Legacy timeframe patch keys MUST be
  excluded from mutation and direction feedback.

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
- **FR-1.7.3** UBS scoring MUST keep `net_profit` as the raw report result, but
  the net-profit threshold and profit score component MUST use
  `normalized_net_profit`. Normalization MUST be broker-scoped via
  `assets/<broker>_normalization.json` and the active broker's asset universe;
  missing broker config defaults to factor `1.0` instead of falling back to
  RoboForex. Metrics JSON MUST include the raw net, normalized net, factor,
  basis, and asset group so old results can be audited after rescoring.
- **FR-1.7.4** The score formula MUST be:
  ```
  score = profit_component + pf_component + recovery_component
        + trades_component + monthly_component + sqn_component
        - dd_penalty - concentration_penalty
  ```
  where each component is capped/floored as defined in `ubs_score._score_formula`.
- **FR-1.7.5** After scoring, the agent MUST validate the parsed report's
  `symbol`/`timeframe` against the candidate target (after applying `symbol_map`).
  A mismatch MUST set status `report_mismatch` regardless of score.
- **FR-1.7.6** After each MT5 batch, reports older than the batch start time MUST
  be ignored. History-cache failures or stale files MUST NOT be scored as if
  they belonged to the current backtest.
- **FR-1.7.7** The per-symbol factor MUST be measured from the symbol's real
  contract value: `reference_notional / (lot_MT5_executes × price × contract ×
  fx_rate)`. When MT5 reports no `trade_tick_value` (every GBX-quoted LSE share,
  and any pair whose conversion is not loaded at extraction time), the notional
  MUST be rebuilt as `price × contract_size × rate`, with the rate implied by the
  symbols MT5 did convert (`ubs/normalization_gen.implied_currency_rates`). Minor
  currency units (`GBX`, `GBp`) MUST be kept distinct from their parent currency:
  upper-casing `GBp` into `GBP` undervalues every LSE share by 100x.
- **FR-1.7.8** A symbol a given extraction cannot measure MUST keep the factor of
  the file being replaced (`carried_symbols`). Only symbols never measured land in
  `skipped_symbols`, and the per-group fallback they receive MUST be the group's
  **minimum** measured factor, never the median: an unmeasured symbol may be
  understated (false reject) but MUST NOT be amplified (false accept). The median
  is what turned 102 unmeasured LSE shares into a 10.0 factor, inflating their net
  profit by up to 96x.
- **FR-1.7.9** Re-applying a normalization change to stored results MUST go
  through `tools/fast_rescore_from_metrics.py`, which reads its thresholds from
  `ui_settings.ini`. `ubs_agent.py --rescore-*-only` deliberately preserves the
  stored factor (`ubs.score.rescore_result`) and MUST NOT be relied on for this.
  Stages whose verdict does not depend on the net gate (Final Tick, Final Tick 6M,
  regression) and rows in non-scored states MUST have their normalization fields
  refreshed without their status being re-judged.

### 1.8 UBS agent — candidate lifecycle

- **FR-1.8.1** Candidate statuses in SQLite `candidates` table MUST be one of:
  `generated` → `accepted` | `rejected` | `no_report` | `parse_error` |
  `report_mismatch` | `no_trades`.
- **FR-1.8.2** Universe and mutation feedback MUST estimate the smoothed
  end-to-end probability of the five-stage chain: base accepted, robustness
  accepted, probe eligible (`accepted` or `pending_ohlc_trades`), and Final Tick
  6M accepted, followed by backward regression accepted when that evidence
  exists. Each correlated candidate source group contributes at most one
  effective trial per stage. Technical/retryable states (`report_mismatch`,
  `no_report`, `parse_error`, `pending_history_quality`) MUST NOT become
  statistical failures. Stage probabilities MUST use an empirical global prior
  with shrinkage for small samples.
- **FR-1.8.3** `report_mismatch` and `no_report` rows MUST be retryable:
  - Single candidate: UI "Reprobar mismatch" → copies `.set` to
    `outputs/ubs_agent/{BROKER}/{ACCOUNT}/<run>/retry_mismatch/`, re-evaluates, updates the
    original DB row.
  - Run-level: "Reprobar run" → copies all mismatches from the run, evaluates
    all produced reports. Partial failures leave failed candidates as `no_report`.
  After a retry updates the original row to `accepted` or `rejected`, that row
  MUST enter the normal weight pool.

- **FR-1.8.4** Accepted candidates MAY be evaluated in a separate OOS robustness
  pass with `ubs_agent.py --evaluate-robustness --robust-run-id <id>`.
  Robustness MUST copy accepted candidate `.set` files into
  `outputs/ubs_agent/{BROKER}/{ACCOUNT}/<run>/robustness/...`, run `run_tests.py` on that folder,
  validate report symbol/timeframe using the same `symbol_map` rules, and store
  results in `candidate_robustness` without overwriting base `candidates.score`.
  `--robust-pending-only` MUST limit the pass to accepted candidates with no
  existing `candidate_robustness` row. Without that flag, robustness MUST rerun
  all accepted candidates and replace their stored OOS row.
- **FR-1.8.5** Robustness statuses MUST be one of: `accepted`, `rejected`,
  `no_report`, `parse_error`, `report_mismatch`, `no_trades`.
- **FR-1.8.6** The probability product MUST be converted to a bounded relative
  log-odds score centred on the global end-to-end probability. Unknown evidence
  is therefore neutral instead of automatically outranking known negative
  values. `AgentMemory` and `UBS Universo` MUST use the identical shared model.
  The UI MUST expose relative score, estimated 6M probability, confidence, and
  number of effective 6M trials separately.
- **FR-1.8.7** Robustness-accepted candidates MAY be evaluated in a Final Tick
  pass with `ubs_agent.py --evaluate-final-tick --final-tick-run-id <id>`.
  Final Tick MUST only select rows where `candidates.status='accepted'` and
  `candidate_robustness.status='accepted'`.
- **FR-1.8.8** Final Tick MUST require an explicit date range and MUST compare
  two reports for the same candidate and dates: an OHLC control report generated
  with MT5 `Model=1`, and a real-tick report generated with `Model=4`.
  An optional separate OHLC-retry date range (`--final-tick-ohlc-from-date` /
  `--final-tick-ohlc-to-date`) MAY be configured to re-run the OHLC batch when
  the primary range produces fewer trades than `--final-tick-min-ohlc-trades`.
- **FR-1.8.9** Final Tick results MUST be stored in `candidate_final_tick`
  without overwriting base candidate or robustness results. The table MUST store
  both report paths, both metrics JSON blobs, `history_quality`, date range, and
  a `similarity_json` payload explaining pass/fail causes.
- **FR-1.8.10** A Final Tick row MUST be `accepted` only if the real-tick report
  has `History Quality` greater than or equal to the configured minimum (`80` by
  default) and the active similarity checks (`profit_factor`, `drawdown_pct`,
  and trade count) remain close to the OHLC metrics within configured deltas.
  Each percentage delta MUST use the symmetric max-denominator formula
  `abs(OHLC - tick) / max(abs(OHLC), abs(tick), 1) * 100`; it is not a classic
  percentage change measured only from the OHLC control value.
  Missing `History Quality` MUST fail the row. `net_profit` MUST be stored in
  `similarity_json` for inspection only and MUST NOT block acceptance, because
  Final Tick validates operational similarity between data models rather than
  absolute profitability.
- **FR-1.8.10a** The short Final Tick probe is a discard filter before the
  six-month stage, not the final portfolio gate. A base+robust accepted strategy
  with `candidate_final_tick.status='accepted'` MAY advance to Final Tick 6M;
  `rejected` is terminal for that candidate and MUST exclude it from downstream
  6M/portfolio/export/live-use pools. `pending_ohlc_trades` MAY also advance to
  Final Tick 6M because the longer window supplies the sample that the short
  probe lacked. `pending_history_quality` and technical/error states MUST NOT
  advance until resolved. Passing or remaining sample-pending in the short probe
  never authorizes live use by itself: only Final Tick 6M `accepted` does so.
- **FR-1.8.11** Final Tick MUST support two intermediate pending states:
  `pending_history_quality` — real-tick report produced but history quality is
  below threshold or the Model=4 tick download/synchronization ended with an
  empty tester context (retryable when the connection or data improves);
  `pending_ohlc_trades` — the OHLC batch produced fewer trades than
  `--final-tick-min-ohlc-trades` (retryable via OHLC-retry date range). Rows in
  pending states MUST NOT be treated as final `accepted` or `rejected`. An empty
  Model=4 report (`Bars=0` and `Ticks=0`) whose journal ends with
  `no history data, stop testing` MUST be treated as a retryable technical
  failure even when the HTML carries an apparently valid History Quality.
  `run_tests.py` MUST retry that empty result once and return a nonzero technical
  exit code if the retry is also empty. Scores and metrics parsed from an empty
  Model=4 shell MUST NOT be stored or displayed as evaluated Tick results.
  Persistent legacy rows with the exact signature
  `rejected + real_tick_no_history + tick_download_failed=true` MUST be migrated
  idempotently to `pending_history_quality`; genuine similarity or quality
  rejections MUST remain unchanged.
- **FR-1.8.12** `--final-tick-pending-only` MUST limit the Final Tick pass to
  rows that have no stored result or are in a pending state. Without that flag,
  Final Tick MUST rerun all robust-accepted candidates and replace existing rows.
- **FR-1.8.13** Final Tick 6M accepted candidates MAY be evaluated by a separate
  backward regression pass with `--evaluate-regression`. The pass MUST use MT5
  `Model=1` (1 minute OHLC), default to `2017.01.01 -> 2019.12.31`, require at
  least 730 days, and store its result in `candidate_regression` without
  overwriting base, robustness, or Final Tick rows.
- **FR-1.8.14** A regression report MUST match the intended symbol/timeframe and
  its reported configured period MUST exactly match the requested dates. Missing
  history, missing reports, parse errors, report mismatch, or date mismatch MUST
  be retryable technical states worth zero points and zero statistical trials.
  A valid matching report with zero trades MUST be a strategy failure.
- **FR-1.8.15** Regression defaults MUST require normalized net profit `> 0`,
  PF `>= 1.10`, trades `>= 36` (`W1 >= 12`, `MN >= 4`), DD `<= 30%`, recovery
  `>= 0.75`, and positive-month ratio `>= 0.50`. Accepted rows add `+80` points.
  Rejected/no-trades rows start at `-100` and subtract per-cause penalties
  capped at an additional `-60`; technical states apply `0`.
- **FR-1.8.16** Regression evidence MUST participate in shared asset/timeframe/
  mutation feedback as a fifth probabilistic stage. Before the first regression
  trial, its prior MUST be neutral so existing probabilities do not change.
  Regression MUST remain an evidence/weight stage, not a new hard portfolio gate;
  Final Tick 6M remains the portfolio eligibility gate.
- **FR-1.8.17** UBS `.set` copies MUST use a stage-specific `UseEveryTick`
  value. Generated/base result sets, OOS robustness sets, and backward
  regression sets MUST use `UseEveryTick=false`. In short Final Tick and Final
  Tick 6M, the OHLC set copy MUST use `UseEveryTick=false` and the real-tick
  set copy MUST use `UseEveryTick=true`. Stage copies MUST NOT modify the
  candidate source set.
- **FR-1.8.18** Every MT5 model MUST have a configurable no-progress watchdog
  and an independent absolute per-job runtime ceiling. Journal or report
  progress MUST reset the inactivity window; a confirmed stall MUST terminate
  the launched process tree and retry once. A forced termination MUST preserve
  a tester-journal diagnostic even when no HTML report exists. Regression
  watchdog failures MUST be stored as the neutral technical state
  `watchdog_timeout`, with the diagnostic path retained and zero score/trial
  effect. `watchdog_timeout` MUST NOT be included in pending/automatic retries;
  it may only run again through an explicit full or selected regression rerun
  after the MT5/history problem is repaired. Report discovery MUST accept only
  `.htm`, `.html`, and `.xml`; watchdog `.mt5log.txt` snapshots MUST NEVER be
  parsed as tester reports.

### 1.9 UBS agent — seed evaluation

- **FR-1.9.1** `--evaluate-seeds` MUST run a dedicated backtest for each seed
  that is new, modified (different mtime/size), has a changed symbol/TF (via
  override), or has a retryable status (`pending`, `no_report`, `parse_error`,
  `report_mismatch`, `no_trades`). Seeds already evaluated without changes MUST
  be skipped. `invalid_seed` is a ready blocked state and MUST NOT be re-run
  automatically unless the seed file or symbol/TF override changes.
- **FR-1.9.1a** If `_manifest.csv` exists in the seed directory, it MAY provide
  metadata for listed seeds, but it MUST NOT hide additional `.set` files present
  under the source directory. Unlisted `.set` files MUST still be loaded,
  registered in `seed_scores`, and evaluated normally.
- **FR-1.9.2** A seed whose symbol or timeframe cannot be determined (both
  `UNKNOWN`) after applying `seed_overrides` MUST be marked `report_mismatch`
  before launching any backtest. No backtest job MUST be created for it.
- **FR-1.9.3** Seed statuses in `seed_scores` table MUST be one of:
  `pending` | `accepted` | `rejected` | `report_mismatch` | `no_report` |
  `parse_error` | `no_trades` | `disabled_symbol` | `invalid_seed`.
- **FR-1.9.4** `accepted`, `rejected`, and `no_trades` seeds with stored reports
  MUST contribute evidence to the base stage of the shared probability model.
  They MUST NOT invent robustness/probe/6M trials; missing later-stage evidence
  is filled only by the global shrunk prior.
  `report_mismatch` is ready for the purpose of pending counts, but it MUST NOT
  contribute to weights.
- **FR-1.9.4a** A parsed MT5 report with zero closed trades MUST be stored as
  `no_trades`, not as ordinary `rejected`. `no_trades` is retryable and MUST
  contribute only the shared fixed negative execution/reliability penalty.
- **FR-1.9.4b** A disabled universe symbol MAY be marked `SEEDS=si`. In that
  case, active seeds for that symbol MUST be evaluated, scored, included in
  Universe weights, and allowed as mutation sources. This MUST NOT re-enable
  generated candidate targets for the symbol; target generation still follows
  the normal enabled universe only. The `GEN/SEEDS` policy MUST be
  broker/account-scoped by `--broker` and `--account-type`
  (`outputs/ubs_disabled_symbols_{BROKER}_{ACCOUNT}.json`).
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
  `0`, meaning a seed passes net profit only when `normalized_net_profit > 0`.
- **FR-1.9.10** Running `--evaluate-seeds` MUST re-score already evaluated
  `accepted`/`rejected` seed rows from their stored reports using the current
  seed thresholds, without requiring another MT5 backtest when the seed file and
  symbol/TF are unchanged.
- **FR-1.9.11** `ubs_agent.py --rescore-seeds-only` MUST re-score existing
  active seed rows and MUST NOT require an MT5 expert path or launch MT5.
  `--rescore-candidates-only`, `--rescore-robustness-only`,
  `--rescore-final-tick-only`, and `--rescore-regression-only` MUST re-score
  final rows directly from their persisted `metrics_json` by default and MUST
  use one atomic batch transaction per stage. Technical validation states MUST
  remain unchanged. `--rescore-from-reports` MAY explicitly force HTML parsing
  when the report parser or broker normalization changed. These commands MUST
  be run with the correct threshold set for seeds, generation, OOS, Final Tick,
  and regression.
- **FR-1.9.12** Before launching new seed backtests, `--evaluate-seeds` MUST
  reconcile reports left by interrupted
  `outputs/ubs_agent/{BROKER}/{ACCOUNT}/seed_eval/eval_*`
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
- **FR-1.10.3** `symbol_map` configuration MUST be broker-scoped. The legacy
  `symbol_map` setting is RoboForex compatibility data; new settings use
  `symbol_map_<broker>`.
- **FR-1.10.4** Symbol normalisation MUST strip only trailing broker suffixes
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
  per broker/account in `outputs/ubs_disabled_symbols_{BROKER}_{ACCOUNT}.json`,
  remain visible as disabled in the UI,
  be excluded from generated candidate targets, and be excluded from seed
  evaluation/weights unless `SEEDS=si` is set for that symbol in the active
  broker/account.
- **FR-1.12.20** UBS seed evaluation MUST skip any seed whose inferred or
  manually overridden symbol maps to a disabled Universe symbol. Skipped seeds
  MUST be recorded as `disabled_symbol`, MUST NOT launch MT5, MUST NOT count as
  pending after a reset, and MUST NOT contribute to weights. If the disabled
  Universe symbol has `SEEDS=si`, the seed MUST run and contribute normally while
  the symbol remains blocked for generated candidate targets.
- **FR-1.12.21** The UI MUST expose robustness configuration in `UBS Agente UBS`:
  independent OOS dates, independent thresholds, positive/negative bonus values,
  and an auto-run toggle. Defaults: robust thresholds copy agent thresholds when
  no saved setting exists; positive bonus `+70`; negative bonus `-70`; dates
  empty = template dates. Bonus values are retained as legacy audit metadata;
  probability feedback uses stage outcomes rather than additive bonuses.
- **FR-1.12.21a** Robustness acceptance MUST require both the absolute OOS score
  gates and the degradation gates against the candidate's construction result.
  The default degradation limits are annualized normalized-net retention
  `>= 0.50`, profit-factor edge retention `(PF_OOS-1)/(PF_IS-1) >= 0.50`,
  duration-adjusted Recovery Factor retention `>= 0.50`, drawdown inflation
  `DD_OOS/max(DD_IS, 2%) <= 2.0`, and trade-rate retention `>= 0.50`.
  Robustness MUST also enforce temporal-generalization gates: residual OOS net
  after removing the three best positive months `>= 20%` of OOS net, positive
  active-month ratio `>= 50%`, cumulative trade-curve R-squared `>= 0.60`,
  stability retention against construction `>= 0.75`, stationary-block
  bootstrap `P(net>0) >= 95%`, and bootstrap PF fifth percentile `>= 1.05`.
  The bootstrap MUST be deterministic for identical trade histories. A zero
  threshold disables that individual gate. Missing or invalid comparison
  inputs MUST be persisted as unavailable and remain neutral rather than
  causing a rejection. The full audit MUST be stored separately in
  `candidate_robustness.degradation_json` and exposed in the robustness UI.
- **FR-1.12.22** The `UBS Resultados` tab MUST expose `Continuar a robustez`
  for the latest visible run and must confirm the number of candidates before
  launching MT5. This action MUST be incremental: it passes
  `--robust-pending-only` and only runs accepted candidates without stored OOS.
  The tab MUST also expose `Reprobar robustez`, which reruns all accepted
  candidates for the visible run.
- **FR-1.12.23** The UI MUST include a dedicated `UBS Robustez` tab showing
  accepted candidates from the visible run, SEL checkbox, OOS status, OOS
  rejection cause, OOS score, applied bonus, OOS metrics, date range, set path,
  and report path.
- **FR-1.12.24** If the robustness auto-run toggle is enabled, a successful
  normal UBS agent run with backtests MUST launch robustness automatically for
  accepted candidates. Auto-run MUST NOT trigger after seed evaluation, seed
  rescoring, retry actions, or another robustness run.
- **FR-1.12.25** The UI MUST expose a `production` / `discovery` generation-mode
  selector in `UBS Agente UBS`, persist it as `ubs_generation_mode`, and pass
  `--generation-mode` to normal and continuation runs. Loading old settings
  MUST map `ubs_force_unseeded_universe=1` to `discovery`.
- **FR-1.12.25a** The UI MUST expose an `Experimentar W1/MN` toggle in
  `UBS Agente UBS`. It MUST persist as `ubs_experimental_long_timeframes` and
  pass `--experimental-long-timeframes` to normal and continuation UBS agent
  runs only when enabled.
- **FR-1.12.25b** The `Experimentar W1/MN` UI row MUST expose four numeric
  inputs: W1 base min trades, MN base min trades, W1 Final Tick min trades, and
  MN Final Tick min trades. Defaults: W1 base `12`, MN base `4`, W1 Final Tick
  `2`, MN Final Tick `1`.
- **FR-1.12.26** `UBS Resultados` and `UBS Robustez` MUST display the latest
  visible run (`hidden=0 order by id desc limit 1`). New UBS generation runs
  MUST become visible immediately because `runs.hidden` defaults to `0`.
- **FR-1.12.27** `UBS Historico` MUST list all runs and its candidate table MUST
  include a `ROBUST` column showing robustness status/bonus (`OK +N`,
  `FAIL -N`, neutral status, or `pendiente`).
- **FR-1.12.28** `UBS Comparar` MUST list visible runs and auto-select a newly
  created latest run when it appears. If no newer run exists, it MUST preserve
  the user's manual run selection.
- **FR-1.12.29** The UI MUST include a dedicated `UBS Final Tick` tab showing
  robust-accepted candidates from the selected/latest visible run with 21 columns:
  SEL checkbox, run, candidate ID, generation, status, cause, symbol, TF, history
  quality %, OHLC score, OHLC net/PF/DD/trades, real-tick score, real-tick
  net/PF/DD/trades, date range, and set filename. Report open actions MUST include
  **Abrir set**, **Abrir OHLC**, and **Abrir Real Tick**.
- **FR-1.12.30** The `UBS Final Tick` tab MUST expose a criteria configuration
  block with editable: primary date range, OHLC-retry date range, min history
  quality, min OHLC trades, and delta tolerances (net, PF, DD, trades). All values
  MUST be persisted in `ui_settings.ini`. Defaults: primary range `2026.05.01 →
  2026.05.31`, min history quality `80`.
- **FR-1.12.30a** The `UBS Final Tick` tab MUST expose:
  - **Continuar Final Tick** (incremental, `--final-tick-pending-only`): runs only
    candidates with no stored row or in a pending state.
  - **Reprobar Final Tick**: reruns all robust-accepted candidates and replaces
    existing rows.
  - **Guardar config**: persists current criteria block values.
  - **Actualizar**: refreshes the results tree from the database.
- **FR-1.12.31** `UBS Robustez` MUST expose a continue action that sends only
  robustness-accepted candidates into Final Tick. Incremental execution MUST
  pass `--final-tick-pending-only`; rerun execution MUST replace existing Final
  Tick rows for robust-accepted candidates.
- **FR-1.12.32** `UBS Portafolio` MUST build its candidate pool from the
  accounts belonging to the active broker only. For RoboForex this means ECN
  and PRO can be combined; AXI and ICTrading MUST remain separate broker pools.
  Eligible strategies require base candidate, robustness, AND Final Tick 6M
  (`candidate_final_tick_6m.status='accepted'`) all `accepted`. The probe Final
  Tick (`candidate_final_tick`) is NOT the portfolio gate; only the 6M stage is.
  A 6M row MAY only originate from a probe `accepted` or
  `pending_ohlc_trades`; a probe `rejected` MUST invalidate/delete downstream 6M
  evidence. Therefore a short-probe failure can never reach the portfolio,
  while a short probe lacking enough trades may still be resolved by a passing
  6M comparison.
  Portfolio history MUST still be built from the base report plus the robustness
  report; Final Tick 6M is an eligibility gate, not the curve source.
  Conservative/Balanced portfolios MUST share one lock pool within the active
  broker. Aggressive portfolios MUST use a separate lock pool: only sets
  selected by another Aggressive portfolio are unavailable to a new or repaired
  Aggressive portfolio, and Aggressive portfolios MUST NOT block
  Conservative/Balanced generation.
- **FR-1.12.33** `UBS Portafolio` MUST expose a "Requerir 3 meses positivos 6M"
  checkbox (`ubs_portfolio_require_3_positive_months_6m`). When enabled, the
  optimizer MUST filter out candidates whose 6M report curve has fewer than 3
  positive months before performing lot allocation.
- **FR-1.12.34** The UI MUST include a `UBS Final Tick 6M` tab (`ubs_final_tick_6m`).
  This tab runs the `six_month` stage of Final Tick, which requires a date range
  of at least 180 days and applies an extra PF floor check in addition to all
  normal similarity checks. It targets the same `candidate_robustness.status='accepted'`
  pool as the probe stage. The tab MUST expose **Continuar 6M**, **Reprobar 6M**,
  and **Reintentar calidad baja** buttons plus a date configuration block.
- **FR-1.12.35** The UI MUST include a `UBS Buscador` tab (`buscador`) with two
  sections: (1) a run auditor showing per-stage counts and non-final pending
  counts for a selected active-broker account+run; (2) a free-text set search
  across pipeline stages and accounts belonging to the active broker only, with
  open/export actions on results.
- **FR-1.12.36** The Multiterminal tab MUST expose a **"Limpiar Tester"** danger
  button that safely removes disposable Tester cache/log/temp files from all
  configured MT5 data directories. It MUST show a preview (file count + size)
  and require confirmation before deleting. It MUST be blocked while any process
  is active or while MT5 terminals are open.
- **FR-1.12.37** `UBS Portafolio` MUST persist a cross-account set quarantine.
  Quarantined sets MUST be excluded from every future portfolio candidate pool
  until explicitly reinstated. Double-clicking a saved portfolio MUST open its
  strategy list; quarantining a member there MUST remove it from that portfolio
  and recalculate the remaining metrics. The detail window MUST offer a
  **Completar portafolio** action that preserves every remaining member, finds
  eligible replacement strategies up to the pre-quarantine strategy count, and
  preserves every existing unit/lot whenever the resulting portfolio remains
  feasible. If removing the quarantined member makes the saved allocation
  violate DD, the repair MAY reduce only the minimum existing units needed to
  restore feasibility; it MUST NOT rebuild or globally redistribute the
  portfolio. Only replacement strategies MAY receive newly optimized units.
  Drawdowns, correlations, curve, decision log, and portfolio metrics MUST be
  recalculated before replacing the saved allocations transactionally. If no
  valid replacement exists, the incomplete portfolio MUST remain unchanged.
- **FR-1.12.38** Portfolio generation MUST support a configurable DD reserve
  percentage and deterministic multi-start local search. The DD reserve reduces
  the effective valley and point-DD budgets without changing the user-entered
  nominal percentages. A saved-portfolio recomposition MUST show a set-by-set
  before/after preview and require explicit application. Immediately before
  application, the current portfolio row, allocations, members, and decision
  log MUST be stored as a compressed version snapshot. The detail window MUST
  allow restoring the latest snapshot.
- **FR-1.12.39** The saved-portfolio detail window MUST expose
  **Revalidar / optimizar** for complete portfolios. It MUST rebuild the
  candidate allocation with the saved portfolio constraints plus the currently
  selected DD reserve and multi-start count, exclude the current portfolio from
  used-set and saved-curve locks, and route the result through the same
  before/after preview, version snapshot, and explicit-apply workflow.
- **FR-1.12.40** New generation and full reoptimization MUST calculate three
  comparable proposals from the same candidate universe: `profit` (configured
  portfolio objective and reserve), `balanced` (Balanced objective with at
  least 15% DD reserve), and `margin` (Conservative objective with at least 25%
  DD reserve). The selector MUST show net, effective valley/point DD, nominal DD
  margin, reserve, units, strategy count, maximum asset-group concentration,
  and number of changed allocations. Selecting a proposal MUST update a
  set-level before/after diff. New generation continues through the normal
  pending-save workflow; reoptimization uses versioned explicit application.
- **FR-1.12.41** Every generated, reoptimized, or recalculated UBS portfolio
  MUST run a deterministic 1,000-simulation circular moving-block bootstrap on
  the combined portfolio P/L increments. The analysis MUST preserve contiguous
  loss sequences within sampled blocks and report valley-DD P50/P95 plus the
  probability of exceeding both the nominal and effective valley-DD limits.
  All three comparable proposals MUST display these values. A proposal whose
  DD P95 exceeds the effective limit MUST be shown as a red alert, but MUST NOT
  be rejected automatically. The complete method, seed, simulation count,
  observation count, block size, thresholds, percentiles, probabilities, and
  alert state MUST be persisted in the portfolio `metrics_json` for audit.
- **FR-1.12.42** The UI MUST include a separate `UBS Portafolio Mensual` screen
  (`portafolio_ubs_mensual`) with the same generation, proposal comparison,
  save, detail, completion, reoptimization, export, correlation, DD-reserve,
  local-search, and bootstrap tools as `UBS Portafolio`, plus a calendar-month
  selector. Its candidate universe MUST contain only active-broker
  broker/account strategies whose base, robustness, and Final Tick 6M stages are
  accepted. Unlike the full-history
  portfolio, monthly generation MUST NOT exclude candidates because their set
  is quarantined or already allocated to another portfolio. For the selected
  month, every strategy curve MUST be rebuilt from trades closing in that month
  across every year available in the combined base + robustness history. Trade
  count, net profit, PF, DD, correlations, ranking, allocation, and bootstrap
  MUST use that month-only curve. Saved monthly portfolios MUST persist
  `portfolio_scope='monthly'` and `target_month`; they MUST be listed separately
  and MUST NOT lock sets in the full-history `UBS Portafolio` screen.
  The screen MAY expose a strict seasonal checkbox. When enabled, every
  generated proposal MUST also pass the selected month year-by-year over the
  latest five years where that month exists in the historical data: yearly net
  MUST be positive and yearly valley/point DD MUST remain inside the same
  effective DD limits. The selected month MUST also be the highest-net calendar
  month for that fixed portfolio allocation over the same five-year window.
  Proposals that fail this strict seasonal validation MUST be rejected before
  the proposal preview/save step, and the audit details MUST be persisted in
  `metrics_json`.
- **FR-1.12.43** `UBS Portafolio` and `UBS Portafolio Mensual` MAY expose a
  `Grid OFF` checkbox. When enabled, portfolio generation, reoptimization, and
  completion MUST exclude candidate rows whose source `.set` file explicitly
  contains `EnableGrid=true` as the current value. Missing/unreadable
  `EnableGrid` keys MUST NOT be treated as grid-enabled. The selected setting
  MUST be persisted in portfolio inputs and UI settings.
- **FR-1.12.44** `UBS Portafolio` and `UBS Portafolio Mensual` MUST expose the
  complete broker-universe asset groups as individually persisted filters.
  For IC Trading these are Forex, Metals, Indices, Energies, Crypto, Stocks,
  Bonds, and Softs. Indices and Energies MUST remain separate. Portfolio symbol
  classification MUST use the maintained broker universe files, including
  exchange-suffixed stocks and aliases, instead of relying only on a small
  hard-coded symbol list. The controls MUST be arranged as a compact grid.
- **FR-1.12.45** Full-history `UBS Portafolio` MUST expose a persisted
  **Excluir usados** checkbox, enabled by default for backward compatibility.
  When enabled, sets allocated to other saved full-history portfolios remain
  ineligible. When disabled, those sets MAY be reused if they pass every
  current DD, correlation, margin, group, and pipeline gate. The setting MUST
  apply consistently to generation, availability counts, reoptimization, and
  completion; quarantine remains a hard exclusion regardless of this option.
- **FR-1.12.46** The UI MUST include a `UBS Regresiva` tab (`ubs_regression`)
  with its own date/threshold/point configuration, `Continuar regresiva`,
  `Reprobar`, `Aplicar criterios`, manual OK/FAIL, report actions, and an
  optional automatic handoff after Final Tick 6M. It MUST distinguish strategy
  failures from neutral technical/retryable states.
- **FR-1.12.47** The manager node MUST expose historical cleanup when
  `cleanOldTest.ps1` and `cleanOlddata.ps1` are available. It MUST allow the
  manager card to enqueue a manual cleanup and MUST run the same two scripts,
  followed by leftover verification, after every completed generation cycle
  and after each run selected through the manual Repair or Regression actions.
  Cleanup failure MUST fail the job and prevent the next run or cycle.
- **FR-1.12.48** Automatic repair after a generation run MUST accept an
  independent `repair_max_workers` limit for all of its repair stages. It MUST
  not consume the generation `max_workers` value when the independent value is
  supplied; clients that omit it remain backward compatible by inheriting
  `max_workers`.

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

### 1.15 AI agent tooling — `codebase-memory-mcp`

- **FR-1.15.1** Code discovery by AI agents MUST use the DeusData
  **`codebase-memory-mcp`** server (project key
  `F-TRADING-MT5_Autotester_agent_AXI`, root `F:/TRADING/MT5_Autotester_agent_AXI`)
  as the primary tool, falling back to text search (grep/glob) only for
  non-indexed material (`.set`, `.ini`, HTML reports, generated outputs) or when
  the index is stale.
- **FR-1.15.2** The server MUST be declared in the project `.mcp.json` under the
  name `codebase-memory` (stdio transport, no args). Because the entry points to
  a machine-specific binary
  (`C:\Users\13199\.claude\tools\codebase-memory-mcp\codebase-memory-mcp.exe`),
  `.mcp.json` stays in `.gitignore` and MUST be recreated on each machine.
- **FR-1.15.3** The MCP server is a **development-time dependency only**. Runtime
  code (`app_ui.py`, `ubs_agent.py`, `run_tests.py`, …) MUST NOT import, launch,
  or depend on it, and it MUST NOT be added to packaging/installer inputs.
- **FR-1.15.4** The graph index MUST be kept fresh: `index_status` /
  `detect_changes` after substantial refactors, `index_repository` to re-index.
  The index is git/branch-scoped, so branch switches (e.g. `AXI` ↔ `IC` ↔ `main`)
  can require re-indexing.
- **FR-1.15.5** Agent-facing entry documents ([CLAUDE.md](CLAUDE.md),
  [AGENTS.md](AGENTS.md)) MUST document this workflow, including the preferred
  tools (`search_graph`, `search_code`, `trace_path`, `get_code_snippet`,
  `query_graph`, `get_architecture`) and the gitignored `.mcp.json` caveat.

### 1.16 Interface with `MT5_Autotester_agent_manager`

The manager (`I:\TRADING\MT5_Autotester_agent_manager`, node `axi` in its
`manager.json`) reads this project directory. The dependency is one-way: this
repository MUST NOT read anything from the manager.

- **FR-1.16.1** These are the only files the manager consumes, and their
  ownership MUST stay explicit:

  | File | Produced by | Consumed by |
  |------|-------------|-------------|
  | `assets/<broker>_normalization.json` | this repo (`tools/gen_axi_normalization.py`) | both — the agent as a scoring factor, the manager inverted into notional per minimum position |
  | `assets/<broker>_symbol_specs.json` | this repo (`tools/gen_axi_normalization.py --dump-specs`) | manager only (`margin_min_lot`, `volume_min`, `contract_size`, `account_leverage`) |
  | `assets/<broker>_max_product_leverage.json` | broker schedule + terminal measurement | manager only (product leverage caps) |
  | `assets/<broker>_assets.ini` | this repo | both |
  | `outputs/ubs_memory_<BROKER>_<ACCOUNT>.sqlite` | this repo | both (manager read-only) |

- **FR-1.16.2** Derived money fields in the spec dump (`notional_min_lot`,
  `observed_leverage`) MUST be expressed in the account currency. Multiplying the
  quoted price without converting makes a pence-quoted share read 74x its real
  exposure and an AED share report 1:73.9 where the cap is 1:20.
  `--dump-specs` writes them converted; `tools/fix_broker_specs_currency.py`
  repairs a dump produced elsewhere. Both are idempotent and only touch
  `origin: terminal*` leverage entries, never the ones taken from the broker's
  published schedule. A measured leverage MUST NOT exceed the account leverage of
  the snapshot: anything above it is timing noise between the price and margin
  reads, and a cap that is too high asks for less margin than the broker will.
- **FR-1.16.3** The `skipped_symbols` list is the contract for "the agent could
  not measure this". Consumers MUST NOT infer a notional, a margin or a leverage
  for those symbols from group aggregates.
- **FR-1.16.4** Writing any of these files MUST preserve what the current run
  could not measure: per-symbol factors, per-symbol margins, and whole symbols the
  terminal failed to resolve are carried from the previous file and reported as
  `carried_symbols`. A snapshot taken while a quote or a name was unavailable MUST
  NOT delete a measurement.
- **FR-1.16.5** Regenerating any of these files MUST be followed by
  `tools/fast_rescore_from_metrics.py` (see FR-1.7.9) so stored results and the
  active factors never disagree.

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

- **2026-07** - Added the independent backward regression stage after Final
  Tick 6M: exact-date 2017-2019 Model=1 validation, `candidate_regression`
  persistence, neutral technical states, configurable points, fifth-stage
  probability feedback, dedicated UI tab, audit/search integration, and
  optional automatic handoff.

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

- **2026-06** - Broker-scoped net-profit normalization: UBS scoring keeps raw
  report `net_profit` but uses `normalized_net_profit` for pass/fail and the
  profit score component. Factors are configured in
  `assets/<broker>_normalization.json`; RoboForex keeps its existing factors
  while brokers without a file use neutral factor `1.0`.

- **2026-06** — Fixed UBS generated symbol safety: generated variants now add
  `ForceSymbol` when missing, and `run_tests.py` recognizes `.JP225Cash` /
  `JP225Cash` before broad aliases like `GOLD -> XAUUSD`.

- **2026-06** — UBS Agent exploration: added `--force-unseeded-universe` and
  the `Poblar universo sin seed` UI toggle. When enabled, generation reserves
  part of asset/TF target selection for universe items not represented in the
  current seed pool, preferring items with no feedback yet.

- **2026-06** — Generation learning v2: report score and evolutionary fitness
  are separated. A regularized prior-run model predicts Final Tick 6M fitness;
  asset/TF/mutation feedback uses smoothed stage probabilities and relative
  log-odds; mutation sampling uses percentile multipliers without negative
  saturation; Universe displays probability/confidence/effective 6M trials;
  and generation exposes explicit `production` / `discovery` modes while
  retaining the old force-unseeded flag as a compatibility alias.

- **2026-06** — UBS Final Tick implemented end-to-end: `candidate_final_tick`
  DB table, `ubs_agent.py --evaluate-final-tick` with flags
  `--final-tick-pending-only`, `--final-tick-min-history-quality`,
  `--final-tick-min-ohlc-trades`, OHLC-retry date range, and four delta-tolerance
  flags. Pending states `pending_history_quality` and `pending_ohlc_trades` added.
  `UBS Final Tick` tab with 21-column tree, criteria configuration block,
  **Continuar Final Tick**, **Reprobar Final Tick**, **Guardar config**, and
  **Actualizar** buttons, plus per-row "Abrir set / OHLC / Real Tick" actions.
  Ten new `ubs_final_tick_*` variables persisted in `ui_settings.ini`.
  `_refresh_all()` now also refreshes the Final Tick panel.

- **2026-06** — Final Tick extended to a two-stage pipeline. `--final-tick-stage`
  accepts `probe` (existing short-window filter, `candidate_final_tick`) and
  `six_month` (new 6M validation, `candidate_final_tick_6m`). The 6M stage requires
  ≥ 180-day date range and adds a PF floor check (`profit_factor_floor` in
  `similarity_json`). `FINAL_TICK_REASON_PENALTIES` gained `"profit_factor_floor": 55.0`.
  New `UBS Final Tick 6M` tab added. Portfolio gate changed from probe to 6M stage.

- **2026-07** — Clarified the two-stage Final Tick lifecycle: the short probe is
  a discard filter, not the final live-use gate. Probe `rejected` is terminal;
  probe `accepted` and `pending_ohlc_trades` may advance to 6M; only Final Tick
  6M `accepted` authorizes portfolio/export eligibility.

- **2026-06** — `UBS Buscador` tab added: run auditor (per-account per-run pipeline
  status and weight breakdown) plus free-text set search across pipeline stages
  and accounts within the active broker. `ui/ubs_search_view.py` + `ui/ubs_search_logic.py`.

- **2026-06** — Multiterminal `Limpiar Tester` button: pre-deletion scan +
  confirmation + safe deletion of `Tester/` temp files, `Tester/cache/`,
  `Tester/logs/`, and `MQL5/Profiles/Tester/` across all configured data dirs.
  `build_tester_cleanup_plan()` + `execute_tester_cleanup()` in `ui/multiterminal_logic.py`.

- **2026-06** — `UBS Portafolio` portfolio gate updated to `candidate_final_tick_6m`
  (6M stage), not probe. New optional filter: "Requerir 3 meses positivos 6M"
  via `filter_rows_by_recent_positive_months()` in `portfolio_manager/ubs_portfolio.py`.

- **2026-07** — Model=4 startup reconnect hardening: before launching a real-tick
  test, `run_tests.py` temporarily rotates every HCC year for the selected
  symbol. This forces reconnect-tolerant M1 synchronization to span AXI's
  predictable startup reconnect before the fragile real-tick download begins.
  Final Tick disk reconciliation must receive broker, symbol suffix, and W1/MN
  thresholds explicitly; reconciliation must not depend on a global CLI `args`
  object.
