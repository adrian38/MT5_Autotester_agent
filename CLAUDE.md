# CLAUDE.md

## First thing, every session

Before any other work, confirm the **codebase-memory graph is usable**, and if
it is not, **say so to the user in the first reply** — do not start the task
quietly on `Grep`/`Read` and let them find out later.

The check is automatic: the `SessionStart` hook in
[.claude/settings.json](.claude/settings.json) runs
[tools/check_codebase_memory.sh](tools/check_codebase_memory.sh) and reports
`codebase-memory READY` or `codebase-memory IS NOT USABLE` with the reason. It
inspects `.mcp.json`, the binary it points to and the graph store on disk —
it **cannot** see whether the MCP tools actually connected in this session, so
still load them (`ToolSearch`) and make one real call before relying on them.
If that fails, report it, state what you will do instead, and carry on.

This repository's AI context lives in [ai_context/main.md](ai_context/main.md).

Open that file first — it indexes every topic-specific document
(architecture, routing, integrations, environment variables, conventions,
development workflow, HTTP endpoints).

## Code discovery: `codebase-memory-mcp` (mandatory)

Code discovery in this repo goes through the **DeusData `codebase-memory-mcp`**
graph. This is not a preference:

1. The **first discovery call** of any task that touches code is a graph call —
   `search_graph`, `search_code` or `get_architecture`. Not `Grep`, not `Glob`,
   not reading files at random.
2. `Grep`/`Glob` are the **fallback**, allowed only when the graph has no answer
   (non-indexed assets: `.set`, `.ini`, `.mq5` sources, generated outputs under
   `outputs/`, `reports/`, `logs/`) or when the index is stale.
3. After closing a batch of edits, **reindex** (`index_repository`) so the next
   session starts from a fresh graph. Reindex through the **MCP tool**, not the
   binary's `cli index_repository`: on v0.9.0 here the CLI runner dies with
   `Indexing worker crashed on a file` (empty worker log) while the same index
   through the server succeeds. If the MCP is down, reindex after restarting
   the session rather than reaching for the CLI.

| Tool | Use it for |
|------|-----------|
| `mcp__codebase-memory__search_graph` | Find symbols/files by meaning or name. |
| `mcp__codebase-memory__search_code` | Text/semantic search inside indexed code. |
| `mcp__codebase-memory__get_code_snippet` | Read the exact body of a node. |
| `mcp__codebase-memory__trace_path` | Follow call/import chains between symbols. |
| `mcp__codebase-memory__query_graph` | Structured queries (callers, dependents…). |
| `mcp__codebase-memory__get_architecture` | High-level module/dependency overview. |
| `mcp__codebase-memory__index_status` / `detect_changes` | Check freshness after edits. |

### Session checklist

- The tools are exposed **lazily**. Load them in the first message of the
  session, in one call:
  `ToolSearch select:mcp__codebase-memory__search_graph,mcp__codebase-memory__search_code,mcp__codebase-memory__get_code_snippet,mcp__codebase-memory__trace_path,mcp__codebase-memory__query_graph,mcp__codebase-memory__get_architecture,mcp__codebase-memory__index_status,mcp__codebase-memory__list_projects,mcp__codebase-memory__index_repository`
- Resolve the project key with `list_projects` (see the per-clone keys below)
  and check `index_status` reports `status: ready`.
- If the server is not in the session at all, it is a **startup** problem:
  `.mcp.json` must exist and the session must be restarted. MCP servers connect
  only at startup; a server that drops mid-session does not come back without a
  restart either. `.mcp.json` is **gitignored** — it holds a machine-specific
  binary path (see the table below), so a fresh clone has to recreate it. A
  binary under another Windows user's profile will not work; `C:\Users\<other>`
  is ACL-restricted.
- `detect_changes` diffs against a **git baseline** (`base_branch`, default
  `main`), not against the graph. A long changed-files list right after a clean
  `index_repository` is normal — judge index health by `index_status`.

