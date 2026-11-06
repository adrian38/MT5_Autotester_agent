# UBS Seeds Final Tick

The desktop sidebar has two seed-specific validation screens immediately below
`UBS Seeds`:

- `UBS Seeds Final Tick`: short OHLC versus real-tick probe.
- `UBS Seeds Final Tick 6M`: six-month validation after an eligible probe.

These screens run directly on active, base-accepted `seed_scores` rows. They
reuse the candidate Final Tick runner, date validation, MT5 models, history
quality rules, similarity thresholds, retry handling, and report reconciliation,
but an adapter persists the result in `seed_final_tick` or
`seed_final_tick_6m`. No candidate row is created.

Seed rows do not have candidate mutation metadata. The adapter query supplies
empty `mutated_keys`, `missing_lot_keys`, and `timeframe_keys` plus the
`seed_final_tick` policy when it presents a seed to the shared runner; no
mutation history is inferred or invented.

The 6M stage accepts only seeds whose current short probe is `accepted` or
`pending_ohlc_trades`. Both tables store the seed mtime and size used for the
evaluation; a changed source seed therefore appears pending rather than reusing
stale evidence. A failed short probe removes downstream 6M evidence.

Seed Final Tick is currently audit-only. It does not change Universe weights,
does not add candidate lifecycle trials, and does not make a seed portfolio
eligible. Seed deletion and the Seeds reset action remove both stage rows.

Runtime artifacts are isolated under the active account output directory:

```text
outputs/ubs_agent/{BROKER}/{ACCOUNT}/seed_final_tick_runs/
    final_tick/run_{cohort_id}_{pending|all}/
    final_tick_6m/run_{cohort_id}_{pending|all}/
```

`cohort_id` is a deterministic hash of the eligible seed IDs and their current
mtime/size snapshots. It stays stable for resume/reconciliation, while a source
or eligibility change gets a fresh work directory and cannot execute stale
copied sets.

CLI entry point:

```text
ubs_agent.py --evaluate-seed-final-tick --final-tick-stage probe|six_month ...
```
