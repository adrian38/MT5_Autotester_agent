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
   comparison, and stores all results in SQLite.

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
- UBS parameter mutability is defined by `is_agent_mutable_key()` in
  `ubs_agent.py` — NOT by the Y/N flag in `.set` files (that is the MT5
  optimizer flag, a different thing) and NOT by `is_mutable_key()` in
  `ubs_generate_sets.py` (which has different constants). Always use
  `is_agent_mutable_key()` when reasoning about what the agent will mutate.
- `app_ui.py` is the composition/layout root. Screen mixins live in `ui/`
  (`ui/dashboard_view.py`, `ui/dashboard_logic.py`, etc.) and UBS support
  modules in `ubs/` (`ubs/memory.py`, `ubs/score.py`, etc.). Do NOT grow
  `app_ui.py` or `ubs_agent.py` with logic that belongs in a domain module.
- For every substantial UI screen/tab use a view/logic pair inside `ui/`:
  `ui/<screen>_view.py` for widgets/layout and `ui/<screen>_logic.py` for
  behavior/state/persistence. This is the mandatory structure for all tabs.
- **UI design rules are in `09-design-system.md`. Read it before touching any
  widget.** Three button types, action-bar pattern, Treeview standard, and
  input sizes are all defined there. Using a wrong button type or omitting
  `stretch=False` on a Treeview column is a bug.

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
| `ubs_historico` | UBS Historico | (part of ubs_results) | (part of ubs_results) |
| `ubs_universo` | UBS Universo | `ui/ubs_universe_view.py` | `ui/ubs_universe_logic.py` |
| `ubs_comparar` | UBS Comparar | (part of ubs_results) | (part of ubs_results) |
| `ubs_params` | UBS Parámetros | `ui/ubs_params_view.py` | `ui/ubs_params_logic.py` |
| `portafolio_ubs` | UBS Portafolio | `ui/ubs_portfolio_view.py` | `ui/ubs_portfolio_logic.py` |

## Recent Important Changes

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
    separate scoring thresholds, positive/negative bonus values, and an
    auto-run toggle.
  - `UBS Resultados` has **Continuar a robustez** for the latest visible run.
    This is incremental and passes `--robust-pending-only`, so it only sends
    accepted candidates without OOS already stored. **Reprobar robustez**
    reruns all accepted candidates and overwrites their OOS row.
  - `UBS Robustez` shows accepted candidates from the visible run plus their
    OOS status, cause, score, bonus, report, and OOS metrics. Its table has a
    `SEL` checkbox column and a `CAUSA` column derived from OOS
    `metrics_json.reasons`.
- SQLite: results are stored in `candidate_robustness`, separate from base
  `candidates` scores.
- Weight rule lives in `ubs/weights.py` and must be shared by
  `AgentMemory.asset_feedback()`, `timeframe_feedback()`, `mutation_feedback()`,
  and the `UBS Universo` UI:
  - base `accepted`: score plus accepted bonus (`+20` asset, `+15` TF/mutation).
  - base `rejected`: score minus `REJECTED_BASE_PENALTY` and per-cause
    penalties from `metrics_json.reasons`, capped so rejected rows never add
    positive weight.
  - base `no_trades`: fixed negative reliability penalty.
  - `report_mismatch`, `no_report`, and `parse_error`: no weight.
  - robust `accepted`: add `positive_bonus` (default `+70`).
  - robust `rejected`: add `negative_bonus` (default `-70`) plus OOS
    per-cause penalties.
  - weights are grouped by correlated candidate source before averaging and
    shrunk toward zero for small samples.
  - active seed scores with scored reports contribute at full base strength,
    the same as generated candidates. Seeds do not receive robustness bonus
    unless a separate seed-date/robustness bonus is explicitly added.
- UBS scoring keeps raw report `net_profit` in metrics, but pass/fail and the
  profit score component use `normalized_net_profit`. Current RoboForex-only
  factors live in `assets/roboforex_normalization.json`: Forex/metals/crypto
  `1.0`, indices/energies `2.0`, stocks `5.0`. Metrics JSON includes raw net,
  normalized net, factor, basis, and group.

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
  - `--final-tick-min-history-quality` — minimum acceptable history quality % (default `80`).
  - `--final-tick-min-ohlc-trades` — minimum OHLC trades required before attempting real tick (default `5`).
  - `--final-tick-ohlc-from-date` / `--final-tick-ohlc-to-date` — alternative date range to
    retry the OHLC batch when the primary range yielded too few trades.
  - `--final-tick-max-net-delta-pct`, `--final-tick-max-pf-delta-pct`,
    `--final-tick-max-dd-delta-pct`, `--final-tick-max-trades-delta-pct` — tolerances for
    comparing real-tick metrics against OHLC control metrics (all default `35.0`).
- The agent copies each robust-accepted `.set` twice under
  `outputs/ubs_agent/<run>/final_tick/...`: one OHLC batch with `Model=1` and
  one real-tick batch with `Model=4` (`Every tick based on real ticks`).
- Final Tick requires explicit `--from-date` and `--to-date`; the UI defaults to
  `2026.05.01 -> 2026.05.31` as the last robustness segment example.
- Results are stored in `candidate_final_tick` with both report paths, both
  metrics JSON blobs, `history_quality`, date range, and `similarity_json`.
