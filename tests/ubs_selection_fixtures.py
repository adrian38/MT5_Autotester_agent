"""Metricas sinteticas compartidas por los tests de seleccion UBS."""
import json


def metrics(*, profit_factor: float = 1.6, recovery: float = 5.0, drawdown: float = 5.0) -> str:
    return json.dumps(
        {
            "profit_factor": profit_factor,
            "recovery_factor": recovery,
            "drawdown_pct": drawdown,
            "trades": 300,
            "positive_month_ratio": 0.65,
            "max_month_concentration": 0.08,
            "sqn": 3.0,
        }
    )
