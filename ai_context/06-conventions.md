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
- Keep ownership split by domain. New behavior should go into focused modules
  or mixins (`app_ui_*.py`, `ubs_*.py`, `portfolio_manager/*`) instead of
  growing `app_ui.py` or `ubs_agent.py`.
- For each substantial UI screen/tab, use at least two files:
  `app_ui_<screen>_view.py` for Tk widget/layout construction and
  `app_ui_<screen>_logic.py` for state transitions, persistence, validation,
  database queries, and long-running actions. This is the default architecture
  for the whole app, not an optional cleanup preference.
- New tabs must start with this view/logic pair. When modifying an existing tab
  that still mixes view and behavior, split the touched responsibility into the
  correct file as part of the change unless doing so would be riskier than the
  feature itself.
- Treat `app_ui.py` as the composition/layout root. It may build Tk frames and
  wire commands, but tab behavior, persistence, database queries, scoring,
  path inference, and long-running actions should live in the domain module
  that owns that feature.
- When touching a large legacy method, prefer extracting a coherent helper or
  mixin first if that reduces future maintenance risk without changing
  behavior.
- Avoid broad refactors in `app_ui.py` unless the task is UI restructuring or
  an extraction that preserves behavior.

## File Editing

- Do not rewrite generated runtime files unless the task explicitly concerns
  them.
- Be careful with `ui_settings.ini` and `.env`; they are local machine state.
- Generated Excel workbooks in `outputs/` are artifacts. Regenerate them for
  verification, but do not treat them as source unless the user wants the
  artifact.

## UI Conventions

`app_ui.py` defines custom Tkinter widgets:

- `RoundedCard`
- `RoundedButton`
- `ToggleSwitch`

It uses `LIGHT_COLORS` and `DARK_COLORS` dictionaries as the theme source of
truth. When modifying UI colors or layout, update through those theme values
where possible.

Long-running actions must run in background threads and report progress back
to Tkinter with thread-safe queue/`after(...)` patterns. Do not block the main
Tk event loop with MT5, MetaEditor, or Excel generation work.

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
