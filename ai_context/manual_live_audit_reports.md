# Manual Strategy Tester reports for a saved live audit

`manager_node_runtime/live_audit_report_upload.py` implements authenticated
replacement of one or more tester HTML reports in the currently visible run.
`POST /api/v1/live-audits/<audit_key>/reports` validates the complete batch
before changing `last_payload`; MT5 is never launched.

The upload filename is the exact `source_set` basename with `.set` replaced by
`.htm` or `.html`. Free prefixes are deliberately rejected. The parsed report
must identify the same symbol and timeframe and report 100% History Quality.
The node stores it as a separate `manual_<set stem>.htm` artifact and records
its SHA-256, upload time, original filename and `report_source=manual_upload`.

Only the selected strategies' tester trades, counts, qualities and artifact
rows are replaced. Other strategies and the original automatic reports remain
unchanged. The manager recalculates the verdict from the updated raw payload.

AXI run `audit-105-mv4perus-1` has three low-quality rows, not two: candidate
1437 (XAUUSD D1), 3535 (XAUUSD H1) and 5628 (XAUUSD H1). The two supplied H1
reports validate at 100%; candidate 1437 remains at 40% without its own report.
