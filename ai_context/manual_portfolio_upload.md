# Manual portfolio uploaded from a set folder

`POST /api/v1/portfolios/manual` is the node-owned half of the manager's
`Solo portafolio` screen. It accepts a named A/M/C mode plus base64 `.set`
files, infers their symbol/timeframe through the regular tester parser and
normalizes them through the same incorporation rules as UBS sets before storing
them under:

`runtime/<node>/manual_portfolios/<uuid>/sets/`

The absolute stored paths become `portfolio_allocations.set_path`, so the live
audit runtime resolves the same files without searching the UBS candidate pool.
Basenames must be unique across uploaded subfolders because audit artifacts and
manual report replacement identify a strategy by its set filename.

The persisted copy fixes `Risk=0`, `StartLots=0.01`, disables variable lot and
sets `UseEveryTick=false` for its OHLC baseline. It also applies every immutable
rule from `ubs/set_rules.py`, including the strategy-dependent NFP rule. The
saved row contains one allocation per set, one unit, and 0.01 lot. It
deliberately records zero profit/drawdown and an explicit
pending-history warning; those figures cannot be inferred from set parameters.
The portfolio's history is created later through the existing live-audit flow.
Starting that history repairs portfolios created by older runtimes: their stored
sets and SQLite lot allocations are normalized before `run_tests.py` is queued.

The history request carries `use_multiterminal`. True uses the configured
`[Multiterminal].workers`; false keeps the same broker-aware runner but forces
one worker, so every set uses the principal enabled terminal. Both modes pass
the broker symbol map and suffix arguments to `run_tests.py`; canonical stored
symbols therefore become their executable broker names (for example
`XAUUSD` -> `XAUUSD.sa` on AXI) without rewriting the uploaded set identity.

Manual history is a one-step batch and is never resumable after failure or an
agent restart. A pending retry must be allowed to leave the persistent FIFO;
otherwise the failed step reserves the node forever. Repeated requests for the
same portfolio are coalesced in place, retaining the newest dates and terminal
mode instead of executing duplicate backtest batches.

`queue.json` is a JSON array, unlike the object-shaped state/config files. It
must be loaded directly as an array on startup; the shared `load_json()` helper
rejects non-object roots and previously made every persisted queue silently
disappear after a restart.

Files are written before the SQLite transaction and their new UUID directory is
removed if validation or persistence fails. A successful portfolio owns that
runtime directory; deleting the database row does not currently garbage-collect
the uploaded source files because saved audit history may still reference them.
