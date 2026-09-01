# Prepared Discovery batches

Lab sends immutable candidates through manager to `manager_node_runtime/node.py`,
embedded in the app. The manager reference node is not the live broker process.

`guided_batches.py` validates hashes, broker/account, full parameter identity and
one numeric step with unchanged optimizer metadata. `guided_controller.py` uses
the existing persistent FIFO and forces base, robustness, Final Tick and Final Tick
6M. Paused work retains ownership; duplicate batches never enqueue twice.

`ubs/prepared.py`, through `ubs_agent.py --prepared-manifest`, validates the local
accepted parent, current universe and mutation rules, then calls the existing
evaluator without remutating. The batch run.json binds fingerprints/candidate IDs
to the exact run for later stages and results. Parent acceptance is not inherited.

API: POST `/api/v1/guided-batches`, GET `/api/v1/guided-batches/{sha256}` with existing
bearer authentication. No payload paths accepted; inbox: `outputs/guided_batches`.
Timing is stage wall time, not CPU usage. No production broker runtime was changed.

Tests: `test_guided_node`, `test_prepared_candidates`, `test_guided_http` (temporary
SQLite, synthetic results, no MT5). Manager checks portable module parity.

Reopen the app to load source changes. The existing restart endpoint performs Git
pull/push before relaunch; do not treat it as a Python-only restart.
