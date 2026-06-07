# Conventions & Coding Style

## Language and UI Text

- Code comments and AI context are English.
- Existing UI labels, logs, and user-facing messages are mostly Spanish.
  Preserve Spanish in UI surfaces unless the user asks for a translation.

## Python Style

- Use the standard library when sufficient.
- Keep functions small and procedural where the existing code is procedural.
- Preserve type hints already present in newer code.
- Prefer `Path` over raw string path manipulation.
- Keep Windows path behavior explicit; this project targets MT5 on Windows.
- Keep ownership split by domain. New behaviour should go into `ui/`, `ubs/`,
  or `portfolio_manager/` rather than growing `app_ui.py` or `ubs_agent.py`.
- For each substantial UI screen/tab, use a view/logic pair inside `ui/`:
  `ui/<screen>_view.py` for Tk widget/layout construction and
  `ui/<screen>_logic.py` for state transitions, persistence, validation,
  database queries, and long-running actions.
- New tabs must start with this view/logic pair.
- Treat `app_ui.py` as the composition/layout root. It may build Tk frames and
  wire commands, but tab behaviour belongs in the appropriate `ui/` mixin.
- `BASE_DIR` inside `ui/` modules must be:
  ```python
  BASE_DIR = Path(__file__).resolve().parent.parent  # project root
  if getattr(sys, "frozen", False):
      BASE_DIR = Path(sys.executable).resolve().parent
  ```
  Using only `.parent` would point to the `ui/` package directory, not root.
- UBS support modules live in `ubs/` (`ubs/memory.py`, `ubs/score.py`, etc.).
  Import them as `from ubs.memory import AgentMemory`, not the old root paths.
- Avoid broad refactors in `app_ui.py` unless the task is UI restructuring or
  an extraction that preserves behaviour.

## File Editing

- Do not rewrite generated runtime files unless the task explicitly concerns
  them.
- Be careful with `ui_settings.ini` and `.env`; they are local machine state.
- Generated Excel workbooks in `outputs/` are artifacts. Regenerate them for
  verification, but do not treat them as source unless the user wants the
  artifact.

## UI Conventions

### Custom widgets (defined in `app_ui.py`)

- `RoundedCard` — styled card container
- `RoundedButton` — primary CTA button (Type A in design system)
- `ToggleSwitch` — on/off toggle
- `ToolTip` — hover tooltip; access via `self._tooltip_cls(widget, text)`

`LIGHT_COLORS` and `DARK_COLORS` are the theme source of truth. Always read
colours through `self.colors["key"]` in mixin methods.

### Button types

All button decisions must follow `ai_context/09-design-system.md`. Summary:

| Type | Widget | When |
|---|---|---|
| A — CTA | `RoundedButton` | one main action per card |
| B — Bar compact | `tk.Button` themed | inside `panel_alt` action bars |
| C — Card content | `ttk.Button` with style | inside card body |

Never mix `ttk.Button` and `tk.Button` in the same `panel_alt` bar.

### Action bar pattern

```python
bar = tk.Frame(parent, bg=self.colors["panel_alt"])
bar.grid(row=N, column=0, sticky="ew", padx=20, pady=(4, 8))
bar.columnconfigure(0, weight=1)
# summary label left (col=0, weight=1), Type-B buttons right
```

### Treeview mandatory rules (all four must be applied)

1. `stretch=False` on every column (enables horizontal scroll).
2. `self._make_tree_sortable(tree)` always.
3. `self._attach_tree_scrollbars(frame, tree, row)` always.
4. Explicit `height=N` on every `ttk.Treeview`.

### Progress dialogs for blocking operations

Use a `tk.Toplevel` + `grab_set()` + background `threading.Thread` +
`queue.Queue` + `after(40, _poll)` pattern. Never block the event loop.
See `ui/ubs_results_logic.py:_export_ubs_results_run` for the canonical
implementation.

### Long-running actions

Long-running operations (compile, backtest, agent) must run in background
threads. Output is streamed line-by-line to the log panel via a thread-safe
queue and `after()` polling. The UI must not freeze.

## Logging

- `run_tests.py` writes both a timestamped log and `logs/last_run.log`.
- `compile_mq5.py` writes both a timestamped log and `logs/last_compile.log`.
- UI panels read/display these logs.
- Keep diagnostic messages specific; MT5 failures are often silent unless logs
  state which generated config/report path was expected.

## Packaging

`tools/build_installer.ps1` is the canonical packaging script. It:

1. Builds one-file executables with PyInstaller.
2. Stages runtime files.
3. Creates a payload ZIP.
4. Builds `MT5AutotesterSetup.exe` from `tools/installer_app.py`.
5. Writes `dist_installer/MT5AutotesterPortable.zip`.

If adding new runtime files required by packaged execution, update the staging
list in `tools/build_installer.ps1`.
