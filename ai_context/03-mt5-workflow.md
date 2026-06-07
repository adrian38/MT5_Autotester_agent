# MT5 Compile & Backtest Workflow

## Path Resolution

MT5 path lookup order in `run_tests.py`:

1. CLI `--mt5-path`.
2. Environment variables / `.env` via `mt5_env.py`:
   - `MT5_TERMINAL_PATH`
   - `MT5_PATH`
3. Known default install locations:
   - `C:\Program Files\RoboForex MT5 Terminal\terminal64.exe`
   - `C:\Program Files\MetaTrader 5\terminal64.exe`
   - `C:\Program Files (x86)\MetaTrader 5\terminal64.exe`
4. `shutil.which("terminal64.exe")`.
5. First default path as fallback.

MetaEditor lookup in `compile_mq5.py` follows a similar pattern:

1. CLI `--metaeditor-path`.
2. CLI `--mt5-path` with executable name changed to `MetaEditor64.exe`.
3. `MT5_METAEDITOR_PATH` / `METAEDITOR_PATH`.
4. `MT5_TERMINAL_PATH` / `MT5_PATH` with executable name changed.
5. Known default install locations.
6. `shutil.which("MetaEditor64.exe")`.

## Compile Flow

`compile_mq5.py`:

1. Determine source directory from CLI `--source-dir` or `compile_root.txt`.
2. If `--source-file` is provided, compile only that `.mq5`.
3. Otherwise compile root-level `*.mq5`. The current implementation accepts a
   `--recursive` flag but source discovery uses `glob("*.mq5")`; verify before
   relying on nested traversal.
4. Build a MetaEditor command:
   - `/compile:<source>`
   - `/log:<logs>/<stem>_compile.log`
   - `/inc:<MQL5 root>` when the source path is inside an `MQL5` tree.
5. Check that the `.ex5` exists and was updated.

## Backtest Flow

`run_tests.py`:

1. Resolve MT5 terminal and terminal data directory.
2. Detect matching running `terminal64.exe` processes unless
   `--skip-running-check` is used.
3. Resolve experts:
   - specific `--expert`;
   - `.ex5` files in `--experts-dir`;
   - first active line of `experts_root.txt`;
   - entries in `experts_list.txt`.
4. Resolve `.set` files from `--set-dir` and/or repeated `--set-file`.
5. Load `tester_template.ini`.
6. For each expert or expert/set combination:
   - copy `.set` files into MT5 tester profile folders;
   - optionally infer `Symbol` and `Period` from the `.set`;
   - apply symbol suffix and symbol map;
   - create a generated `.ini` under `configs/`;
   - delete previous report artifacts with the same report name before real
     execution;
   - launch `terminal64.exe /config:<ini>`.
7. Locate reports in MT5 data/install report locations.
8. Copy report files into `reports/`.

`terminal64.exe /config:<ini>` is treated as a single-job launch contract. The
runner opens MT5 for one generated `.ini`, waits for that MT5 process to exit,
then reads fresh reports and moves to the next job. This open/close cycle is
intentional: an already-open MT5 instance can silently ignore a new `/config`
request or keep using its existing tester state. Do not change this into “keep
MT5 open and send the next config” unless replacing the runner with a proven
MT5-side queue/control mechanism.

## Multiterminal Backtests

`run_tests.py` can distribute a backtest queue across multiple manually
configured MT5 terminals:

- CLI flags: `--multi-terminal`, `--terminals-config <path>`,
  `--max-workers N`.
- UI settings live in `ui_settings.ini` under `[Multiterminal]` and
  `[Terminal.N]`.
- Each terminal profile can specify `enabled`, `name`, `mt5_path`, `data_dir`,
  `experts_root`, `ubs_ex5_file`, and `portable`.
- In UBS mode, enabled profiles must point `ubs_ex5_file` to a UBS / Ultimate
  Breakout System `.ex5`. A profile configured with another EA must fail before
  MT5 launches, because the report can otherwise look valid while scoring the
  wrong expert.
- The worker count is a limit, not a required exact count: use up to `N`
  enabled terminals and never more workers than jobs.
