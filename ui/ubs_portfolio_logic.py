"""Logica de la pantalla de Portafolio UBS, repartida en mixins por area."""
from __future__ import annotations

from ui.ubs_portfolio_base import (  # noqa: F401
    BASE_DIR,
    DEFAULT_PORTFOLIO_FORM,
    PORTFOLIO_ASSET_GROUP_FLAGS,
    PORTFOLIO_BUNDLE_DISPLAY,
    PORTFOLIO_MARGIN_PROFILE_DISPLAY,
    PORTFOLIO_TYPE_BATCH_SPECS,
    PORTFOLIO_TYPE_DISPLAY,
    PORTFOLIO_TYPE_LABELS,
)
from ui.ubs_portfolio_build import UBSPortfolioBuildMixin
from ui.ubs_portfolio_bundle import UBSPortfolioBundleMixin
from ui.ubs_portfolio_complete import UBSPortfolioCompleteMixin
from ui.ubs_portfolio_inputs import UBSPortfolioInputsMixin
from ui.ubs_portfolio_insert import UBSPortfolioInsertMixin
from ui.ubs_portfolio_optimize import UBSPortfolioOptimizeMixin
from ui.ubs_portfolio_proposals import UBSPortfolioProposalsMixin
from ui.ubs_portfolio_result import UBSPortfolioResultMixin
from ui.ubs_portfolio_schema import UBSPortfolioSchemaMixin
from ui.ubs_portfolio_sources import UBSPortfolioSourcesMixin
from ui.ubs_portfolio_tables import UBSPortfolioTablesMixin
from ui.ubs_portfolio_versions import UBSPortfolioVersionsMixin


class UBSPortfolioLogicMixin(
    UBSPortfolioSchemaMixin,
    UBSPortfolioSourcesMixin,
    UBSPortfolioInsertMixin,
    UBSPortfolioBundleMixin,
    UBSPortfolioVersionsMixin,
    UBSPortfolioInputsMixin,
    UBSPortfolioBuildMixin,
    UBSPortfolioTablesMixin,
    UBSPortfolioOptimizeMixin,
    UBSPortfolioProposalsMixin,
    UBSPortfolioCompleteMixin,
    UBSPortfolioResultMixin,
):
    """Pantalla de Portafolio UBS: cada area vive en su propio mixin."""
