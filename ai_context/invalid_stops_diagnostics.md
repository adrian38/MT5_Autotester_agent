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
