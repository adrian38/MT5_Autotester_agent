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
