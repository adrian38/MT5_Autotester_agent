# Environment, Settings & Runtime Files

## Environment File

`mt5_env.py` reads `.env` from the app base directory. In source mode, that is
the repository root. In PyInstaller frozen mode, it is the executable folder.

Supported variables:

| Variable | Purpose |
|----------|---------|
| `MT5_TERMINAL_PATH` | Preferred full path to `terminal64.exe`. |
| `MT5_PATH` | Legacy/alternate terminal path name. |
| `MT5_METAEDITOR_PATH` | Preferred full path to `MetaEditor64.exe`. |
| `METAEDITOR_PATH` | Legacy/alternate MetaEditor path name. |
| `TELEGRAM_BOT_TOKEN` | Optional Telegram bot token. |
| `TELEGRAM_CHAT_ID` | Optional Telegram chat ID. |

`.env.example` documents the expected format.

## UI Settings

`ui_settings.ini` stores runtime UI state. It is not a stable API, but common
keys include:

### `[Paths]`

- `mt5_path`
- `mt5_data_root`
- `metaeditor_path`
- `compile_root`
- `compile_file`
- `experts_root`
- `ubs_ex5_file`
- `set_files_root`
- `ubs_set_file`
- `template_path`
- `portfolio_input`
- `portfolio_output`

### `[General]`

- `recursive`
- `delay`
- `symbol_suffix_enabled`
- `symbol_suffix`
- `symbol_map_enabled`
- `symbol_map`
- `telegram_enabled`
- `portfolio_threshold`
- `theme`

Be careful when editing docs or defaults around this file: it may contain
machine-specific absolute paths.

## Text Path Files

The project also uses simple text files for source discovery:

- `compile_root.txt`: first active non-comment line points to `.mq5` source
  directory.
- `experts_root.txt`: first active non-comment line points to `.ex5` discovery
  directory.
- `experts_list.txt`: explicit `.ex5` paths relative to MT5 `MQL5/Experts`.

## Generated Directories

- `configs/`: generated Strategy Tester `.ini` files.
- `logs/`: timestamped run/compile logs and `last_run.log` /
  `last_compile.log`.
- `reports/`: copied MT5 `.htm/.html` reports, images, and `.set` files.
- `outputs/`: generated Excel workbooks.
- `outputs/ubs_memory.sqlite`: UBS runs/candidates plus `seed_scores`,
  `seed_overrides`, and `candidate_robustness`.
- `outputs/ubs_agent/<run>/robustness/`: copied accepted candidate `.set` files
  for out-of-sample robustness batches.
- `outputs/ubs_global_params.json`: global UBS EA parameter values.
- `outputs/ubs_mutation_overrides.json`: user mutability overrides for UBS
  parameters.
- `build_installer/`: temporary PyInstaller packaging workdir.
- `dist_installer/`: generated installer and portable ZIP.

## Python Packages

Normal source execution requires only a small set of third-party packages:

- `lxml`
- `openpyxl`
- `Pillow` optional for smoother UI rendering

Packaging additionally requires `PyInstaller`. Standard-library modules such as
`tkinter`, `sqlite3`, `urllib`, and `winreg` are provided by Python/Windows.

## Packaged/Frozen Runtime

Most scripts use:

```python
BASE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
```

Preserve this pattern for files that must work both from source and from
packaged `.exe` builds.
