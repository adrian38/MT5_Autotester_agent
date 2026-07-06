# AGENTS.md

This repository's AI context lives in [ai_context/main.md](ai_context/main.md).

Open that file first — it indexes every topic-specific document
(architecture, routing, integrations, environment variables, conventions,
development workflow, HTTP endpoints).

For code discovery, prefer `codebase-memory-mcp` graph tools
(`search_graph`, `trace_path`, `get_code_snippet`, `query_graph`,
`search_code`) before falling back to text search. If the MCP tools are not
visible yet in a fresh Codex chat, load/discover `codebase-memory-mcp` first;
the project should already be indexed and `auto_index` is enabled.

For functional requirements and the technical-debt backlog, see
[requirements.md](requirements.md).
