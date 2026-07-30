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

### Degradation-relative criteria (walk-forward efficiency)

In addition to the absolute floors above, the regression stage compares the
backward holdout against the candidate's own base window (the metrics stored in
`candidates.metrics_json`), following the standard out-of-sample rules of
retaining edge (walk-forward efficiency) and bounding drawdown expansion. Only
length-independent ratios are used, so windows of different length stay
comparable:

| Relative check | CLI flag | Default | Reason emitted |
|---|---|---:|---|
| Profit-factor efficiency `PF_reg / PF_base` | `--regression-min-pf-efficiency` | `>= 0.50` | `pf_efficiency` |
| Drawdown ratio `DD%_reg / DD%_base` | `--regression-max-dd-ratio` | `<= 2.0` | `dd_ratio` |

Guards (in `ubs/regression_rules.py:regression_degradation`):

- A threshold of `0` disables that check.
- If the candidate has no usable base metrics, the check is **skipped** (neutral,
  never a failure) — a missing base window is not a strategy loss.
- The profit-factor efficiency ignores the "no losing trades" sentinel
  (`PF >= 50`) on either side, since the ratio would be meaningless.
- The drawdown ratio floors the base denominator at `2.0` percentage points
  (`REGRESSION_DD_RATIO_FLOOR_PCT`) so a near-zero base drawdown does not
  explode the ratio and cause false failures.

These criteria only run when the report has trades and passes the technical
gates (match, dates, history). Their reasons merge with the absolute reasons, so
a candidate that passes every absolute floor can still be `rejected` if it
degraded too much from the base window (and the per-cause point penalties apply
the same way as the absolute reasons). Re-apply after tuning with
`--rescore-regression-only`; stored rows do not change until then.

## Points and statistical evidence

- `accepted`: `+80`.
- `rejected` or a valid matching `no_trades` report: base `-100` plus
  per-cause penalties, capped at `-60` additional (`-160` maximum total).
- `no_report`, `parse_error`, `report_mismatch`, `date_mismatch`, `no_history`:
  `0`; these are technical/retryable and do not create a probability trial.
- `watchdog_timeout`: `0`; it is technical and does not create a probability
  trial, but `--regression-pending-only` does not retry it automatically. Repair
  the MT5/history condition first, then use an explicit full or selected rerun.

The regression stage is the fifth factor in shared probability feedback. Its
prior is exactly `1.0` until the first real trial exists, preserving all legacy
probabilities. Once evidence exists, an empirical global prior (clamped to
`0.05..0.95`) and the existing shrinkage prevent tiny samples from dominating.

## MT5 runtime watchdog

Regression uses `Model=1`, but it is protected by the same general runner
watchdog as every other model. `run_tests.py` polls the fresh tester journal and
report artifacts every 10 seconds. The defaults are:

- `tester_stall_after=300`: after five minutes without journal or report
  progress, two consecutive checks terminate the process tree and retry the
  candidate once.
- `tester_max_runtime=1800`: absolute 30-minute ceiling for one backtest,
  including cases where the journal cannot be found.

Both values live in `[Multiterminal]` in `ui_settings.ini` and have CLI
overrides `--tester-stall-after` and `--tester-max-runtime`. A forced
termination saves `reports/<report>.watchdog_attempt_N.mt5log.txt` even when no
HTML report exists. Report discovery accepts only `.htm`, `.html`, and `.xml`,
so this diagnostic can never be parsed as a tester report. If the snapshot is
fresh and no report exists, regression records the neutral
`watchdog_timeout` state and keeps the TXT in `report_path` for inspection.
Snapshots containing symbol-specific `old tick` lines also store that history
signal in `details_json`. The normal pending/automatic continuation excludes
`watchdog_timeout` to prevent an unrepaired history defect from looping forever.

## CLI and UI

Run or resume:

```powershell
python .\ubs_agent.py --evaluate-regression --regression-run-id 1 --regression-pending-only --expert "C:\path\to\UBS.ex5" `
  --regression-min-pf-efficiency 0.5 --regression-max-dd-ratio 2.0
```

Reapply new thresholds without opening MT5:

```powershell
python .\ubs_agent.py --rescore-regression-only --regression-run-id 1
```

The `UBS Regresiva` tab exposes the same dates, thresholds, point values,
pending-only continuation, full rerun, rescore, report inspection, manual
verdicts, and optional automatic handoff after Final Tick 6M.
