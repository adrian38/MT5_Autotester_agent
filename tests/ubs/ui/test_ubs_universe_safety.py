import unittest

from ui.ubs_universe_table import UBSUniverseTableMixin


class _Tree:
    def __init__(self):
        self.rows = []

    def insert(self, *_args, **kwargs):
        self.rows.append(kwargs)
        return str(len(self.rows))


class _Status:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class _UniverseHarness(UBSUniverseTableMixin):
    def __init__(self):
        self.ubs_universe_assets_tree = _Tree()
        self.ubs_universe_paths = {}
        self.ubs_universe_checked = {"EURUSD"}
        self.ubs_timeframe_checked = {"H1"}
        self.status_text = _Status()
        self.refreshes = 0

    @staticmethod
    def _checkbox_text(value):
        return "x" if value else ""

    @staticmethod
    def _format_ubs_number(value):
        return "" if value is None else str(value)

    @staticmethod
    def _tag_for_weight(value):
        return "neutral" if value is None else "weighted"

    def _refresh_ubs_universe(self):
        self.refreshes += 1


class UBSUniverseSafetyTests(unittest.TestCase):
    def test_locked_render_hides_every_weight_derived_value(self):
        owner = _UniverseHarness()
        stat = {"best": 50.0, "tests": 4, "accepted": 2, "pending": 1}
        ranked = [(1.2, "FX", "EURUSD", (), stat, 1.2, 0.7, 0.8, 9, 3, 42.0)]

        owner._render_ubs_universe_asset_tree(
            ranked, {"EURUSD"}, set(), set(), show_weights=False,
        )

        values = owner.ubs_universe_assets_tree.rows[0]["values"]
        self.assertEqual(values[6:13], ("", "", "", "", "", "", ""))
        self.assertEqual(owner.ubs_universe_assets_tree.rows[0]["tags"], ("neutral",))

    def test_clear_selection_does_not_touch_persisted_evidence(self):
        owner = _UniverseHarness()

        owner._clear_universe_selection()

        self.assertEqual(owner.ubs_universe_checked, set())
        self.assertEqual(owner.ubs_timeframe_checked, set())
        self.assertEqual(owner.status_text.value, "Selección del Universo limpiada")
        self.assertEqual(owner.refreshes, 1)


if __name__ == "__main__":
    unittest.main()
