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

- Project key: `F-TRADING-MT5_Autotester_agent_AXI` (root
  `F:/TRADING/MT5_Autotester_agent_AXI`, git-aware, branch-scoped).
- Server name in MCP config: `codebase-memory` (stdio, no args). It is declared
  in the project `.mcp.json`, which is **gitignored** because it points to a
  machine-specific binary path
  (`C:\Users\13199\.claude\tools\codebase-memory-mcp\codebase-memory-mcp.exe`).
  A fresh clone on another machine must recreate `.mcp.json`.
- Use `index_status` / `detect_changes` to check freshness after large edits,
  and `index_repository` to re-index when the graph is stale.
- Fall back to grep/glob for non-indexed material (`.set`, `.ini`, HTML
  reports, generated outputs) or when the index is stale.

For functional requirements and the technical-debt backlog, see
[requirements.md](requirements.md).
