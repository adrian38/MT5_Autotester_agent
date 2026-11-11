import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.rescore_generalization_v2 import database_summary


class GeneralizationV2MigrationTests(unittest.TestCase):
    def test_database_summary_accepts_legacy_robustness_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir) / "legacy.sqlite"
            conn = sqlite3.connect(memory)
            try:
                conn.execute(
                    """
                    create table candidate_robustness (
                        candidate_id integer primary key,
                        status text not null,
                        metrics_json text
                    )
                    """
                )
                conn.executemany(
                    "insert into candidate_robustness values (?, ?, ?)",
                    [
                        (1, "accepted", '{"score_formula_version": "2"}'),
                        (2, "rejected", "legacy-invalid-json"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            summary = database_summary(memory)

            self.assertEqual(summary["statuses"], {"accepted": 1, "rejected": 1})
            self.assertEqual(summary["score_v2"], 1)
            self.assertEqual(summary["degradation_v2"], 0)
            self.assertEqual(summary["integrity"], "ok")

    def test_database_summary_counts_current_degradation_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = Path(temp_dir) / "current.sqlite"
            conn = sqlite3.connect(memory)
            try:
                conn.execute(
                    """
                    create table candidate_robustness (
                        candidate_id integer primary key,
                        status text not null,
                        metrics_json text,
                        degradation_json text not null default ''
                    )
                    """
                )
                conn.execute(
                    "insert into candidate_robustness values (?, ?, ?, ?)",
                    (
                        1,
                        "accepted",
                        '{"score_formula_version": "2"}',
                        '{"version": "robustness_degradation_v2"}',
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            summary = database_summary(memory)

            self.assertEqual(summary["score_v2"], 1)
            self.assertEqual(summary["degradation_v2"], 1)
            self.assertEqual(summary["integrity"], "ok")


if __name__ == "__main__":
    unittest.main()
