# Central PostgreSQL migration

## Target ownership

- PostgreSQL runs on the `agent_manager` host and is the only authoritative database.
- Every stored row is scoped by `node_id`, `broker`, and `account_type`.
- Broker nodes never connect to PostgreSQL directly. They submit authenticated,
  idempotent result payloads to the manager API.
- The manager panel reads PostgreSQL locally.
- MT5 reports, charts, logs, and other heavy artifacts stay on the node that
  produced them. Seeds plus accepted/actionable `.set` files are small operational
  inputs and are included in the central clone snapshot. A separate authenticated
  artifact API is still required for remote report opening.
- No mapped drives, SQLite over SMB, database copies used as live storage, or
  `runtime/portfolio_snapshots` are part of the target design.

The Docker deployment in this repository is the first migration stage. It must be
run on the manager machine for production. It does not authorize broker nodes to
open the PostgreSQL port.

## Current process inventory

| Process / component | Starts or calls | Current database behavior | Required cutover |
|---|---|---|---|
| `app_ui.py` | Tk UI, embedded `NodeServer`, CLI scripts | Many screens read and mutate account SQLite files directly | Manager panel must read/write PostgreSQL through a manager-owned repository/service |
| `manager_node_runtime.node` | Authenticated HTTP server and queued `ubs_agent.py` stages | Reads SQLite for status, stage counts, latest run id and portfolios | Node status must use job results/local metadata; result rows must be posted to manager ingestion API |
| `ubs_agent.py` / `ubs.memory.AgentMemory` | `run_tests.py` and the full UBS pipeline | Authoritative writer for runs, candidates, seeds, robustness and Final Tick tables | Keep local write only during transition; then submit typed batches to manager and stop treating SQLite as authoritative |
| `run_tests.py` | One `terminal64.exe` per job | Produces local reports; no SQLite writes | Keep reports local and return artifact identifiers/paths in ingestion payloads |
| `compile_mq5.py` / `compile_and_backtest.py` | `MetaEditor64.exe`, then `run_tests.py` | No SQLite writes | No database change required |
| UBS results/seeds/robustness/final-tick UI logic | Direct SQL and `ubs.manual_status` | Manual accept/reject, reset, delete and rescore operations write SQLite | Move mutations to manager-owned commands/API |
| UBS universe/search UI logic | Direct SQL across broker/account SQLite files | Reads weights/audits; reset actions null scores | Query and mutate central scoped rows |
| UBS portfolio UI logic | Creates and writes portfolio tables in SQLite | Saves portfolios, allocations, decisions, members, quarantine and versions | Persist these tables only in PostgreSQL |
| `manager_node_runtime.portfolio_save` | `/api/v1/portfolios/save|delete|exclude` | Writes the node SQLite after authenticated manager requests | Remove/disable these node-side DB writes after manager portfolio persistence is ready |
| `ubs.account` legacy migration | Copies/replaces old SQLite files and rewrites paths | Transitional local filesystem/SQLite mutation | Retire after every account is imported and cut over |
| `ubs.backup` and `tools/ubs_memory_backup.py` | SQLite backup API | Creates SQLite backups | Replace authoritative backup with `pg_dump`; keep node artifact backup separate |
| Audit tools | SQLite reads; some instantiate schema helpers | Inspect local memories | Add PostgreSQL-scoped audit variants before retiring SQLite |
| PowerShell cleanup, installer, Explorer actions | External Windows processes | No database writes | No database change required |
| `tools/sync_node_configuration.py` | PostgreSQL config registry | Publishes/restores broker/node/account files and actionable `.set` artifacts | Keep as the clone bootstrap; application startup integration remains pending |

## Configuration and clone registry

Migration `003_configuration_registry.sql` adds three central structures:

- `configuration_documents`: versioned exact file payloads scoped by
  `node_id`, `broker`, and `account_type`. `.env` and `manager_node.json` are
  encrypted with PostgreSQL `pgcrypto`; plaintext is never stored for those rows.
- `terminal_profiles`: queryable projection of every `[Terminal.N]` entry,
  including broker, executable, data directory, Experts root and enabled state.
- `broker_universe_symbols`: queryable projection of the broker asset INI by
  asset group, symbol and original position.

The clone snapshot includes:

- node: `manager_node.json`, `.env`, `ui_settings.ini`, `compile_root.txt`,
  `experts_root.txt`, and `experts_list.txt`;
- broker: `tester_template.ini`, `assets/<broker>_assets.ini`, broker
  normalization, and timeframe universe when present;
- broker/account: disabled-symbol policy, global parameters, mutation overrides
  when present, all ready seeds, and candidate `.set` files whose state is
  `accepted`, `history_ok`, `no_history`, `no_report`, or `report_mismatch`;
- portfolio-referenced `.set` files when present.

