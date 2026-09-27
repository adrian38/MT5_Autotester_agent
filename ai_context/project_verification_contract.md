# Project verification contract

The IC checkout has an enforceable repository gate in
`python -m tools.verify_project`. Its quick mode checks undefined global names,
the 60-line function ratchet, the 600-line file ratchet, the `ai_context` index,
and the guard tests. The default mode also runs the full unittest suite.
When the sibling manager checkout is mounted, the gate also compares the two
guided-protocol modules after normalizing Git line endings.

The size baselines describe inherited debt at the moment the contract was
introduced. They are not allowances for new code: an existing item may stay
the same or shrink, and its entry must be removed once it reaches the limit.
The baseline must never be enlarged to make a failure green.

The source inventory covers root Python entry points and the owned packages
`manager_node_runtime`, `parsers`, `portfolio_manager`, `tests`, `tools`, `ubs`
and `ui`. Generated/runtime directories are deliberately outside this static
inventory and must be preserved.

The verification command snapshots Git `HEAD` and tracked worktree status. It
fails if another session changes either while verification is running, so a
green result belongs to one stable source state.

This contract does not operate MT5. Starting terminals, backtests, the desktop
application, UBS cycles, memory rewrites or node restarts still requires an
explicit user request.