- Compilation remains sequential. Multiterminal mode applies to backtest
  queues, including UBS Tester and UBS Agent backtest execution.
- Multiterminal still follows the same one-job `/config` contract per profile:
  a worker opens its terminal for the assigned job, waits for exit, collects the
  report, then takes another job. It should not keep that terminal open and
  inject multiple configs into the same running instance.
- Running-terminal checks must block only terminal instances that are already
  open before the batch starts, or that were left behind by a previous failed
  job. They must not treat terminals opened by the current active batch as a
  reason to stop that same batch.

## Template Contract

`tester_template.ini` must contain a `[Tester]` section. The script overwrites
or fills at least:

- `Expert`
- `Report`
- `Symbol` when suffix/map/set inference applies
- `Period` when set inference applies

Common template fields:

```ini
[Tester]
Expert=
Symbol=XAUUSD
Period=M30
Model=1
FromDate=2020.01.01
ToDate=2026.05.22
Deposit=1000
Currency=EUR
Leverage=1:500
Optimization=0
Visual=0
ReplaceReport=1
ShutdownTerminal=1
Report=
```

## Symbol / Timeframe Inference

`run_tests.py` contains heuristics for:

- Forex symbols.
- XAUUSD/GOLD names.
- BTCUSD.
- US100/US30/US500/DAX indices.
- XTIUSD/WTI/crude oil.
- Timeframes such as M1, M5, M15, M30, H1, H4, D1, W1.

Broker symbol rewriting can be configured through a `source=target` symbol map
from the UI or `--symbol-map`.

Exact path tokens are intentionally preferred over loose aliases. Example:
`XAGUSD__D1__XAUUSD_MIX__...set` should infer `XAGUSD`, even though the name
also contains `XAUUSD_MIX`.

Broker/index symbols can appear with broker-specific casing or suffixes in
generated paths. `run_tests.py` must recognize exact `*Cash` tokens such as
`.JP225Cash` / `JP225Cash` before broad aliases like `GOLD -> XAUUSD`; otherwise
a generated `JP225Cash/H4/...GOLD...set` can incorrectly run on `XAUUSD`.

When UBS generated variants are executed, `ubs_agent.py` calls `run_tests.py`
with `--prefer-set-path-timeframe`. This makes the tester `Period` come from
the generated target folder/name instead of a timeframe still present in the
source seed label or internal set parameters. `ForceSymbol` values read from a
`.set` are preserved literally for the generated tester `Symbol`, so broker
symbols such as `.JP225Cash` keep their exact casing.

`ubs_agent.py:create_variant()` must ensure generated variants contain
`ForceSymbol=<target_symbol>`. If the source seed lacks `ForceSymbol`, the agent
adds it via `replace_or_add_plain_key()`; this makes future tester inference
depend on the intended target, not on inherited seed names.

## UBS Agent Report Validation

The UBS agent does not trust filenames as proof that MT5 executed the intended
asset. After each report is parsed, `ubs_agent.py` compares:

- candidate target symbol after applying `symbol_map`;
- candidate target timeframe;
- parsed report symbol;
- parsed report timeframe.

If these do not match, the candidate is stored as `report_mismatch`, not
`accepted` or `rejected`. Mismatches are excluded from agent feedback and from
the Universe tab weights. The Results tab has `Reprobar mismatch` for one
candidate and `Reprobar run` for all mismatches in the visible run.

After a retry fixes a mismatch/no-report candidate and the original row becomes
`accepted` or `rejected`, it enters the normal weight pool. `rejected` rows now
use the shared `ubs.weights` formula: raw score minus the base rejection penalty
and per-cause penalties from `metrics_json.reasons`.

## UBS Seed Evaluation

Original UBS seeds can be scored with `ubs_agent.py --evaluate-seeds`.
The UI exposes this from `UBS Agente UBS` and the dedicated `UBS Seeds` tab.

If `_manifest.csv` exists in the seed directory, it is metadata, not an
exclusive allow-list. `load_seeds()` must load manifest rows and then include
any additional `.set` files present under the source directory. Otherwise the UI
can show files as pending while the agent never evaluates them.

Seed results are stored in `outputs/ubs_memory.sqlite`:

- `seed_scores`: one row per source seed, including score, accepted flag,
  status, report path, active/inactive state, symbol, and timeframe.
- `seed_overrides`: manual symbol/timeframe corrections keyed by seed path.

Hard rule: if a UBS seed cannot infer both symbol and timeframe after applying
`seed_overrides`, it must be marked `report_mismatch` before MT5 is launched.
Do not copy it into the seed evaluation folder and do not run a backtest for
it. The user must correct it in `UBS Seeds` before it can be evaluated.

Accepted/rejected/no-trades seed scores with stored reports feed Universe
asset/timeframe weights at full base strength, the same as generated candidates.
Seeds do not receive robustness bonus unless a separate seed-date/robustness
bonus is explicitly added.
`report_mismatch`, `no_report`, and `parse_error` seed rows must not feed
weights.
For pending/backtest counts, `report_mismatch` is considered ready/quarantined:
do not re-run it unless the source seed changes or the user saves a different
symbol/timeframe override. Retryable states are `pending`, `no_report`,
`parse_error`, and `no_trades`.

If a seed report parses successfully but has zero closed trades, classify it as
`no_trades` instead of ordinary `rejected`. This usually means an MT5/history or
session-filter execution problem, so it is retryable and feeds only the shared
fixed negative reliability penalty. The Seeds tab can relaunch a single selected seed through
`ubs_agent.py --retry-seed-path`.

Seed acceptance thresholds in the UI are independent from UBS Agent generation
thresholds. The default seed net-profit threshold is `0`, which means strict
`normalized_net_profit > 0` because the scorer rejects
`normalized_net_profit <= min_net_profit`. The raw report `net_profit` remains
stored for audit/display; current RoboForex scoring factors are configured in
`assets/roboforex_normalization.json`.
When `--evaluate-seeds` runs, already evaluated `accepted`/`rejected` seeds are
re-scored from their stored reports using the current seed thresholds, without
rerunning MT5 if the seed file and symbol/timeframe are unchanged.
Use `ubs_agent.py --rescore-seeds-only`, `--rescore-candidates-only`, and
`--rescore-robustness-only` when thresholds or normalization changed and MT5
should not be launched.

Seed evaluation is resumable after an interrupted MT5 batch. Before launching
new backtests, `--evaluate-seeds` scans `outputs/ubs_agent/seed_eval/eval_*`,
matches copied `.set` files back to source seeds by file content, validates the
fresh report symbol/timeframe, and updates `seed_scores`. Use
`ubs_agent.py --evaluate-seeds --reconcile-seed-eval-only` to do only this
SQLite/report reconciliation without opening MT5.

The UI can reset seed evaluation from `UBS Seeds`:

- "Resetear evaluación" resets active `seed_scores` rows to `pending`, clears
  score/report fields, deletes stored report files when possible, and does not
  delete source `.set` files.
- After reset, Universe weights are locked/hidden until seed evaluation is run
  again and the user presses "Calcular pesos" in `UBS Universo`.
- "Calcular pesos" only unlocks weights when active seeds are ready. Current UI
  ready states are `accepted`, `rejected`, `no_trades`, `report_mismatch`, and
  `disabled_symbol`.

Seeds and Universe tables use a SEL checkbox column for multi-row operations.
Seed actions use checked rows when any exist, otherwise the selected row.
Universe symbols can be disabled/enabled from checked rows; the disabled set is
stored in `outputs/ubs_disabled_symbols.json`. Disabled symbols remain visible
in the Universe table, but are excluded from displayed weights and from UBS
agent target-symbol exploration. During seed evaluation, a seed whose inferred
or overridden symbol maps to a disabled symbol is recorded as `disabled_symbol`
and is not sent to MT5, including after a seed reset.

For single-candidate retry, `ubs_agent.py --retry-candidate-id <id>` copies the
candidate `.set` into `outputs/ubs_agent/<run>/retry_mismatch/...`, runs
`run_tests.py` only on that retry folder, and then re-evaluates the original
candidate row.

For run-level retry, use `ubs_agent.py --retry-run-id <run> --retry-mismatch-run`.
It copies all current `report_mismatch` candidates from that run into one retry
folder, runs one batch, and updates each original SQLite row. If one MT5
backtest in that batch fails, the retry should still evaluate every report that
was produced; only candidates without a generated report should remain
`no_report`.