It intentionally excludes SQLite files, generated tester INIs, queue/runtime
state, HTML reports, Excel workbooks, logs, images and rejected/no-trade sets.
Restore validates every SHA-256, rejects absolute/path-traversal targets, rewrites
project-local Windows paths to the clone root, and can assign a new node id/port.

`CENTRAL_DATABASE_URL` and `CENTRAL_CONFIG_KEY` are the unavoidable bootstrap
secrets. The config key must be independent, at least 20 characters, and shared
out-of-band with a new clone. PostgreSQL cannot safely store the key that decrypts
its own bootstrap secrets.

The database stores terminal addresses, not MetaTrader installations or `.ex5`
binaries outside the project. A clone on another machine still requires those
external installations/Experts paths to exist or be remapped.

### AXI snapshot verified 2026-07-16

- 11 base configuration documents plus 3,118 `.set` files (50 MiB plaintext).
- 359 ready seeds and 2,759 actionable/generated sets.
- 1,256 AXI universe symbols in seven asset groups.
- Six terminal profiles are preserved: five AXI profiles and one legacy
  RoboForex profile. Only one AXI profile is currently enabled. Every AXI
  terminal executable/data/Experts path existed during inspection; the legacy
  RoboForex profile's Experts root did not.
- Full clean restore produced 3,129 files with matching hashes and zero reports,
  SQLite files, spreadsheets, HTML files or logs.

### RoboForex ECN portfolio recovery verified 2026-07-16

RoboForex portfolio history was split across two same-lineage memories on `G:`.
The legacy memory was imported first and the scoped current memory second. Shared
source ids are updated by the current copy; legacy-only ids remain available.

- 21 unique portfolios: source ids `14–21`, `23`, `25–34`, `41`, and `42`.
- 213 allocations and 213 members, all with units/lots, profit contribution,
  drawdown data, member quality score, and combined profit.
- 5,885 optimizer decision rows, all with decision score, gain, and resulting
  portfolio metrics; five quarantine rows; no orphan portfolio relationships.
- All 21 portfolios retain `metrics_json` and binding constraint.
- 26,630 scored candidates and 2,828 complete generation-selection weight rows.

Migration `004_portfolio_runtime_metrics.sql` was required after the current
RoboForex memory exposed newer floating-drawdown, recent-performance, and report
path columns. The importer intentionally rejected that schema drift before the
migration was added; it now imports and repeats idempotently.

### RoboForex clone snapshot verified 2026-07-16

- The current ECN memory on `G:` contributes 37 runs, 26,635 candidates,
  10,576 robustness rows, 6,361 Final Tick rows, 2,545 six-month Final Tick
  rows, and 2,828 complete generation-selection rows.
- ECN has 356 current seed scores and 33 overrides. PRO has 354 seed scores and
  38 overrides; its legacy and current memories contained the same logical
  seeds under different checkout paths and were reconciled rather than doubled.
- The clone registry contains 12 base documents plus 10,966 actionable `.set`
  files (178,350,864 plaintext bytes), five terminal profiles, and 90 universe
  symbols.
- A clean restore produced 10,978 files with verified SHA-256, rewrote the node
  id and project root, and contained zero SQLite files, reports, spreadsheets,
  HTML files, logs, or `portfolio_snapshots`.

### ICTrading Standard migration verified 2026-07-16

The two dated backups were imported oldest-first and the current UNC memory was
imported last. The current memory is a superset, so the reconciled central scope
contains:

- 40 runs, 21,058 candidates, 356 seed scores, and 13 seed overrides;
- 2,042 robustness rows, 1,190 Final Tick rows, 613 six-month Final Tick rows,
  and 1,914 generation-selection rows;
- three portfolios, 54 allocations, 54 members, and 77 scored decisions;
- zero unresolved portfolio, allocation, member, decision, or candidate links.

There are 2,461 historical candidates whose source `run_id` is absent from the
available ICTrading memories. They are retained with a null central run link and
an explicit ingestion warning, as with the known AXI legacy anomaly.

The clone registry contains 11 base documents plus 5,821 actionable `.set`
files (97,856,185 plaintext bytes), all six `[Terminal.N]` profiles from the
source settings (five ICTrading and one RoboForex profile), and 3,335 universe
symbols. A clean restore produced 5,832 files with matching SHA-256, correctly
rewrote clone identity/project paths, and contained zero excluded artifact
types.

Seed identities are stored as portable
`sets\ubs_ready\<BROKER>\<ACCOUNT_TYPE>\...` paths. This removes drive letters,
UNC roots, and the pre-broker `ubs_ready/<account>` layout from their natural
key while leaving report/artifact source paths untouched.

## Existing authenticated node API

The node API already uses a bearer token compared with `hmac.compare_digest`.
It currently exposes health, status, logs, run summaries, universe and portfolio
views plus job/universe/portfolio mutations. It does **not** expose a complete
result-ingestion contract and is not a substitute for the manager ingestion API.

