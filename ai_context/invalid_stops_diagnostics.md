# Invalid stops shown as no_trades (2026-09-06)

Observed in ICTrading STANDARD run 445, candidates 82223 and 82224,
KNDI.NAS M30: valid zero-trade reports and 112 explicit failed orders in each
saved journal excerpt, with negative TP values and `[Invalid stops]`.

Two independent gaps caused the misleading status:

- `run_tests.write_tester_journal_sidecars` saves only the last 64 KiB. The old
  OOS detector required `testing of Experts`, normally lost from long journals.
- Base/seed evaluation wrote `no_trades` without consulting the OOS detector.

The shared detector is now `ubs/tester_diagnostics.py::invalid_stops_metadata`.
It scopes evidence to the latest start header, or (for truncated journals) the
latest matching `SYMBOL,TF: ... Test passed` completion. It requires exact
symbol/timeframe identity and explicit failed buy/sell orders; generic retcodes,
another symbol, another timeframe and earlier attempts must not qualify. Counts
refer to the saved excerpt, not necessarily all orders in the backtest.

Base and seed evaluation classify matching zero-trade reports as `rejected` and
persist the evidence in `metrics_json`; OOS uses the same detector and preserves
its existing `degradation_json` contract. Reports with trades and technical
context/mismatch/history failures retain their existing handling. Close-only
and disabled trading retain priority and remain separate from invalid stops.

`AgentMemory._reclassify_invalid_stops_no_trades` repairs the latest run's base
and OOS rows plus seed rows at initialization. An explicit run ID can repair an
older run. It preserves numeric metrics and OOS audit fields and only changes
rows with attributable evidence. SQLite-only base rescore retains the evidence
even when the journal has disappeared. `ScoreResult.from_json` extracts only
dataclass fields so audit metadata does not prevent scoring.

Results, Seeds and Robustness share the explicit user-facing reason
`ordenes rechazadas por Invalid stops (stops invalidos)`.

The executing processes are the IC checkout's `ubs_agent.py` subprocess and
`app_ui.py` (which imports the UI mixins). The manager's `mt5_manager/node.py`
does not execute this logic. Restart the desktop agent to reload already
imported Python modules; do not interrupt an active backtest just to reload UI.

## Applied data repair

Only base candidate rows 82223 and 82224 in run 445 were repaired directly.
Their previous complete rows were backed up in the manager checkout under
`runtime/invalid_stops_run445_before_20260906_235029.json`, after checking its
`dev_branch.assert_writable` guard. Neither reports nor set parameters changed.
Other historical runs were not bulk-reclassified.

## Verification

- 154 focused unittest tests pass, including truncated/foreign/stale journals,
  base evaluation, seed evaluation, OOS detection and migration, reason display,
  metric preservation and rescore after removing the journal.
- Full discovery: 655 tests; failures in guided HTTP (`KeyError: attempt`),
  queue scheduling and regression queue timing. The guided HTTP error also
  reproduces with the changed modules loaded from HEAD; the two queue tests
  pass in that isolated baseline run. Repeating the baseline queue tests also
  reproduces the regression queue timing failure on the third attempt.
- The MCP graph was consulted for discovery/impact. Its IC index is stale;
  reindexing was rejected because the configured server has `CBM_ALLOWED_ROOT`
  pinned to the manager. Direct source reads and tests supply current evidence.

## Score and learning audit (2026-09-07)

Read-only audit of the actual IC memory and current source; no score, weight,
or status changes were needed for this follow-up.

- Candidates 82223/82224 both have `score=-75`, `accepted=0`, zero trades and
  `failure_type=invalid_stops`. Recomputing with their stored ScoreConfig returns
  exactly -75. This is the conventional score of the empty report, not a
  measurement of profitability or a penalty multiplied by rejected orders.
- Both rows occur in `AgentMemory._candidate_feedback_rows` (60 scored rows
  from run 445). They are not silently excluded by an unrecognized new status:
  `invalid_stops` is a reason attached to the existing `rejected` state.
- Current asset/TF/mutation selection uses `probability_feedback_signals`.
  Both `no_trades` and `rejected` contribute one negative base-stage outcome;
  changing those two rows' status in a counterfactual calculation produces
  identical selection signals. Later stages receive no fabricated trials.
  Correlated candidates are grouped, and the magnitude is smoothed and relative
  to global evidence. The 112 observed rejected orders are not 112 trials.
- Generation/source success labels treat a base rejection as failure to reach
  FT 6M. The metric fitness model's `finalized_six_month_label` excludes base
  rejections; an OOS rejection after accepted base is a valid negative label.
- `trade_disabled`, `no_history`, `no_report`, `parse_error`,
  `pending_tester_context`, and `report_mismatch` remain neutral in base
  probability feedback and generation labels. The actual database contains
  2053 trade-disabled candidates with NULL scores, not negative strategy scores.
- Legacy `feedback_weight` does change: these rows go from -40 (`no_trades`)
  to -140 (`-75 - 50 - 15`). The last 15 is the generic fallback for a rejection
  reason; there is no dedicated invalid-stops coefficient. This additive
  utility is retained for audit/UI fallback, not the current selection signal.

Interpretation: negative execution-viability evidence is appropriate for an
unusable set/symbol combination (the observed orders have negative TP). It does
not establish that the underlying trading signal is unprofitable. A dedicated
execution model or different attribution to SL/TP parameters would be a new
modeling policy, not a missing status mapping, so coefficients were not changed
arbitrarily. Existing feedback also attributes failures to asset/TF/mutated
keys and cannot isolate which individual parameter caused the invalid stops.

Validation: 65 focused unittest tests passed (`test_ubs_weights`,
`test_ubs_selection`, `test_ubs_score`, `test_ubs_agent_rescore`,
`test_invalid_stops_diagnostics`), plus read-only assertions on the two actual
rows for score reconstruction, unchanged before/after selection signals,
technical neutrality and base/OOS labels.


## Incompatible volume (2026-09-08)

Run 448 candidates 82403/82404 (AVXUSD H1) carried BTCUSD inputs with
StartLots=0.01 and MaxLots=99, while their matching tester journals explicitly
reported a minimum of 100 lots. `execution_failure_metadata` now handles both
invalid stops and `incompatible_volume`. Volume classification requires the
same attributable test's MaxLots input to be below its positive broker minimum;
a minimum warning or low StartLots alone is insufficient. Truncated journals
without MaxLots remain unclassified. No live symbol specs or current sets are
used as substitutes for the actual test evidence.

Base, seeds, OOS and SQLite rescore preserve this rejection reason and numeric
scores, through the existing invalid-stops migration entry point (name retained
for compatibility). Existing technical/report-context and trade-mode gates
retain priority. Latest-run migration also covers incompatible volume.

`manager_node_runtime.node.database_snapshot` exposes separate
`execution_failures` counts for base and OOS. The manager's node copy carries
the same read-only contract. Its app.js splits those counts out of the rejected
chip, displaying “Lotaje incompatible” and “Invalid stops”; original stage
counts and totals remain unchanged, and old nodes keep their existing display.
The IC desktop agent must reload Python modules when idle; the manager must
serve the updated app.js and the browser reload it.

The two run-448 candidate rows were reclassified with before-images in
`outputs/backups/incompatible_volume_run448_20260908_004909.json`, after the
manager dev-branch write guard. Scores remain -75; no set/report or other run
was modified by that repair.

Verification: focused detector/evaluation/migration/rescore and existing agent
suite, manager node tests, and Node VM assertions for actual card HTML,
remaining rejected count, unchanged total and old-node fallback.
