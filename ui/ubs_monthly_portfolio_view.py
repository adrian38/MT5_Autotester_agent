from __future__ import annotations

from tkinter import ttk

from ui.ubs_portfolio_view import UBSPortfolioViewMixin


class _MonthlyPortfolioScreenAdapter:
    """Reuse the portfolio layout while keeping an independent widget namespace."""

    def __init__(self, app: object) -> None:
        object.__setattr__(self, "_app", app)

    def __getattr__(self, name: str):
        app = object.__getattribute__(self, "_app")
        if "ubs_portfolio" in name:
            monthly_name = name.replace("ubs_portfolio", "ubs_monthly_portfolio")
            try:
                return getattr(app, monthly_name)
            except AttributeError:
                pass
        return getattr(app, name)

    def __setattr__(self, name: str, value: object) -> None:
        app = object.__getattribute__(self, "_app")
        if "ubs_portfolio" in name:
            name = name.replace("ubs_portfolio", "ubs_monthly_portfolio")
        setattr(app, name, value)

    def _card(self, parent, title: str):
        app = object.__getattribute__(self, "_app")
        if title == "Portfolio Builder":
            title = "UBS Portafolio Mensual"
        return app._card(parent, title)


class UBSMonthlyPortfolioViewMixin:
    def _build_ubs_monthly_portfolio(self, parent: ttk.Frame) -> None:
        adapter = _MonthlyPortfolioScreenAdapter(self)
        UBSPortfolioViewMixin._build_ubs_portfolio(adapter, parent)
