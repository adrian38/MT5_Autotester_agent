#!/usr/bin/env bash
# SessionStart check: is the codebase-memory graph usable in this checkout?
#
# Printed as JSON on stdout so Claude Code injects it into the session context
# (and shows the user a message when something is wrong). Never fails the
# session: every problem is reported, not raised.
#
# Registered in .claude/settings.json under hooks.SessionStart.

set -u

root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$root" 2>/dev/null || true

# Windows-style root ("F:/TRADING/..."), which is what the project key derives from.
win_root="$(pwd -W 2>/dev/null || pwd)"
project_key="$(printf '%s' "$win_root" | sed -e 's/://' -e 's#/#-#g')"

store_dir="$HOME/.cache/codebase-memory-mcp"
db="$store_dir/$project_key.db"
mcp_json="$root/.mcp.json"

problems=""
add_problem() { problems="${problems}${problems:+; }$1"; }

# 1. The server has to be declared, or no session will ever see the tools.
if [ ! -f "$mcp_json" ]; then
  add_problem ".mcp.json is missing from the checkout root (it is gitignored, so a fresh clone has to recreate it)"
  binary=""
else
  binary="$(sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p' "$mcp_json" | head -1 | sed 's/\\\\/\//g')"
  if [ -z "$binary" ]; then
    add_problem ".mcp.json has no command path for the codebase-memory server"
  elif [ ! -f "$binary" ]; then
    add_problem "the binary .mcp.json points to does not exist: $binary"
  fi
fi

# 2. The graph for THIS checkout has to exist, or every graph query comes back empty.
if [ ! -f "$db" ]; then
  add_problem "no graph indexed for $project_key (expected $db) - run index_repository"
  db_info=""
else
  db_mb="$(( $(wc -c < "$db") / 1048576 ))"
  db_when="$(date -r "$db" '+%Y-%m-%d %H:%M' 2>/dev/null || echo unknown)"
  db_info="graph $db_mb MB, last indexed $db_when"
fi

# 3. Working tree moved on since the last index? Only a hint, never a failure.
stale=""
if [ -n "${db_info:-}" ] && command -v git >/dev/null 2>&1; then
  newer="$(git ls-files -z 2>/dev/null \
    | xargs -0 -r ls -1 --time-style=+%s -l 2>/dev/null \
    | awk -v t="$(date -r "$db" +%s 2>/dev/null || echo 0)" '$6 > t {n++} END {print n+0}')"
  [ "${newer:-0}" -gt 0 ] 2>/dev/null && stale=" - $newer tracked file(s) changed since then, consider reindexing"
fi

if [ -z "$problems" ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"codebase-memory READY for %s (%s%s). Per CLAUDE.md, code discovery starts in the graph: load the tools with ToolSearch and use search_graph / search_code / get_architecture before Grep or Glob. Confirm the tools really answer before relying on them; if they do not, say so to the user before anything else."},"suppressOutput":true}\n' \
    "$project_key" "$db_info" "$stale"
else
  printf '{"systemMessage":"codebase-memory NO disponible: %s","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"codebase-memory IS NOT USABLE: %s. CLAUDE.md requires the graph for code discovery, so tell the user this in your first reply, before starting any work, and say what you will do instead."}}\n' \
    "$problems" "$problems"
fi
