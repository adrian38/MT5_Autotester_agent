"""Dobles de prueba compartidos por los tests de persistencia de portafolio."""
from __future__ import annotations

from ui.ubs_portfolio_logic import UBSPortfolioLogicMixin



class _PortfolioLogic(UBSPortfolioLogicMixin):
    pass


class _MonthlyProposalApplyLogic(UBSPortfolioLogicMixin):
    def _accept_generated_ubs_monthly_portfolio_proposal(self, proposal):
        self.accepted_monthly_proposal = proposal

    def _save_pending_ubs_monthly_portfolio(self):
        self.saved_monthly_proposal = True


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _DummyConn:
    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _BatchSaveLogic(UBSPortfolioLogicMixin):
    def __init__(self):
        self.inserted = []
        self.bundle_inserted = []
        self.selected_id = None
        self.save_enabled = True
        self.notifications = []
        self.ubs_portfolio_status = _Var("")

    def _ubs_portfolio_conn(self):
        return _DummyConn()

    def _insert_portfolio(self, conn, inputs, result, *, commit=True):
        portfolio_id = len(self.inserted) + 1
        self.inserted.append((portfolio_id, inputs, result, commit))
        return portfolio_id

    def _insert_portfolio_bundle(self, conn, proposals, selected_result, *, commit=True):
        portfolio_id = len(self.bundle_inserted) + 1
        self.bundle_inserted.append((portfolio_id, proposals, selected_result, commit))
        return portfolio_id

    def _set_ubs_portfolio_save_enabled(self, enabled):
        self.save_enabled = enabled

    def _refresh_ubs_portfolios(self, select_id=None):
        self.selected_id = select_id

    def _notify_ubs_portfolio_event(self, message):
        self.notifications.append(message)


class _Result:
    def __init__(self, net):
        self.allocations = [object()]
        self.total_net_profit = net
        self.total_lot = 0.01
        self.active_strategies = 1
