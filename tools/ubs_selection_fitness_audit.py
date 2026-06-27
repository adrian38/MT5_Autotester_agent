from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys


BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ubs.account import (
    ACCOUNT_TYPES,
    BROKERS,
    DEFAULT_ACCOUNT_TYPE,
    DEFAULT_BROKER,
    account_memory_path,
    migrate_legacy_account_storage,
    normalize_account_type,
    normalize_broker,
)
from ubs.db import connect_memory
from ubs.selection import SelectionFitnessModel, finalized_six_month_label


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Valida selection_fitness dejando un run completo fuera.")
    parser.add_argument("--broker", choices=BROKERS, default=DEFAULT_BROKER)
    parser.add_argument("--account-type", choices=ACCOUNT_TYPES, default=DEFAULT_ACCOUNT_TYPE)
    parser.add_argument("--memory", default="")
    parser.add_argument("--holdout-run-id", type=int)
    return parser.parse_args()


def _auc(labels: list[int], scores: list[float]) -> float:
    ordered = sorted(enumerate(scores), key=lambda item: item[1])
    ranks = [0.0] * len(scores)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + 1 + end) / 2.0
        for original_index, _score in ordered[index:end]:
            ranks[original_index] = rank
        index = end
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        return 0.5
    positive_rank_sum = sum(rank for rank, label in zip(ranks, labels) if label)
    return (positive_rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def main() -> int:
    args = parse_args()
    broker = normalize_broker(args.broker)
    account_type = normalize_account_type(args.account_type, broker)
    migrate_legacy_account_storage(BASE_DIR, account_type, broker)
    memory_path = Path(args.memory).expanduser() if args.memory else account_memory_path(BASE_DIR, account_type, broker)
    if not memory_path.exists():
        print(f"ERROR: no existe memoria {memory_path}")
        return 1
    conn = connect_memory(memory_path, enable_wal=False)
    try:
        holdout_run_id = args.holdout_run_id
        if holdout_run_id is None:
            row = conn.execute(
                """
                select c.run_id
                from candidates c
                join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
                group by c.run_id
                order by c.run_id desc limit 1
                """
            ).fetchone()
            if row is None:
                print("ERROR: no hay runs con Final Tick 6M")
                return 1
            holdout_run_id = int(row["run_id"])
        rows = conn.execute(
            """
            select c.run_id, c.period, c.score, c.metrics_json, c.status,
                   cr.status robust_status,
                   ft.status final_tick_status,
                   ft6.status final_tick_6m_status
            from candidates c
            left join candidate_robustness cr on cr.candidate_id=c.id
            left join candidate_final_tick ft on ft.candidate_id=c.id
            left join candidate_final_tick_6m ft6 on ft6.candidate_id=c.id
            where c.status='accepted' and c.score is not null
            """
        ).fetchall()
    finally:
        conn.close()

    model = SelectionFitnessModel.train(row for row in rows if int(row["run_id"]) != holdout_run_id)
    if model is None:
        print("ERROR: historial finalizado insuficiente para entrenar")
        return 1
    holdout = [row for row in rows if int(row["run_id"]) == holdout_run_id and finalized_six_month_label(row) is not None]
    labels = [int(finalized_six_month_label(row) or 0) for row in holdout]
    scores = [model.predict(row["score"], row["metrics_json"], row["period"]).probability for row in holdout]
    print(f"Memoria: {memory_path}")
    print(f"Holdout run: #{holdout_run_id}")
    print(f"Training: rows={model.training_rows} positives={model.positive_rows}")
    print(f"Holdout: rows={len(holdout)} positives={sum(labels)}")
    print(f"AUC: {_auc(labels, scores):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
