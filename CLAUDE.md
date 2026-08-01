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

- Project key: `F-TRADING-MT5_Autotester_agent_AXI` (root
  `F:/TRADING/MT5_Autotester_agent_AXI`, git-aware, branch-scoped).
- Server name in MCP config: `codebase-memory` (stdio). It is declared in the
  project `.mcp.json`, which is **gitignored** because it points to a
  machine-specific binary path
  (`C:\Users\13199\.claude\tools\codebase-memory-mcp\codebase-memory-mcp.exe`).
  A fresh clone on another machine must recreate `.mcp.json`.
- The tools may be exposed lazily; load them via tool search before calling if
  they are not visible in a fresh session.
- Fall back to `Grep`/`Glob` when the graph has no answer (non-indexed assets,
  `.set`/`.ini` files, generated outputs) or when the index is stale.

For functional requirements and the technical-debt backlog, see
[requirements.md](requirements.md).
