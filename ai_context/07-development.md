# Development Guide

## Common Commands

Run the desktop UI:

```powershell
python .\app_ui.py
```

Compile sources:

```powershell
python .\compile_mq5.py
python .\compile_mq5.py --source-dir "C:\path\to\MQL5\Experts"
python .\compile_mq5.py --source-dir "C:\path\to\MQL5\Experts" --source-file "MyEA.mq5"
python .\compile_mq5.py --dry-run
```

Run backtests:

```powershell
python .\run_tests.py
python .\run_tests.py --experts-dir "C:\path\to\MQL5\Experts"
python .\run_tests.py --expert "MyEA.ex5" --set-dir ".\sets"
python .\run_tests.py --dry-run
```

Compile then backtest:

```powershell
python .\compile_and_backtest.py
python .\compile_and_backtest.py --source-dir "C:\path\to\MQL5\Experts" --dry-run
```

Build installer:

```powershell
.\tools\build_installer.ps1
```

## Verification Checklist

For compile/backtest changes:

- Run the relevant script with `--dry-run`.
- Confirm generated `configs/*.ini` has the expected `Expert`, `Symbol`,
  `Period`, `Report`, and optional set fields.
- If testing real MT5 execution, ensure MT5 is fully closed first.
- Check `logs/last_run.log` or `logs/last_compile.log`.

For portfolio changes:

- Parse at least one real report and confirm symbol/timeframe/deposit/trade
  counts are non-empty.
- Generate `ALL_STRATEGIES.xlsx`.
- Inspect `INDEX` with `openpyxl`.
- Run the other Portfolio Manager generators if shared parser logic changed.

For UI changes:

- Start `python app_ui.py`.
- Exercise the changed panel manually.
- Verify long-running actions keep the UI responsive.
- Check both light and dark theme if colors/layout changed.

For packaging changes:

- Run `tools/build_installer.ps1`.
- Confirm generated files exist in `dist_installer/`.
- Verify new required runtime files are included in the stage directory.

## Useful Debug Snippets

Parse reports quickly:

```powershell
python - <<'PY'
from pathlib import Path
from portfolio_manager.mt5_report import parse_report

for path in sorted(Path("reports").glob("*.htm"))[:3]:
    report = parse_report(path)
    print(path.name, report.symbol, report.timeframe, report.initial_deposit, len(report.trades), len(report.raw_deals))
PY
```

Generate all Portfolio Manager outputs:

```powershell
python - <<'PY'
from pathlib import Path
from portfolio_manager.generator import (
    generate_workbook,
    generate_drawdown_workbook,
    generate_portfolio_drawdown_workbook,
    generate_portfolio_valley_drawdown_workbook,
    generate_top_portfolio_valleys_workbook,
    generate_dd_threshold_workbook,
)

reports = Path("reports")
outputs = Path("outputs")
tasks = [
    ("ALL_STRATEGIES.xlsx", lambda: generate_workbook(reports, outputs / "ALL_STRATEGIES.xlsx")),
    ("ALL_STRATEGIES_DD.xlsx", lambda: generate_drawdown_workbook(reports, outputs / "ALL_STRATEGIES_DD.xlsx")),
    ("PORTFOLIO_DD.xlsx", lambda: generate_portfolio_drawdown_workbook(reports, outputs / "PORTFOLIO_DD.xlsx")),
    ("PORTFOLIO_VALLEY_DD.xlsx", lambda: generate_portfolio_valley_drawdown_workbook(reports, outputs / "PORTFOLIO_VALLEY_DD.xlsx")),
    ("PORTFOLIO_TOP5_VALLEYS.xlsx", lambda: generate_top_portfolio_valleys_workbook(reports, outputs / "PORTFOLIO_TOP5_VALLEYS.xlsx")),
    ("DD_THRESHOLD.xlsx", lambda: generate_dd_threshold_workbook(reports, outputs / "DD_THRESHOLD.xlsx", 50)),
]
for name, fn in tasks:
    result = fn()
    print(name, len(result))
PY
```

Compile syntax only:

```powershell
python -m compileall .
```

