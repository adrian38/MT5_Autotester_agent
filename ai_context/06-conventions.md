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
- Avoid broad refactors in `app_ui.py` unless the task is UI restructuring.

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

