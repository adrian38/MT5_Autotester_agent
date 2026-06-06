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

Run UBS agent:

```powershell
python .\ubs_agent.py --dry-run
python .\ubs_agent.py --execute-backtests --expert "C:\path\to\Ultimate Breakout System_4.3.ex5"
python .\ubs_agent.py --retry-candidate-id 262 --expert "C:\path\to\Ultimate Breakout System_4.3.ex5"
python .\ubs_agent.py --retry-candidate-id 262 --expert "C:\path\to\Ultimate Breakout System_4.3.ex5" --dry-run
python .\ubs_agent.py --retry-run-id 1 --retry-mismatch-run --expert "C:\path\to\Ultimate Breakout System_4.3.ex5" --dry-run
python .\ubs_agent.py --evaluate-seeds --source-dir ".\sets\ubs_ready" --expert "C:\path\to\Ultimate Breakout System_4.3.ex5" --dry-run
python .\ubs_agent.py --evaluate-robustness --robust-run-id 1 --expert "C:\path\to\Ultimate Breakout System_4.3.ex5" --from-date 2025.01.01 --to-date 2026.06.01 --dry-run
python .\ubs_agent.py --force-unseeded-universe --dry-run
```

Audit current UBS memory and weights:

```powershell
python .\tools\ubs_memory_audit.py
python .\tools\ubs_memory_audit.py --strict
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

For UBS agent changes:

- Run `python -m py_compile run_tests.py ubs_agent.py app_ui.py`.
- Run `python .\tools\ubs_memory_audit.py` after scoring, seed evaluation,
  robustness, or weight changes. It checks run status counts, seed readiness,
  stale/missing reports, old robustness bonus defaults, JSON metrics, and
  current asset/timeframe/mutation weights using the same `AgentMemory`
  formula as the app.
- For symbol/timeframe inference changes, run a dry retry of a known mismatch
  and confirm the generated `.ini` has the intended `Symbol` and `Period`.
  Include a case whose source seed name contains a loose alias, e.g.
  `JP225Cash/H4/...GOLD...set`, and confirm the generated `.ini` uses
  `Symbol=.JP225Cash`, not `XAUUSD`.
- For seed evaluation changes, run `ubs_agent.py --evaluate-seeds --dry-run`
  against a small seed folder.
- Confirm a UBS seed with missing/unknown symbol or timeframe is stored as
  `report_mismatch` before `run_tests.py` is launched. This case must not
  create a backtest job.
- Confirm manual fixes saved in the UI `UBS Seeds` tab are written to
  `seed_overrides` and are applied by both seed evaluation and normal UBS
  generation.
- For seed reset/weight changes, use `UBS Seeds` -> `Resetear evaluación` and
  confirm active seed rows become `pending`, report paths/scores are cleared,
  Universe weights are hidden, and `UBS Universo` -> `Calcular pesos` refuses
  to unlock while non-ready active seed rows remain.
- If multiterminal behavior changed, run a dry test with
  `run_tests.py --multi-terminal --terminals-config ui_settings.ini
  --max-workers 2 --dry-run` and verify the queue splits without launching MT5.
- Confirm `outputs/ubs_memory.sqlite` candidate statuses remain terminal after
  completed generations: `accepted`, `rejected`, `report_mismatch`,
  `no_report`, `parse_error`, or `no_trades`.
- Confirm `report_mismatch` rows do not feed `asset_feedback`,
  `timeframe_feedback`, seed feedback, or Universe tab weights.
- Confirm active seed rows with valid scored reports (`accepted`, `rejected`,
  `no_trades`) feed `asset_feedback` and `timeframe_feedback` at the same base
  strength as generated candidates. They should not be scaled down as a prior.
  They also should not receive robustness/date bonus unless an explicit seed
  bonus feature exists.
- After retrying a `report_mismatch` or `no_report` candidate, confirm the
  original row is updated. If it becomes `rejected`, it should contribute
  through `ubs.weights`: raw score minus rejection/cause penalties and without
  accepted bonus.
- For robustness changes, run `ubs_agent.py --evaluate-robustness --dry-run`
  against a run with accepted candidates. Confirm copied sets are created under
  `outputs/ubs_agent/<run>/robustness/...`, and confirm real/non-dry results
  write `candidate_robustness` without modifying base candidate scores.
- Confirm robustness bonus math is consistent in `AgentMemory` and `UBS
  Universo`: robust `accepted` adds positive bonus, robust `rejected` adds
  negative bonus plus OOS cause penalties, and
  `no_report`/`parse_error`/`report_mismatch`/`no_trades` are neutral for the
  robustness adjustment.
- For unseeded-universe exploration changes, use a fixed `--random-seed` and
  confirm generated candidates include policy labels `asset_unseeded_force` or
  `tf_unseeded_force` when `--force-unseeded-universe` is active.
- If testing real MT5 retry, close MT5 first, select a `mismatch reporte` row
  in the UI, and use `Reprobar mismatch` for one candidate or `Reprobar run`
  for all mismatches in the visible run.

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

For dependency changes:

- Runtime third-party packages should stay limited to `lxml`, `openpyxl`, and
  optional `Pillow` unless a change clearly requires more.
- `PyInstaller` is a packaging dependency only.
- Do not add standard-library modules such as `tkinter`, `sqlite3`, `urllib`,
  or `winreg` to pip requirements.

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

Inspect UBS memory:

```powershell
@'
import sqlite3
conn = sqlite3.connect("outputs/ubs_memory.sqlite")
conn.row_factory = sqlite3.Row
for row in conn.execute("select status, count(*) n from candidates group by status order by status"):
    print(dict(row))
conn.close()
'@ | python -
```

Known UBS memory snapshot after run #4 on 2026-06-06:

- runs: `#2`, `#3`, `#4`; visible/latest run: `#4`.
- total candidates: `900`; every run has `300` candidates.
- run #4 candidates: `185 accepted`, `85 rejected`, `30 no_trades`,
  `0 no_report/report_mismatch/parse_error`.
- run #4 robustness: `185` accepted candidates evaluated, `143 OK`, `42 FAIL`.
- active seeds: `318`; `303` have valid scored reports (`215 accepted`,
  `88 rejected`), and `15` are `disabled_symbol`.
- active seeds with valid scored reports contribute to weights at the same base
  strength as generated candidates.

This snapshot is historical only; always query `outputs/ubs_memory.sqlite` for
the current state before drawing conclusions.
