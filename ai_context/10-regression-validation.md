# UBS Backward Regression Validation

## Purpose

The regression stage asks a different question from Final Tick: after a
candidate survives the current base, OOS, and Final Tick 6M pipeline, did the
same fixed strategy also behave coherently in an older market regime? The
default holdout is `2017.01.01 -> 2019.12.31` and uses MT5 `Model=1` (1 minute
OHLC) for a fast, broad three-year check.

This stage is backward and out of sample relative to the recent selection
pipeline. It must never optimize parameters on the old interval. It is evidence
for learning and ranking, not a replacement for the real-tick 6M portfolio gate.

## Official MT5 basis

- [MQL5 testing modes](https://www.mql5.com/en/docs/runtime/testing): 1 minute
  OHLC generates only the minute open, high, low, and close ticks, so it is fast
  but approximate; unusually strong results should still be checked with Every
  Tick/real ticks.
- [MetaTrader 5 strategy testing help](https://www.metatrader5.com/en/terminal/help/algotrading/testing):
  the tester supports custom date ranges and describes forward/out-of-sample
  validation as protection against parameter fitting.
- [Historical data preparation](https://www.metatrader5.com/en/terminal/help/algotrading/test_preparation):
  MT5 loads data before the configured start and can shift the effective start
  when requested history is unavailable. Therefore the stored report dates are
  checked exactly and missing/shifted history is technical, not a strategy loss.
- [Testing statistics](https://www.mql5.com/en/docs/constants/environment_state/Statistics):
  official definitions for profit factor, recovery factor, trades, and drawdown.
- [MQL5 walk-forward analysis](https://www.mql5.com/en/articles/3279): evaluate
  consistency on unseen segments and inspect several dimensions (profitability,
  drawdown, recovery, and sample size), not net profit alone.

## Eligibility and persistence

Only rows satisfying all of these conditions are eligible:

1. `candidates.status='accepted'`
2. `candidate_robustness.status='accepted'`
3. `candidate_final_tick_6m.status='accepted'`

The result is stored independently in `candidate_regression`. Losing an
upstream acceptance deletes the now-stale regression row. The executor copies
sets under the selected run's `regression_2017_2019/` directory and always calls
`run_tests.py --model 1` with the regression date overrides.

## Default decision rule

| Metric | Default |
|---|---:|
| Normalized net profit | `> 0` |
| Profit factor | `>= 1.10` |
| Trades | `>= 36` |
| W1 trades | `>= 12` |
| MN trades | `>= 4` |
| Drawdown | `<= 30%` |
| Recovery factor | `>= 0.75` |
| Positive-month ratio | `>= 0.50` |

The lower PF/recovery floors and higher DD ceiling relative to current-period
selection recognize that this is an older regime and an approximate OHLC
model. The minimum sample and monthly consistency checks prevent a small number
of lucky trades from passing.

## Points and statistical evidence

- `accepted`: `+80`.
- `rejected` or a valid matching `no_trades` report: base `-100` plus
  per-cause penalties, capped at `-60` additional (`-160` maximum total).
- `no_report`, `parse_error`, `report_mismatch`, `date_mismatch`, `no_history`:
  `0`; these are technical/retryable and do not create a probability trial.

The regression stage is the fifth factor in shared probability feedback. Its
prior is exactly `1.0` until the first real trial exists, preserving all legacy
probabilities. Once evidence exists, an empirical global prior (clamped to
`0.05..0.95`) and the existing shrinkage prevent tiny samples from dominating.

## CLI and UI

Run or resume:

```powershell
python .\ubs_agent.py --evaluate-regression --regression-run-id 1 --regression-pending-only --expert "C:\path\to\UBS.ex5"
```

Reapply new thresholds without opening MT5:

```powershell
python .\ubs_agent.py --rescore-regression-only --regression-run-id 1
```

The `UBS Regresiva` tab exposes the same dates, thresholds, point values,
pending-only continuation, full rerun, rescore, report inspection, manual
verdicts, and optional automatic handoff after Final Tick 6M.
