"""Fixtures compartidos de los tests de reparacion riesgo/beneficio."""
import json

from ubs.score import ScoreResult


BASE_THRESHOLDS = {
    "min_net_profit": 100.0,
    "min_profit_factor": 1.2,
    "min_trades": 50,
    "max_drawdown_pct": 25.0,
    "min_recovery_factor": 1.0,
    "min_positive_month_ratio": 0.0,
}
# The base leg of an OOS pair was accepted, so its own net gate had to be below
# the normalized net it stored.
LENIENT_THRESHOLDS = {**BASE_THRESHOLDS, "min_net_profit": 20.0}
BASE_WINDOW = ("2020.01.01", "2024.12.31")
OOS_WINDOW = ("2025.01.01", "2025.12.31")
SCHEMA = """
create table candidates (
    id integer primary key, run_id integer, generation integer, symbol text,
    target_symbol text, period text, report_path text, score real,
    accepted integer, metrics_json text, status text
);
create table candidate_robustness (
    candidate_id integer primary key, run_id integer, status text, report_path text,
    score real, accepted integer, metrics_json text, degradation_json text
);
"""


def metrics(**overrides) -> ScoreResult:
    """A low-net candidate with strong equity evidence, as stored before the rule."""

    values = dict(
        report_path="base.htm", name="sample", symbol="USDCAD", timeframe="H1",
        score=218.147, accepted=False, net_profit=22.99, raw_net_profit=22.99,
        normalized_net_profit=26.55, net_profit_factor=1.1548,
        net_profit_basis="test", normalization_group="Forex", history_quality=100.0,
        profit_factor=3.2944, recovery_factor=67.6176, drawdown=.34, drawdown_pct=.03,
        trades=1108, positive_month_ratio=59 / 60, max_month_concentration=.0453,
        avg_trade=.0207, sqn=18.0, reasons=("net_profit",), active_months=60,
        residual_profit_ratio=.876, trade_curve_stability=.99,
        bootstrap_reps=2000, bootstrap_net_positive_probability=.999,
        bootstrap_pf_p05=1.8, score_config=dict(BASE_THRESHOLDS),
    )
    values.update(overrides)
    return ScoreResult(**values)


def stored(result: ScoreResult, **extra) -> str:
    """Serialize the way a pre-rule row looks: no policy, no risk audit."""

    payload = {**json.loads(result.to_json()), **extra}
    payload["score_config"] = {
        key: value for key, value in (payload.get("score_config") or {}).items()
        if key != "risk_profit"
    }
    payload.pop("risk_profit_audit", None)
    return json.dumps(payload, sort_keys=True)
