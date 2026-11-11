import json
import sqlite3
import unittest

from ubs.db import configure_sqlite_connection
from ubs.memory import AgentMemory, variant_from_candidate_row
from ui.ubs_universe_logic import UBSUniverseLogicMixin


class Scanner(UBSUniverseLogicMixin):
    pass


def candidate_row(mutated_keys: str) -> dict:
    return {
        "seed_path": "seed.set",
        "set_path": "candidate.set",
        "symbol": "US500",
        "target_symbol": "USDCAD",
        "period": "H1",
        "family": "family",
        "run_strategy": "1",
        "mutated_keys": mutated_keys,
        "missing_lot_keys": "",
        "policy": "asset_universe_explore",
        "timeframe_keys": "",
    }


class HistoricalReadNormalizationTests(unittest.TestCase):
    def test_force_symbol_is_not_learned_as_a_mutation_key(self) -> None:
        rows = [
            {
                "run_id": 1,
                "seed_path": "seed.set",
                "target_symbol": "USDCAD",
                "period": "H1",
                "family": "family",
                "mutated_keys": "ForceSymbol",
                "mutation_details_json": json.dumps(
                    [{"kind": "symbol_retarget", "key": "ForceSymbol", "old": "US500", "new": "USDCAD"}]
                ),
                "status": "rejected",
                "robust_status": "",
                "final_tick_status": "",
                "final_tick_6m_status": "",
                "regression_status": "",
            }
        ]
        memory = object.__new__(AgentMemory)
        memory._candidate_feedback_rows = lambda: rows

        self.assertEqual(memory.mutation_feedback_signals(), {})
        self.assertEqual(memory.mutation_direction_feedback_signals(), {})

    def test_legacy_variant_keeps_real_keys_but_drops_force_symbol(self) -> None:
        variant = variant_from_candidate_row(candidate_row("ForceSymbol;ATR_Period"))
        self.assertEqual(variant.mutated_keys, ("ATR_Period",))


class MutationKeyDatabaseRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = configure_sqlite_connection(sqlite3.connect(":memory:"))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "create table candidates (id integer primary key, run_id integer, mutated_keys text)"
        )
        self.conn.executemany(
            "insert into candidates values (?,?,?)",
            [
                (1, 10, "ForceSymbol"),
                (2, 10, "ForceSymbol;ATR_Period"),
                (3, 11, "ATR_Period"),
                (4, 11, ""),
            ],
        )
        self.scanner = Scanner()

    def tearDown(self) -> None:
        self.conn.close()

    def test_scan_and_apply_remove_only_force_symbol(self) -> None:
        changes = self.scanner.scan_non_parameter_mutation_keys(self.conn)
        self.assertEqual([item["candidate_id"] for item in changes], [1, 2])
        self.assertEqual([item["mutated_keys"] for item in changes], ["", "ATR_Period"])

        updated = self.scanner.apply_non_parameter_mutation_key_updates(self.conn, changes)

        self.assertEqual(updated, 2)
        stored = dict(self.conn.execute("select id, mutated_keys from candidates").fetchall())
        self.assertEqual(stored, {1: "", 2: "ATR_Period", 3: "ATR_Period", 4: ""})

    def test_apply_does_not_overwrite_a_concurrent_change(self) -> None:
        changes = self.scanner.scan_non_parameter_mutation_keys(self.conn)
        self.conn.execute("update candidates set mutated_keys='Exit_stop' where id=1")

        updated = self.scanner.apply_non_parameter_mutation_key_updates(self.conn, changes)

        self.assertEqual(updated, 1)
        self.assertEqual(
            self.conn.execute("select mutated_keys from candidates where id=1").fetchone()[0],
            "Exit_stop",
        )


if __name__ == "__main__":
    unittest.main()
