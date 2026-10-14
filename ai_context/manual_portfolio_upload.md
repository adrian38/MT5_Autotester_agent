# Manual portfolio uploaded from a set folder

`POST /api/v1/portfolios/manual` is the node-owned half of the manager's
`Solo portafolio` screen. It accepts a named A/M/C mode plus base64 `.set`
files, validates `ForceSymbol`, `TimeFrame`, and positive `StartLots`, then
stores the exact files under:

`runtime/<node>/manual_portfolios/<uuid>/sets/`

The absolute stored paths become `portfolio_allocations.set_path`, so the live
audit runtime resolves the same files without searching the UBS candidate pool.
Basenames must be unique across uploaded subfolders because audit artifacts and
manual report replacement identify a strategy by its set filename.

The saved row contains one allocation per set, one unit, and the exact
`StartLots`. It deliberately records zero profit/drawdown and an explicit
pending-history warning; those figures cannot be inferred from set parameters.
The portfolio's history is created later through the existing live-audit flow.

Files are written before the SQLite transaction and their new UUID directory is
removed if validation or persistence fails. A successful portfolio owns that
runtime directory; deleting the database row does not currently garbage-collect
the uploaded source files because saved audit history may still reference them.
