from __future__ import annotations

import unittest

from tools.file_length import MAX_LINES, file_lengths, load_baseline
from tools.source_files import ratchet_offenders


class FileLengthTests(unittest.TestCase):
    def test_file_length_ratchet(self) -> None:
        failures = ratchet_offenders(file_lengths(), load_baseline(), MAX_LINES)
        self.assertEqual(failures, {"new": {}, "grown": {}, "stale": {}}, failures)


if __name__ == "__main__":
    unittest.main()
