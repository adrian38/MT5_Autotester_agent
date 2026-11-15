from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from manager_node_runtime.node import JobController, completed_runs_snapshot


class ManagerNodeRunsPaginationTests(unittest.TestCase):
    def test_every_run_is_reachable_through_fixed_size_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            memory = project / "runs.sqlite"
            with closing(sqlite3.connect(memory)) as conn:
                conn.executescript("""
                    create table runs(id integer primary key, created_at text, generations integer, hidden integer default 0);
                    create table candidates(id integer primary key, run_id integer, generation integer, status text);
                """)
                conn.executemany(
                    "insert into runs values(?, '2026-08-29', 1, 0)",
                    ((run_id,) for run_id in range(1, 206)),
                )
                conn.executemany(
                    "insert into candidates values(?, ?, 1, 'accepted')",
                    ((run_id, run_id) for run_id in range(1, 206)),
                )
                conn.commit()

            self.assertEqual(
                [run["id"] for run in completed_runs_snapshot(memory, 100, 100)],
                list(range(105, 5, -1)),
            )
            controller = JobController(
                {"node_id": "ic", "project_dir": str(project), "memory_path": str(memory)},
                project / "manager_node.json",
            )
            first = controller.runs(100, 0)
            last = controller.runs(100, 200)

            self.assertEqual(first["runs"][0]["id"], 205)
            self.assertTrue(first["pagination"]["has_more"])
            self.assertEqual(first["pagination"]["next_offset"], 100)
            self.assertEqual([run["id"] for run in last["runs"]], [5, 4, 3, 2, 1])
            self.assertFalse(last["pagination"]["has_more"])
            self.assertIsNone(last["pagination"]["next_offset"])


if __name__ == "__main__":
    unittest.main()
