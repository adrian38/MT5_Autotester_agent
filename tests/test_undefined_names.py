from __future__ import annotations

import unittest

from tools.source_files import iter_python_files
from tools.undefined_names import ALLOWED_STAR_IMPORTS, relative_path, unresolved


class UndefinedNamesTests(unittest.TestCase):
    def test_owned_python_has_no_undefined_globals_or_unlisted_star_imports(self) -> None:
        stars = set()
        failures = {}
        for path in iter_python_files():
            missing = unresolved(path.read_text(encoding="utf-8"))
            if missing is None:
                stars.add(relative_path(path))
            elif missing:
                failures[relative_path(path)] = missing
        self.assertEqual(failures, {})
        self.assertEqual(stars, set(ALLOWED_STAR_IMPORTS))


if __name__ == "__main__":
    unittest.main()
