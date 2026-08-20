# Live-account audit in the embedded manager node

The executing implementation is `manager_node_runtime/live_audit.py`, loaded by
`app_ui.py` through `manager_node_lifecycle.py`. The similarly named manager
module is the reference implementation; changing only the manager does not
change this broker node.

Each audit use is identified by `audit_key`, independently from `portfolio_id`.
The same saved bundle can therefore be audited in different accounts or in
different A/M/C variants. Requests must include one exact `portfolio_type`:
`aggressive`, `balanced`, or `conservative`. The runtime never falls back to all
bundle members.

## MT5 source-history rules

- Verify login, exact server, and `terminal_info.connected` before trusting
  account history.
- `history_deals_get()` immediately after `initialize()` can return an empty or
  stale cache. Poll until a non-empty fingerprint is stable; for a genuinely
  empty account, retain every empty snapshot in diagnostics.
- A close inside the audited period can belong to a position opened before the
  period. Recover its full deal history by `position_id` before reconstructing
  trades, then retain only trades whose close falls inside the requested range.
- Persist safe diagnostics: sync snapshots, raw/market/open/close deal counts,
  missing and recovered prior opens, reconstructed trades, portfolio closures,
  and ignored foreign closures.
- Filter the account closures by `(symbol, EA_MagicNumber)` from the selected
  bundle variant before comparing them with Strategy Tester.

The 2026-08-20 verification for audit use `9` connected to MT5 profile
`MT5_IC_1`, login `52958158`, server `CapitalPointTrading-Demo`. The exact
seven-day interval contained 17 market deals, 8 in-period opens and 9 closes.
One close had an opening before the period and was recovered successfully.
Five of the nine closes matched the selected Moderate bundle signatures; four
belonged to other strategies. The corrected end-to-end run produced 39 tester
trades and 4 real-tester matches.

## Tester artifacts and logging

Source `.set` files are UTF-16. Detect their encoding, preserve it in the audit
copy, and replace `StartLots` rather than appending a second parameter. Logs
must redact both account secrets and every `Password=` value, including the log
files written internally by `run_tests.py`. The verified run created six
UTF-16 set copies with exactly one `StartLots` each and 27 log/text artifacts
with no persisted password or configured secret.

## Auditable comparison contract

The runtime persists one safe JSON row per tester trade in
`comparison_detail.operation_comparisons`. Each row contains the tester trade,
the selected real trade or nearest unused candidate, measured deltas, applied
limits, and exact reason codes. It also persists per-strategy summaries,
unmatched real operations, methodology, and aggregate drawdown validation.

`matched_trades` is the number of symbol/side/open-time aligned pairs. It is not
proof that a pair passed every check; `within_tolerance_trades` is that stricter
count. This distinction must remain visible in summaries. The manager renders
the contract on a dedicated result page. Old aggregate-only results cannot be
expanded retrospectively and must request a fresh audit instead of inventing
row detail.

## Reports and executed-lot evidence

Every completed run now persists `strategy_artifacts`. For each selected member
it records the configured portfolio lot and rereads `StartLots` from the exact
runtime `.set` copy after writing it. It also records magic, source/runtime set
filenames, trade count, History Quality, and the MT5 report filename. The
manager result page shows an explicit match/mismatch status and opens each
original MT5 report in a separate tab.

The runtime also writes `real_account_period_report.html`, containing every
reconstructed account closure in the audited interval and marking whether its
`(symbol, magic)` belongs to the selected variant. Run
`20260821_000733_052028` (audit use 9, aggressive) produced nine account
closures: five selected and four foreign. Its six reread lot checks matched:
BTCUSD 0.06, XAUUSD 0.03, USDJPY 0.04, XAGUSD 0.04, EURUSD 0.06, and USTEC
0.01; every MT5 report had 100% History Quality.
Those checks prove the runtime set values, not the final EA order size. Five
reports traded exactly their `StartLots`; USTEC traded 0.10 while its runtime
set contained `StartLots=0.01`. Keep `observed_trade_volumes` and
`report_volumes_match_start_lots` visible so this discrepancy cannot be hidden
behind a successful set rewrite.

`LiveAuditController.artifact_path` only serves HTML/image files from the
current run's `reports` directory. The authenticated node endpoint serves the
bytes and the manager proxies them. Never broaden it to `.set`, INI, logs, or
arbitrary runtime paths; those can contain implementation details or secrets.
## Normalización del lote en portfolios antiguos

Desde 2026-08-21 el runtime ejecutado lee `assets/<broker>_symbol_specs.json` y
normaliza `StartLots` a `max(lot_guardado, units * volume_min)`, redondeado a
`volume_step`. Así USTEC con una unidad guardada históricamente como `0.01` se
audita a `0.10`, que es el mínimo real publicado por ICTrading. La evidencia de
la auditoría conserva ambos lotes y la regla aplicada.