The target manager API still needs endpoints for:

1. Idempotent result-batch ingestion keyed by node and batch/request id.
2. Explicit run/candidate/stage schemas with validation and size limits.
3. Manual manager-owned mutations (status, score reset, deletion, portfolio save).
4. Authenticated artifact metadata/download from nodes, with path allow-listing.

## Migrated relational data

`database/init/001_central_schema.sql` covers every non-internal table currently
present in the account memories:

- `runs`, `candidates`, `seed_scores`, `seed_overrides`
- `candidate_robustness`, `candidate_final_tick`, `candidate_final_tick_6m`
- `generation_seed_selection`
- `portfolios`, `portfolio_allocations`, `portfolio_decision_log`,
  `portfolio_members`, `portfolio_quarantine`, `portfolio_versions`

Central ids are independent from node ids. Every source id is preserved in a
`source_*` column and uniqueness is scoped by node/broker/account. The importer
creates a consistent temporary SQLite snapshot through the SQLite backup API, so
committed WAL data is included and the live source is never modified.

The importer refuses unknown source tables or columns. This is intentional: a
schema change must update both PostgreSQL and the importer instead of silently
dropping new data.

`database/init/004_portfolio_runtime_metrics.sql` extends portfolio persistence
with closed/floating drawdown fields and allocation-level balance/equity DD,
recent performance, floating-DD source, and Final Tick/full-history artifact
paths found in the current RoboForex runtime schema.

### Known AXI legacy anomaly

The first AXI migration found 1,270 candidates whose source `run_id` is `0` and
has no matching `runs` row. They are retained with `source_run_id=0` and a null
central `run_id`. The ingestion batch records `warnings_orphan_candidates=1270`.
No robustness or Final Tick row in the inspected AXI memory has a missing parent.

## Commands

Create a local secret file from `.env.central-db.example`, then:

```powershell
docker compose --env-file .env.central-db -f docker-compose.central-db.yml up -d db
docker compose --env-file .env.central-db -f docker-compose.central-db.yml --profile tools build migrator
docker compose --env-file .env.central-db -f docker-compose.central-db.yml --profile tools run --rm migrator `
  --config /app/manager_node.json `
  --sqlite-path /app/outputs/ubs_memory_AXI_STANDARD.sqlite
```

Run the last command once per broker/account memory, using that node's own config.
It is safe to repeat: all domain records are upserted by their scoped source key,
and each attempt is recorded in `ingestion_batches`.

On the manager host, mount/stage each broker checkout in turn and publish its
complete clone snapshot with the manager-owned tool (do not expose PostgreSQL to
the broker node):

```powershell
docker compose --env-file .env.central-db -f docker-compose.central-db.yml --profile tools run --rm config-sync `
  publish --root /app
```

This includes actionable `.set` files and is intentionally the slower full
snapshot. To update only the 11 small configuration documents while preserving
the last active `.set` snapshot:

```powershell
docker compose --env-file .env.central-db -f docker-compose.central-db.yml --profile tools run --rm config-sync `
  publish --root /app --configuration-only
```

After changing `CENTRAL_CONFIG_KEY`, republish the two encrypted documents from
local plaintext with `--configuration-only --rotate-secrets`. Keep the previous
key until that command succeeds; existing ciphertext cannot be recovered with a
new key alone.

Restore a fresh Git clone (the path after `--target-project-dir` is the Windows
host path, while `--root` is the same folder through the Docker `/app` mount):

```powershell
docker compose --env-file .env.central-db -f docker-compose.central-db.yml --profile tools run --rm config-sync `
  restore --root /app --target-project-dir "F:\TRADING\NEW_AXI_CLONE" `
  --source-node-id axi-standard-192-168-1-152 --new-node-id axi-new-node --port 8763
```

Add `--configuration-only` to restore just the 11 base documents without the
`.set` snapshot.

Do not reuse a node id or listening port for two simultaneously running clones.

## Cutover order

1. Import and reconcile every account memory while nodes continue operating.
2. Implement and test manager ingestion plus node delivery/retry/outbox behavior.
3. Change the manager panel and portfolio mutations to PostgreSQL.
4. Change agent completion to deliver typed result batches and artifact metadata.
5. Run a final reconciliation, compare counts and relationships, then freeze the
   SQLite writers.
6. Remove mapped-drive/copy/snapshot code and replace database backups with
   PostgreSQL backups.

Until step 5, PostgreSQL is a migration mirror, not yet the runtime source of truth.
The configuration/clone registry is usable now as a manager-local migration tool.
Remote broker nodes must not receive PostgreSQL credentials; an authenticated
manager bootstrap endpoint must eventually serve the same scoped snapshot. The
restored application also still needs the manager repository and result-ingestion
cutover before it can run solely from central state with no transitional local
SQLite writer.