### The graph panel (`http://127.0.0.1:9749`)

A reindex is not finished until the project shows there. What the panel is, on
this workstation:

| | |
|---|---|
| Panel URL | `http://127.0.0.1:9749` (JSON-RPC at `/rpc`, REST at `/api/*`) |
| Shared store | `C:\Users\13199\.cache\codebase-memory-mcp\<project-key>.db` |
| UI build (**used by `.mcp.json`**) | `%LOCALAPPDATA%\codebase-memory-mcp\codebase-memory-mcp.exe` — v0.9.0 `ui` variant, on `PATH`, serves the panel |
| Standard build | `~\.claude\tools\codebase-memory-mcp\codebase-memory-mcp.exe` — same version, **no UI**: `--ui=true` does nothing. Kept as fallback |

`.mcp.json` points at the UI build so the session's own server can serve the
panel with a freshly read project list.

**Do not blindly `update` this binary.** Versions **≥ 0.11** refuse to start on
this workstation:

```
secure CLI coordination could not be created (endpoint): C:\Users\13199:
DACL entry 4 grants mutation rights to untrusted identity (… -1009)
```

The profile `C:\Users\13199` grants `FullControl` to the local accounts `robo`
and `demo`, and the newer builds treat that as untrusted. v0.12.1 fails the same
way but reports only `could not load saved runtime settings`. Until those ACLs
are cleaned up, **v0.9.0 is the ceiling**; reinstall a pinned version with:

```powershell
$env:CBM_DOWNLOAD_URL = "https://github.com/DeusData/codebase-memory-mcp/releases/download/v0.9.0"
& "$env:USERPROFILE\.claude\tools\codebase-memory-mcp\install.ps1" --ui --skip-config `
    "--dir=$env:LOCALAPPDATA\codebase-memory-mcp"
```

Both builds read the same store, but the panel's project list is a **snapshot
taken when its process started** — it never picks up projects indexed later. A
project missing from the panel does not mean it is unindexed: check
`list_projects` first, then restart the panel process to refresh it:

```powershell
$uiPid = (Get-NetTCPConnection -LocalPort 9749 -State Listen).OwningProcess
Stop-Process -Id $uiPid -Force
Start-Process "$env:LOCALAPPDATA\codebase-memory-mcp\codebase-memory-mcp.exe" -WindowStyle Hidden
```

Kill only the process holding port 9749. The other `codebase-memory-mcp.exe`
instances are other agents' MCP servers (Codex spawns its own) — leave them.

## Checkouts, branches and brokers

This repo is cloned **once per broker**, each clone pinned to its own branch.
The broker is not inferable from the source — the code carries all three at once
(`validate_roboforex_margin` / `validate_ttp_margin`, `assets/axi_*`,
`assets/ictrading_*`) — so confirm which checkout you are in before touching
assets, margin profiles or normalization.

| Branch | Broker | Checkout root |
|--------|--------|---------------|
| `dev` | **RoboForex** | `G:\TRADING\MT5_Autotester_agent` |
| `AXI` | **AXI** | `F:\TRADING\MT5_Autotester_agent_AXI` |
| `IC` | **ICTrading** | `C:\Users\Adrian\Adrian\TRADING\MT5_Autotester_agent_IC\MT5_Autotester_agent` |

Broker branches (`AXI`, `IC`) merge into `dev`. Paths are per-workstation, and
the IC checkout is normally worked on a different PC than `dev`/`AXI`.

The `codebase-memory` project key is derived from the checkout root, so it
differs per clone (`G-TRADING-MT5_Autotester_agent` for RoboForex,
`F-TRADING-MT5_Autotester_agent_AXI` for AXI). **Resolve it with
`list_projects` instead of hardcoding it**, and pass the key matching the
checkout you are actually in.

For functional requirements and the technical-debt backlog, see
[docs/requirements.md](docs/requirements.md).
