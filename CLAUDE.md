# CLAUDE.md

This repository's AI context lives in [ai_context/main.md](ai_context/main.md).

Open that file first — it indexes every topic-specific document
(architecture, routing, integrations, environment variables, conventions,
development workflow, HTTP endpoints).

## Code discovery: `codebase-memory-mcp`

This project uses the **DeusData `codebase-memory-mcp`** server as the primary
code-discovery tool. Prefer its graph tools over blind text search:

| Tool | Use it for |
|------|-----------|
| `mcp__codebase-memory__search_graph` | Find symbols/files by meaning or name. |
| `mcp__codebase-memory__search_code` | Text/semantic search inside indexed code. |
| `mcp__codebase-memory__get_code_snippet` | Read the exact body of a node. |
| `mcp__codebase-memory__trace_path` | Follow call/import chains between symbols. |
| `mcp__codebase-memory__query_graph` | Structured queries (callers, dependents…). |
| `mcp__codebase-memory__get_architecture` | High-level module/dependency overview. |
| `mcp__codebase-memory__index_status` / `detect_changes` | Check freshness after edits. |

Notes:

- The tools may be exposed lazily; load them via tool search before calling if
  they are not visible in a fresh session.
- Server name in MCP config: `codebase-memory` (stdio). It is declared in the
  project `.mcp.json`, which is **gitignored** because it points to a
  machine-specific binary path (installer default:
  `%LOCALAPPDATA%\Programs\codebase-memory-mcp\codebase-memory-mcp.exe`).
  A fresh clone on another machine must recreate `.mcp.json`, then restart the
  session — MCP servers only connect at startup. A binary under another Windows
  user's profile will not work; `C:\Users\<other>` is ACL-restricted.
- `detect_changes` diffs against a **git baseline** (`base_branch`, default
  `main`), not against the graph. A long changed-files list right after a clean
  `index_repository` is normal — use `index_status` (`status: ready`) to judge
  index health.
- Fall back to `Grep`/`Glob` when the graph has no answer (non-indexed assets,
  `.set`/`.ini` files, generated outputs) or when the index is stale.

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
[requirements.md](requirements.md).
