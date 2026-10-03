from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from manager_node_runtime.live_audit_sets import resolve_portfolio_set


class LiveAuditSetResolutionTests(unittest.TestCase):
    def test_duplicate_identical_sets_are_resolved_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "run_001" / "strategy.set"
            second = root / "run_002" / "strategy.set"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_text("StartLots=0.01\n", encoding="utf-8")
            second.write_text("StartLots=0.01\n", encoding="utf-8")

            resolved = resolve_portfolio_set(root, "strategy.set")

        self.assertEqual(resolved, first)

    def test_duplicate_different_sets_are_rejected_as_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "run_001" / "strategy.set"
            second = root / "run_002" / "strategy.set"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_text("StartLots=0.01\n", encoding="utf-8")
            second.write_text("StartLots=0.02\n", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "varios sets distintos"):
                resolve_portfolio_set(root, "strategy.set")


if __name__ == "__main__":
    unittest.main()
