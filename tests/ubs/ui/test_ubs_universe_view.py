import unittest
from unittest.mock import patch

from ui.ubs_universe_view import UBSUniverseViewMixin


class _Label:
    def __init__(self, *_args, **_kwargs):
        self.grid_options = None

    def grid(self, **kwargs):
        self.grid_options = kwargs

    def bind(self, *_args):
        return None


class UBSUniverseViewTests(unittest.TestCase):
    def test_legend_uses_row_below_tables(self):
        owner = UBSUniverseViewMixin()
        owner.ubs_timeframe_summary = object()
        label = _Label()

        with patch("ui.ubs_universe_view.ttk.Label", return_value=label):
            owner._build_ubs_universe_legend(object())

        self.assertEqual(label.grid_options["row"], 5)


if __name__ == "__main__":
    unittest.main()
