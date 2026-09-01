# Remote Universo controls (2026-08-31)

The manager card opens a four-step dialog. `capabilities.universe_sync` advertises
the complete API implemented by `UniverseControllerMixin` in
`manager_node_runtime/universe_service.py`, inherited by the embedded
`manager_node_runtime.node.JobController`.

- POST `/api/v1/universe/sync`: optional `mt5_path`, `login`, `server`, `password`.
  Empty credentials use the configured terminal/session. Uses existing MT5
  extraction and universe writer, backs up files, retires missing symbols in GEN
  and removes their seed exceptions. Returns total/added/removed/newly_disabled.
- POST `/api/v1/universe/history-preview`: enabled pending count and H1 dates.
- POST `/api/v1/jobs/universe-history`: starts existing `ubs_agent.py` with
  `--probe-universe-history --probe-history-timeframe H1 --execute-backtests`.
  Uses normal node terminal/source/memory options and a one-year date range.
  Preparation runs in the child process; HTTP does not wait for backtests.
- POST `/api/v1/universe/disable-preview`: latest probe-only no_history verdicts,
  already-disabled count and newly-disabled symbols for user confirmation.
- POST `/api/v1/universe/disable-no-history`: accepts the confirmed `symbols`
  list, intersects it with current verdicts and never expands the approval.

Writes stay inside the configured agent project (`assert_writable`). Empty MT5
inventory and corrupt policy fail before overwriting. Extraction errors redact
passwords; credentials are never persisted in job state or a command line.
History verdict previews open SQLite read-only and explicitly close connections.

The history stage is part of the existing node process/log/pause/resume control.
The stage name is `universe_history`, not `generation`; its resume branch must
keep the probe flag. No cleanup/repair/generation pipeline is appended.
Mutation/probe actions reject an active process, resumable pipeline, task queue,
live audit or UI subprocess. `app_ui.py` supplies a callback reading only the
subprocess handle (no Tk calls from HTTP). `job_running` covers a synchronous
symbol sync too, so local script starts cannot collide with it.

Restart the agent app to load the routes. AXI/RoboForex copies have not been
modified by this task. The manager's `mt5_manager/node.py` is not this runtime.

Validation: 594 agent unittests pass; `tests/test_manager_node_universe.py`
covers file persistence/backups, alias and latest-verdict behavior, scoped
destinations, credentials, busy states, actual HTTP/child-process completion
using a harmless stub, and probe resume. Manager tests additionally drive this
fork in a separate interpreter through ManagerServer. No real MT5 backtests ran.
