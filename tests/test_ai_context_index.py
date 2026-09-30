from __future__ import annotations

import unittest

from tools.ai_context_index import missing


class AIContextIndexTests(unittest.TestCase):
    def test_every_note_is_indexed(self) -> None:
        self.assertEqual(missing(), [])


if __name__ == "__main__":
    unittest.main()
