# AGENTS.md

This repository's AI context lives in [ai_context/main.md](ai_context/main.md).

Open that file first — it indexes every topic-specific document
(architecture, routing, integrations, environment variables, conventions,
development workflow, HTTP endpoints).

## Code discovery: `codebase-memory-mcp`

This project uses the **DeusData `codebase-memory-mcp`** server as the primary
code-discovery tool. Prefer its graph tools (`search_graph`, `search_code`,
`trace_path`, `get_code_snippet`, `query_graph`, `get_architecture`) before
falling back to text search. If the MCP tools are not visible yet in a fresh
Codex chat, load/discover `codebase-memory-mcp` first; the project should
already be indexed and `auto_index` is enabled.

- Project key: derived from the checkout root, so it differs per clone
  (`G-TRADING-MT5_Autotester_agent` for RoboForex,
  `F-TRADING-MT5_Autotester_agent_AXI` for AXI). Git-aware and branch-scoped.
  Resolve it with `list_projects` instead of hardcoding it, and pass the key
  matching the checkout you are actually in.
- Server name in MCP config: `codebase-memory` (stdio, no args). It is declared
  in the project `.mcp.json`, which is **gitignored** because it points to a
  machine-specific binary path (installer default:
  `%LOCALAPPDATA%\Programs\codebase-memory-mcp\codebase-memory-mcp.exe`).
  A fresh clone on another machine must recreate `.mcp.json` and restart the
  session. A binary under another Windows user's profile will not work;
  `C:\Users\<other>` is ACL-restricted.
- Use `index_status` / `detect_changes` to check freshness after large edits,
  and `index_repository` to re-index when the graph is stale. Note
  `detect_changes` diffs against a git baseline (`base_branch`, default `main`),
  not against the graph — a long changed-files list right after a clean
  re-index is normal; trust `index_status` (`status: ready`) instead.
- Fall back to grep/glob for non-indexed material (`.set`, `.ini`, HTML
  reports, generated outputs) or when the index is stale.

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

For functional requirements and the technical-debt backlog, see
[requirements.md](requirements.md).