Normal UBS generations follow the same partial-failure rule. A non-zero
`run_tests.py` exit does not discard reports already produced, but the agent
must stop if the batch produced no puntuable reports.

## UBS Robustness OOS

Accepted UBS candidates can be sent to an out-of-sample robustness pass with
`ubs_agent.py --evaluate-robustness --robust-run-id <id>`. The UI exposes this
as:

- `UBS Agente UBS` -> `Robustez OOS` configuration block.
- `UBS Resultados` -> `Continuar a robustez` for only accepted candidates
  without stored OOS, plus `Reprobar robustez` to rerun all accepted candidates.
- `UBS Robustez` -> OOS result table plus the same continue/rerun actions.

The full robustness run copies every accepted candidate `.set` from the
selected run into `outputs/ubs_agent/<run>/robustness/run_<id>_<timestamp>/`,
then calls `run_tests.py` on that folder. `--robust-pending-only` filters that
queue to accepted candidates with no existing `candidate_robustness` row. The
UI's "Continuar" action uses that flag; "Reprobar" intentionally omits it.
`--from-date` / `--to-date` are robustness-only dates when provided; empty
values use the tester template.

Robustness has its own score thresholds and its own positive/negative weight
bonus. The default bonus scale is `+70/-70`. The base candidate score in
`candidates` remains unchanged. OOS results are stored in
`candidate_robustness`:

- `accepted`: add `positive_bonus` to asset/timeframe/mutation feedback.
- `rejected`: add `negative_bonus` plus per-cause OOS penalties.
- `no_report`, `parse_error`, `report_mismatch`, `no_trades`: store the state
  but apply no robustness bonus.

The agent and `UBS Universo` must use `ubs.weights` for the same formula:
accepted rows get the accepted bonus, rejected rows get rejection/cause
penalties and are capped so they never add positive weight, no-trades rows get
a fixed reliability penalty, robust results apply
their OOS adjustment, correlated groups are averaged before aggregation, and
small samples are shrunk toward zero. Seeds do not have a robustness bonus by
default, but seeds with scored reports still contribute even when candidate
evidence exists, at the same base strength as generated candidates.

## UBS Unseeded Universe Exploration

Normal target selection is intentionally biased toward the current seed symbol
and toward assets/timeframes with positive feedback. To force coverage of
assets or timeframes with no seed representation, enable `Poblar universo sin
seed` in `UBS Agente UBS` or pass `ubs_agent.py --force-unseeded-universe`.

When enabled:

- `choose_target_symbol()` computes universe symbols not represented by the
  current seed pool and gets a 65% early chance to choose one of them before
  ordinary exploit/feedback logic.
- `choose_target_period()` computes timeframes not represented by the current
  seed pool and gets a 50% early chance to choose one if it is related to the
  current seed timeframe.
- The forced branch prefers items with no feedback yet. Items with negative
  feedback can still be explored if no unseen item remains.
- Disabled symbols are never selected by this branch.

## MT5 Gotchas

- MT5 can ignore `/config` when the same terminal is already open. Preserve the
  running-terminal detection unless intentionally changing this behavior.
- Reports may be written to the terminal data folder or install directory;
  `run_tests.py` searches multiple locations.
- Stale report files can survive in several MT5 locations. `run_tests.py`
  deletes matching report artifacts just before real execution.
- MT5 history-cache failures can leave no fresh report or leave stale artifacts.
  `run_tests.py` and `ubs_agent.py` must ignore reports older than the current
  batch start time.
- MT5 report files may be UTF-16 HTML. Do not parse them as plain UTF-8 text
  without checking encoding.
- Broker symbols may start with a dot, for example `.US30Cash`; this leading
  dot is part of the symbol and must not be stripped by report parsing.
- Broker symbols may contain internal dots, for example `BRK.B`. Report/config
  names derived from `.set` stems must preserve those dots and the rest of the
  stem; do not run `Path(stem).stem` on an already-extensionless stem, because
  it turns `BRK.B_...` into `BRK` and causes report collisions.
