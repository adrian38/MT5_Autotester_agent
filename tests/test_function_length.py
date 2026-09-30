from __future__ import annotations

import unittest

from tools.function_length import MAX_LINES, function_lengths, load_baseline
from tools.source_files import ratchet_offenders


class FunctionLengthTests(unittest.TestCase):
    def test_function_length_ratchet(self) -> None:
        failures = ratchet_offenders(function_lengths(), load_baseline(), MAX_LINES)
        self.assertEqual(failures, {"new": {}, "grown": {}, "stale": {}}, failures)


if __name__ == "__main__":
    unittest.main()
