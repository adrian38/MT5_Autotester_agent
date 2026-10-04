from __future__ import annotations

import unittest
from unittest.mock import patch

from ui.app_state_portfolio import AppPortfolioStateMixin


class _Variable:
    def __init__(self, *, value=None) -> None:
        self.value = value

    def get(self):
        return self.value


class _State(AppPortfolioStateMixin):
    @staticmethod
    def _saved_int(value, default):
        return int(value) if value is not None else default

    @staticmethod
    def _bool_setting(value, default):
        return default if value is None else str(value).casefold() in {"1", "true", "yes"}


class AppPortfolioStateTests(unittest.TestCase):
    @patch("ui.app_state_portfolio.tk.BooleanVar", _Variable)
    @patch("ui.app_state_portfolio.tk.IntVar", _Variable)
    @patch("ui.app_state_portfolio.tk.StringVar", _Variable)
    def test_monthly_type_defaults_to_initialized_general_portfolio_type(self) -> None:
        state = _State()
        state._init_portfolio_inputs({"ubs_portfolio_type": "Agresivo"})
        state._init_monthly_portfolio_inputs({})
        self.assertEqual(state.ubs_monthly_portfolio_type.get(), "Agresivo")

    @patch("ui.app_state_portfolio.tk.BooleanVar", _Variable)
    @patch("ui.app_state_portfolio.tk.IntVar", _Variable)
    @patch("ui.app_state_portfolio.tk.StringVar", _Variable)
    def test_saved_monthly_type_overrides_the_general_type(self) -> None:
        state = _State()
        state._init_portfolio_inputs({"ubs_portfolio_type": "Agresivo"})
        state._init_monthly_portfolio_inputs({"ubs_monthly_portfolio_type": "Conservador"})
        self.assertEqual(state.ubs_monthly_portfolio_type.get(), "Conservador")


if __name__ == "__main__":
    unittest.main()