- **Pending states** (intermediate, not final): `pending_history_quality` — real-tick
  report produced but history quality is below threshold; `pending_ohlc_trades` —
  OHLC batch produced too few trades to make a valid comparison, OHLC retry dates
  may be used to get a qualifying OHLC before re-running real tick.
- A row is final `accepted` only when the real-tick report has
  `History Quality > 80` by default and its real-tick metrics are close to the
  OHLC metrics within configured tolerances for net, PF, DD, and trades.
- UI: `UBS Final Tick` shows robust-accepted candidates, final status, cause,
  history quality %, OHLC metrics, real-tick metrics, date range, set path, and
  "Abrir set" / "Abrir OHLC" / "Abrir Real Tick" report actions.
  Buttons: **Continuar Final Tick** (pending-only, incremental) and
  **Reprobar Final Tick** (replaces all existing rows for the visible run).
  A criteria configuration block exposes all thresholds (quality, min OHLC trades,
  delta tolerances, primary and OHLC-retry date ranges) and a **Guardar config** button.
  `UBS Robustez` also exposes `Continuar Final Tick`.
- The UI variables `ubs_final_tick_from_date`, `ubs_final_tick_to_date`,
  `ubs_final_tick_ohlc_from_date`, `ubs_final_tick_ohlc_to_date`,
  `ubs_final_tick_min_history_quality`, `ubs_final_tick_min_ohlc_trades`,
  and the four `ubs_final_tick_max_*_delta_pct` variables are persisted in
  `ui_settings.ini`.

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

`UBS Agente UBS` has a **Poblar universo sin seed** toggle. It is off by
default and persisted as `ubs_force_unseeded_universe` in `ui_settings.ini`.
When enabled, the UI passes `--force-unseeded-universe` to `ubs_agent.py`.

The option reserves part of generation for universe coverage:

- Asset target selection gets a 65% early chance to choose a universe symbol
  not represented by the current seed pool, preferring symbols with no feedback.
- Timeframe target selection gets a 50% early chance to choose related
  timeframes not represented by the current seed pool, preferring TFs with no
  feedback.
- If an explored asset/TF survives into the next generation as an internal
  candidate seed, it is no longer considered unseeded for that generation.
- Disabled universe symbols remain excluded.

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
  RoboForex-normalized net used by pass/fail scoring.
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
- **"Completar run"** button: appears in `UBS Resultados` when the visible run
  still has planned generations remaining. It delegates to the normal
  continuation path and may run pending backtests plus generate/evaluate the
  remaining generations. This is the deliberate full-plan action.
- **"Continuar a robustez"** button: sends only accepted candidates that do not
  have a `candidate_robustness` row yet (`--robust-pending-only`).
- **"Reprobar robustez"** button: reruns robustness for all accepted candidates
  in the visible run and replaces existing OOS rows.
- **Retryable problem rows**: `report_mismatch` and `no_report` can be retried
  individually or at run level. Once a retry updates the row to `accepted` or
  `rejected`, it enters the normal weight pool. A `rejected` candidate now
  contributes through `ubs.weights`: raw score minus the base rejection penalty
  and per-cause penalties.

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
All are persisted in `ui_settings.ini` under the `ubs_portfolio_*` keys.

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

Do not reintroduce global scaling (`S = target_dd/current_dd`), risk-parity lot
calibration, StartLots validation, or automatic lot normalization in this module.

**Persistence**: `portfolios`, `portfolio_allocations`,
`portfolio_decision_log`, plus legacy-compatible `portfolio_members` in
`outputs/ubs_memory.sqlite`. A set in saved allocations/members is globally
excluded from future portfolios until its portfolio is deleted.

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
  via `force_fixed_lot_text`) → deduplicates by SHA256 of normalised content →
  copies to configured seeds folder. Modal progress popup + summary dialog.
  Implemented in `ui/ubs_seeds_logic.py:_import_ubs_seeds`.
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
- **"Principal"** (was "Habilitada"): only ONE terminal can be principal at a
  time. Clicking SEL unmarks all others. "Aplicar fila" enforces exclusivity
  by setting `enabled=False` on all other profiles when Principal=ON.
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
- MT5 seed reports with zero closed trades are classified as `no_trades`; the
  Seeds tab exposes "Repetir backtest" to relaunch one selected seed directly.
- Seeds and Universe tables have a SEL checkbox column. Seed actions use checked
  rows when present, otherwise the selected row. Universe checked symbols can be
  disabled/enabled; disabled symbols are persisted in
  `outputs/ubs_disabled_symbols.json`, remain visible, and are excluded from
  weights, seed backtest execution, pending counts after reset, and agent
  target-symbol exploration. Seed evaluation records skipped disabled symbols as
  `disabled_symbol` without launching MT5.
- Evaluation dialog shows the actual expected backtest count (pre-computed from
  DB state) alongside the total seed count.
- Refresh buttons now refresh full panel state, and `_refresh_all()` isolates
  section errors so one broken view does not block every tab.

### Fresh MT5 report guard

`run_tests.py` and `ubs_agent.py` ignore reports older than the current batch
start time. This prevents MT5 history-cache failures or stale files from being
scored as if they belonged to the current backtest.

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
