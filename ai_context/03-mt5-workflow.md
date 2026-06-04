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

Accepted/rejected seed scores can feed Universe asset/timeframe weights.
`report_mismatch`, `no_report`, and `parse_error` seed rows must not feed
weights.
For pending/backtest counts, `report_mismatch` is considered ready/quarantined:
do not re-run it unless the source seed changes or the user saves a different
symbol/timeframe override. Retryable states are `pending`, `no_report`,
`parse_error`, and `no_trades`.

If a seed report parses successfully but has zero closed trades, classify it as
`no_trades` instead of ordinary `rejected`. This usually means an MT5/history or
session-filter execution problem, so it is retryable and does not feed Universe
weights. The Seeds tab can relaunch a single selected seed through
`ubs_agent.py --retry-seed-path`.

Seed acceptance thresholds in the UI are independent from UBS Agent generation
thresholds. The default seed net-profit threshold is `0`, which means strict
`net_profit > 0` because the scorer rejects `net_profit <= min_net_profit`.
When `--evaluate-seeds` runs, already evaluated `accepted`/`rejected` seeds are
re-scored from their stored reports using the current seed thresholds, without
rerunning MT5 if the seed file and symbol/timeframe are unchanged.
Use `ubs_agent.py --rescore-seeds-only` when only thresholds changed and MT5
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
  ready states are `accepted`, `rejected`, and `report_mismatch`.

Seeds and Universe tables use a SEL checkbox column for multi-row operations.
Seed actions use checked rows when any exist, otherwise the selected row.
Universe symbols can be disabled/enabled from checked rows; the disabled set is
stored in `outputs/ubs_disabled_symbols.json`. Disabled symbols remain visible
in the Universe table, but are excluded from displayed weights and from UBS
agent target-symbol exploration.

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
