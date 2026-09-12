# Prepared Discovery batches

Lab sends immutable candidates through manager to `manager_node_runtime/node.py`,
embedded in the app. The manager reference node is not the live broker process.

`guided_batches.py` validates hashes, broker/account and full parameter identity.
Ordinary candidates require one numeric step with unchanged optimizer metadata;
the explicit `symbol_exploration` mode permits only a `ForceSymbol` retarget to
an enabled symbol that has no Final Tick 6M positive in current memory.
`guided_controller.py` uses
the existing persistent FIFO and forces base, robustness, Final Tick and Final Tick
6M. Paused work retains ownership; duplicate batches never enqueue twice.

`ubs/prepared.py`, through `ubs_agent.py --prepared-manifest`, validates the local
accepted parent, current universe and mutation rules, then calls the existing
evaluator without remutating. The batch run.json binds fingerprints/candidate IDs
to the exact run for later stages and results. Parent acceptance is not inherited.
Pure symbol retargets keep their `symbol_exploration`/`symbol_retarget` provenance
in `mutation_details_json`, but persist an empty `mutated_keys`: `ForceSymbol` is
execution context, never a strategy-parameter mutation or mutation-weight signal.

API: POST `/api/v1/guided-batches`, GET `/api/v1/guided-batches/{sha256}` with existing
bearer authentication. No payload paths accepted; inbox: `outputs/guided_batches`.
Timing is stage wall time, not CPU usage. No production broker runtime was changed.

Tests: `test_guided_node`, `test_prepared_candidates`, `test_guided_http` (temporary
SQLite, synthetic results, no MT5). Manager checks portable module parity.

Reopen the app to load source changes. The existing restart endpoint performs Git
pull/push before relaunch; do not treat it as a Python-only restart.

Automatic repair is strictly post-run. The node first executes the ordinary
pipeline once (`generation`, robustness and both Final Tick stages as enabled),
using the run worker limit. Only after those stages finish does it append the
configured repair attempts and their two pending-only phases. Repair must never
replace or split the ordinary pipeline.

MT5 LiveUpdate can exit the launched PID and retain/relaunch the same profile
under another PID (`/update /path:...`, then `/skipupdate`). `run_tests.run_test`
now waits for profile release after every process wait, before restoring history,
retrying or completing a job. Matching includes the updater installation path;
two clear process snapshots are required. The wait uses the configured absolute
job timeout (minimum 120 seconds). If release cannot be confirmed, that worker
stops consuming jobs; it must not reuse the profile. This is runner ownership,
not a Lab or manager scheduling delay. New runner subprocesses load this change.

ICTrading prepared execution resolves symbols against the active ICTrading universe
and writes exact broker casing (for example `TecDE30`, `MidDE50`) into the
execution copy's `ForceSymbol` and candidate metadata. Package bytes, hashes,
parent sets and batch identity stay unchanged. This applies to both exploration
and numeric prepared candidates; AXI and RoboForex execution behavior is unchanged.

Execution spelling must come from the instrument groups returned by
`load_asset_universe`, never `broker_universe_symbols`: the latter intentionally
uppercases membership keys and includes aliases. IC prepared execution matches
case-insensitively without stripping punctuation or suffixes, requires exactly
one actual instrument after symbol mapping, and rejects unresolved/ambiguous
names before creating a run. Regression tests use a real temporary assets INI
and the real membership loader, covering mixed case, suffixes and alias keys.
This fixes new prepared execution copies; existing completed batches and their
persisted source sets are not rewritten. Candidate, generation and full-run
retry paths repair `ForceSymbol` in their temporary execution copies from the
current IC instrument groups, so historical candidates also use exact broker
spelling. An unresolved or ambiguous name stops before MT5 is launched.
