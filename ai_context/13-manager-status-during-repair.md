# Manager status during bulk repair

`JobController` holds its execution lock while checking pending work and
skipping already-complete pipeline stages. A repair covering hundreds of runs
can hold that lock for minutes, even though HTTP health checks and the repair
itself keep working. Do not infer a stopped worker from a status timeout.

Status and log reads use `_read_status_snapshot`: they try the execution lock
without waiting and otherwise return the latest detached snapshot published
by `_persist` / `_persist_queue`. Snapshots include a consistent job and queue
and do not share mutable nested state with the writer or HTTP callers.
`job_snapshot_stale` and `job_observed_at` distinguish this fallback from a
fresh observation. Command endpoints still use the execution lock; this change
does not allow concurrent launches or bypass process guards.

The manager separately retains the last successful response when polling fails,
labels the card as stale, keeps counters and details visible, and disables
mutation controls until a successful refresh. `/api/pulse` also exposes `stale`.

Validation: `tests/test_manager_node_status.py` blocks a real repair preflight
in a worker thread and checks actual HTTP status/log requests remain available,
then verifies refresh and nested snapshot isolation. The fixture never runs MT5.

Restart the agent only when its current work can safely stop to load Python
changes. Editing files does not update the already-running embedded node.
