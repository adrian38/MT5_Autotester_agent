# MT5 Compile & Backtest Workflow

## Path Resolution

MT5 path lookup order in `run_tests.py`:

1. CLI `--mt5-path`.
2. Environment variables / `.env` via `mt5_env.py`:
   - `MT5_TERMINAL_PATH`
   - `MT5_PATH`
3. Known default install locations:
   - `C:\Program Files\RoboForex MT5 Terminal\terminal64.exe`
   - `C:\Program Files\MetaTrader 5\terminal64.exe`
   - `C:\Program Files (x86)\MetaTrader 5\terminal64.exe`
4. `shutil.which("terminal64.exe")`.
5. First default path as fallback.

MetaEditor lookup in `compile_mq5.py` follows a similar pattern:

1. CLI `--metaeditor-path`.
2. CLI `--mt5-path` with executable name changed to `MetaEditor64.exe`.
3. `MT5_METAEDITOR_PATH` / `METAEDITOR_PATH`.
4. `MT5_TERMINAL_PATH` / `MT5_PATH` with executable name changed.
5. Known default install locations.
6. `shutil.which("MetaEditor64.exe")`.

## Compile Flow

`compile_mq5.py`:

1. Determine source directory from CLI `--source-dir` or `compile_root.txt`.
2. If `--source-file` is provided, compile only that `.mq5`.
3. Otherwise compile root-level `*.mq5`. The current implementation accepts a
   `--recursive` flag but source discovery uses `glob("*.mq5")`; verify before
   relying on nested traversal.
4. Build a MetaEditor command:
   - `/compile:<source>`
   - `/log:<logs>/<stem>_compile.log`
   - `/inc:<MQL5 root>` when the source path is inside an `MQL5` tree.
5. Check that the `.ex5` exists and was updated.

## Backtest Flow

`run_tests.py`:

1. Resolve MT5 terminal and terminal data directory.
2. Detect matching running `terminal64.exe` processes unless
   `--skip-running-check` is used.
3. Resolve experts:
   - specific `--expert`;
   - `.ex5` files in `--experts-dir`;
   - first active line of `experts_root.txt`;
   - entries in `experts_list.txt`.
4. Resolve `.set` files from `--set-dir` and/or repeated `--set-file`.
5. Load `tester_template.ini`.
6. For each expert or expert/set combination:
   - copy `.set` files into MT5 tester profile folders;
   - optionally infer `Symbol` and `Period` from the `.set`;
   - apply symbol suffix and symbol map;
   - create a generated `.ini` under `configs/`;
   - launch `terminal64.exe /config:<ini>`.
7. Locate reports in MT5 data/install report locations.
8. Copy report files into `reports/`.

## Template Contract

`tester_template.ini` must contain a `[Tester]` section. The script overwrites
or fills at least:

- `Expert`
- `Report`
- `Symbol` when suffix/map/set inference applies
- `Period` when set inference applies

Common template fields:

```ini
[Tester]
Expert=
Symbol=XAUUSD
Period=M30
Model=1
FromDate=2020.01.01
ToDate=2026.05.22
Deposit=1000
Currency=EUR
Leverage=1:500
Optimization=0
Visual=0
ReplaceReport=1
ShutdownTerminal=1
Report=
```

## Symbol / Timeframe Inference

`run_tests.py` contains heuristics for:

- Forex symbols.
- XAUUSD/GOLD names.
- BTCUSD.
- US100/US30/US500/DAX indices.
- XTIUSD/WTI/crude oil.
- Timeframes such as M1, M5, M15, M30, H1, H4, D1, W1.

Broker symbol rewriting can be configured through a `source=target` symbol map
from the UI or `--symbol-map`.

## MT5 Gotchas

- MT5 can ignore `/config` when the same terminal is already open. Preserve the
  running-terminal detection unless intentionally changing this behavior.
- Reports may be written to the terminal data folder or install directory;
  `run_tests.py` searches multiple locations.
- MT5 report files may be UTF-16 HTML. Do not parse them as plain UTF-8 text
  without checking encoding.

