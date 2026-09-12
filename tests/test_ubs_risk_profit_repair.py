import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ubs.degradation import RobustnessDegradationConfig, evaluate_robustness_degradation
from ubs.risk_profit import RiskProfitConfig, evaluate_risk_profit
from ubs.risk_profit_repair import (
    apply_risk_profit_restatements,
    scan_risk_profit_restatements,
)
from ubs.score import (
    ScoreConfig,
    ScoreResult,
    rescore_result,
    residual_profit_ratio_after_top_months,
    top_month_count,
)
from ui.ubs_universe_logic import UBSUniverseLogicMixin


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


class RiskProfitRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.reads: list[str] = []
        self.monthly_reads: list[str] = []
        self.equity = {"base.htm": (5.7, .56), "oos.htm": (2.0, .2)}
        # Todo lo que la ruta OOS lee y las filas viejas no guardaron. 17 meses
        # -> el 5% quita 1 mes, no los 3 fijos.
        self.evidence = {
            "oos.htm": dict(
                equity_drawdown=2.0, equity_drawdown_pct=.2, equity_recovery_factor=2.5,
                scaled_residual_profit_ratio=.79, scaled_residual_top_months=1,
                trade_curve_stability=.99, bootstrap_reps=2000, bootstrap_mean_block=5.0,
                bootstrap_net_positive_probability=.99, bootstrap_net_p05=1.0,
                bootstrap_pf_p05=1.8,
            ),
            "base.htm": dict(
                equity_drawdown=5.7, equity_drawdown_pct=.56, equity_recovery_factor=4.03,
                scaled_residual_profit_ratio=.86, scaled_residual_top_months=3,
                trade_curve_stability=.99, bootstrap_reps=2000, bootstrap_mean_block=5.0,
                bootstrap_net_positive_probability=.999, bootstrap_net_p05=2.0,
                bootstrap_pf_p05=1.8,
            ),
        }

    def tearDown(self) -> None:
        self.conn.close()

    def read_equity(self, path: str):
        self.reads.append(path)
        if path not in self.equity:
            raise OSError(f"no such report: {path}")
        return self.equity[path]

    def read_oos_evidence(self, path: str, share: float):
        self.monthly_reads.append(path)
        if path not in self.evidence:
            raise OSError(f"no such report: {path}")
        return dict(self.evidence[path])

    def scan(self, **kwargs):
        return scan_risk_profit_restatements(
            self.conn, read_equity=self.read_equity,
            read_oos_evidence=self.read_oos_evidence, **kwargs
        )

    def insert_base(self, candidate_id=1, *, status="rejected", result=None, **extra):
        result = result if result is not None else metrics()
        self.conn.execute(
            """insert into candidates
                   (id, run_id, generation, symbol, target_symbol, period, report_path,
                    score, accepted, metrics_json, status)
               values (?, 7, 1, 'USDCAD', 'USDCAD', 'H1', ?, ?, ?, ?, ?)""",
            (candidate_id, result.report_path, result.score,
             int(status == "accepted"), stored(result, **extra), status),
        )
        self.conn.commit()
        return result

    def oos_result(self, **overrides) -> ScoreResult:
        values = dict(
            report_path="oos.htm", net_profit=5.0, raw_net_profit=5.0,
            normalized_net_profit=5.77, trades=250, active_months=12,
            profit_factor=2.8, residual_profit_ratio=.5, equity_drawdown=None,
            equity_drawdown_pct=None, score_config=dict(LENIENT_THRESHOLDS),
        )
        values.update(overrides)
        return metrics(**values)

    def insert_oos_pair(self, candidate_id=1, *, oos_status="rejected", base_equity=None,
                        extra_reasons=()):
        """An accepted base row plus an OOS row rejected only by the net gate."""

        lenient = self.config(mode="off", thresholds=LENIENT_THRESHOLDS)
        base = metrics(equity_drawdown=base_equity, equity_drawdown_pct=None,
                       score_config=dict(LENIENT_THRESHOLDS))
        base = rescore_result(base, lenient)
        self.insert_base(candidate_id, status="accepted", result=base)
        oos = rescore_result(self.oos_result(), lenient, risk_stage="oos")
        degradation = evaluate_robustness_degradation(
            json.loads(base.to_json()), json.loads(oos.to_json()),
            base_from_date=BASE_WINDOW[0], base_to_date=BASE_WINDOW[1],
            oos_from_date=OOS_WINDOW[0], oos_to_date=OOS_WINDOW[1],
            config=RobustnessDegradationConfig(),
        )
        combined = tuple(dict.fromkeys([*oos.reasons, *extra_reasons, *degradation["reasons"]]))
        self.conn.execute(
            """insert into candidate_robustness
                   (candidate_id, run_id, status, report_path, score, accepted,
                    metrics_json, degradation_json)
               values (?, 7, ?, 'oos.htm', ?, 0, ?, ?)""",
            (candidate_id, oos_status, oos.score,
             stored(replace(oos, reasons=combined)),
             json.dumps(degradation, sort_keys=True)),
        )
        self.conn.commit()
        return base, oos, degradation

    def config(self, mode="enforce", thresholds=None) -> ScoreConfig:
        return ScoreConfig(**(thresholds or BASE_THRESHOLDS),
                           risk_profit=RiskProfitConfig(mode=mode))

    def stored_row(self, table="candidates", column="status", key="id", value=1):
        return self.conn.execute(
            f"select * from {table} where {key}=?", (value,)
        ).fetchone()[column]

    # ----- base stage --------------------------------------------------------

    def test_base_row_is_rescued_with_equity_read_back_from_the_report(self) -> None:
        self.insert_base()
        plan = self.scan()
        self.assertEqual(len(plan.base), 1)
        change = plan.base[0]
        self.assertEqual((change["stored_status"], change["expected_status"]),
                         ("rejected", "accepted"))
        self.assertEqual(change["selected_route"], "risk_adjusted")
        self.assertEqual(change["equity_source"], "reporte")
        self.assertEqual(self.reads, ["base.htm"])

        self.assertEqual(apply_risk_profit_restatements(self.conn, plan)["base"], 1)
        row = self.conn.execute("select * from candidates where id=1").fetchone()
        payload = json.loads(row["metrics_json"])
        self.assertEqual((row["status"], row["accepted"]), ("accepted", 1))
        self.assertEqual(payload["equity_drawdown"], 5.7)
        self.assertEqual(payload["reasons"], [])
        self.assertEqual(payload["risk_profit_audit"]["selected_route"], "risk_adjusted")
        self.assertEqual(payload["score_config"]["risk_profit"]["mode"], "enforce")

    def test_shadow_mode_reports_the_rescue_without_writing_anything(self) -> None:
        self.insert_base()
        plan = self.scan(policy=RiskProfitConfig(mode="shadow"))
        self.assertEqual(plan.base, [])
        self.assertTrue(plan.is_empty(), "shadow no mueve nada, asi que no escribe")
        self.assertEqual(len(plan.audit_only), 1)
        audit = json.loads(plan.audit_only[0]["metrics_json"])["risk_profit_audit"]
        self.assertTrue(audit["would_rescue"])
        apply_risk_profit_restatements(self.conn, plan)
        self.assertEqual(self.stored_row(), "rejected")

    def test_rows_failing_another_gate_are_never_read_or_touched(self) -> None:
        self.insert_base(result=metrics(reasons=("net_profit", "profit_factor")))
        self.insert_base(2, result=metrics(reasons=()), status="no_trades")
        plan = self.scan()
        self.assertTrue(plan.is_empty())
        self.assertEqual(self.reads, [])

    def test_insufficient_equity_evidence_keeps_the_rejection(self) -> None:
        self.equity["base.htm"] = (None, None)
        self.insert_base()
        plan = self.scan()
        self.assertTrue(plan.is_empty())
        self.assertEqual(plan.skipped, {"sin_equity_en_reporte": 1})

    def test_verdict_that_no_longer_follows_its_own_criteria_is_reported(self) -> None:
        # Stored as rejected for the net gate, but its own stored threshold
        # already passes: that is a pending rescore, not this rule.
        self.insert_base(result=metrics(normalized_net_profit=500.0))
        plan = self.scan()
        self.assertTrue(plan.is_empty())
        self.assertEqual(plan.skipped, {"criterio_desfasado": 1})
        self.assertEqual(self.reads, [])

    def test_missing_report_is_reported_instead_of_guessed(self) -> None:
        self.insert_base(result=metrics(report_path=""))
        plan = self.scan()
        self.assertTrue(plan.is_empty())
        self.assertEqual(plan.skipped, {"sin_reporte": 1})

    def test_unreadable_report_is_reported(self) -> None:
        self.insert_base(result=metrics(report_path="gone.htm"))
        plan = self.scan()
        self.assertTrue(plan.is_empty())
        self.assertEqual(plan.skipped, {"reporte_ilegible": 1})

    def test_execution_diagnostics_survive_the_rewrite(self) -> None:
        self.insert_base(failure_type="incompatible_volume", volume_min=0.1)
        plan = self.scan()
        payload = json.loads(plan.base[0]["metrics_json"])
        self.assertEqual(payload["failure_type"], "incompatible_volume")
        self.assertEqual(payload["volume_min"], 0.1)

    def test_second_pass_finds_nothing_left_to_do(self) -> None:
        self.insert_base()
        apply_risk_profit_restatements(self.conn, self.scan())
        self.reads.clear()
        second = self.scan()
        self.assertTrue(second.is_empty())
        self.assertEqual(self.reads, [])

    def test_row_the_route_cannot_rescue_is_left_untouched(self) -> None:
        self.insert_base(result=metrics(active_months=12))
        stored_before = self.stored_row(column="metrics_json")
        plan = self.scan()
        self.assertTrue(plan.is_empty())
        self.assertEqual(len(plan.audit_only), 1)
        self.assertEqual(plan.audit_only[0]["risk_status"], "insufficient_evidence")
        apply_risk_profit_restatements(self.conn, plan)
        self.assertEqual(self.stored_row(), "rejected")
        self.assertEqual(self.stored_row(column="metrics_json"), stored_before)

    def test_apply_leaves_rows_that_moved_meanwhile_alone(self) -> None:
        self.insert_base()
        plan = self.scan()
        self.conn.execute("update candidates set status='no_trades' where id=1")
        self.conn.commit()
        self.assertEqual(apply_risk_profit_restatements(self.conn, plan)["base"], 0)
        self.assertEqual(self.stored_row(), "no_trades")

    def test_rescued_rows_without_oos_row_are_listed_as_new_work(self) -> None:
        self.insert_base()
        plan = self.scan()
        self.assertEqual([item["candidate_id"] for item in plan.pending_robustness], [1])

    # ----- robustness stage --------------------------------------------------

    def test_oos_row_is_rescued_on_equity_basis_comparisons(self) -> None:
        self.insert_oos_pair()
        plan = self.scan()
        self.assertEqual(len(plan.base_evidence), 1, "base equity is required evidence")
        self.assertEqual(len(plan.robustness), 1)
        change = plan.robustness[0]
        self.assertEqual((change["stored_status"], change["expected_status"]),
                         ("rejected", "accepted"))
        self.assertEqual(change["selected_route"], "risk_adjusted")
        written = apply_risk_profit_restatements(self.conn, plan)
        self.assertEqual((written["base_evidence"], written["robustness"]), (1, 1))
        row = self.conn.execute("select * from candidate_robustness where candidate_id=1").fetchone()
        degradation = json.loads(row["degradation_json"])
        self.assertEqual((row["status"], row["accepted"]), ("accepted", 1))
        self.assertEqual(degradation["risk_basis"], "equity")
        self.assertTrue(degradation["final_accepted"])
        self.assertFalse(degradation["absolute_accepted"])
        self.assertEqual(json.loads(row["metrics_json"])["reasons"], [])

    def test_oos_row_without_base_equity_evidence_stays_pending(self) -> None:
        self.insert_oos_pair()
        self.evidence.pop("base.htm")
        plan = self.scan()
        self.assertEqual(plan.base_evidence, [])
        self.assertEqual(plan.robustness[0]["expected_status"], "pending_risk_evidence")
        self.assertIn("base_equity", plan.robustness[0]["missing_comparisons"])

    def test_pending_oos_row_resolves_once_the_report_can_be_read(self) -> None:
        """Una fila que ya paso por la ruta lleva `risk_profit_evidence` en sus
        razones. Eso lo escribe la propia regla, no un criterio guardado: si
        contara como causa, la fila quedaria excluida para siempre."""

        self.insert_oos_pair(oos_status="pending_risk_evidence",
                             extra_reasons=("risk_profit_evidence",))
        plan = self.scan()
        self.assertEqual(plan.robustness[0]["expected_status"], "accepted")
        apply_risk_profit_restatements(self.conn, plan)
        self.assertTrue(self.scan().is_empty())

    def test_a_row_the_route_parked_is_revisited(self) -> None:
        """Al aparcar una fila la ruta le QUITA `net_profit` de las razones.

        Si el scope leyera esas razones al pie de la letra, ninguna fila
        aparcada volveria a mirarse: la regla no podria resolver lo que ella
        misma dejo pendiente.
        """

        self.insert_oos_pair(oos_status="pending_risk_evidence")
        self.conn.execute(
            "update candidate_robustness set metrics_json=? where candidate_id=1",
            (stored(replace(self.oos_result(), reasons=("risk_profit_evidence",))),),
        )
        self.conn.commit()
        plan = self.scan()
        self.assertEqual(len(plan.robustness), 1)
        self.assertEqual(plan.robustness[0]["expected_status"], "accepted")

    def test_oos_row_rejected_by_another_absolute_gate_is_out_of_scope(self) -> None:
        self.insert_oos_pair()
        self.conn.execute(
            "update candidate_robustness set metrics_json=? where candidate_id=1",
            (stored(self.oos_result(reasons=("net_profit", "profit_factor"))),),
        )
        self.conn.commit()
        plan = self.scan()
        self.assertEqual(plan.robustness, [])
        self.assertEqual(self.reads, [])

    def test_base_rows_never_pay_for_the_monthly_series(self) -> None:
        """El cambio de duracion es solo de robustez: base no reparsea nada."""

        self.insert_base()
        plan = self.scan()
        self.assertEqual(len(plan.base), 1)
        self.assertEqual(self.reads, ["base.htm"])
        self.assertEqual(self.monthly_reads, [])
        payload = json.loads(plan.base[0]["metrics_json"])
        self.assertIsNone(payload["scaled_residual_profit_ratio"])
        self.assertEqual(payload["residual_profit_ratio"], .876)

    def test_oos_row_is_judged_with_the_scaled_concentration(self) -> None:
        self.insert_oos_pair()
        plan = self.scan()
        change = plan.robustness[0]
        self.assertEqual(self.monthly_reads, ["base.htm", "oos.htm"],
                         "el par completo: la comparacion es relativa")
        self.assertEqual(change["evidence_source"], "reporte")
        self.assertEqual(change["scaled_residual_top_months"], 1)
        self.assertEqual(change["scaled_residual_profit_ratio"], .79)
        payload = json.loads(change["metrics_json"])
        checks = payload["risk_profit_audit"]["checks"]
        self.assertEqual(checks["residual_profit_ratio"]["value"], .79)
        self.assertEqual(checks["residual_profit_ratio"]["basis"], "scaled_top_months")
        degradation = json.loads(change["degradation_json"])
        self.assertEqual(degradation["checks"]["residual_profit_ratio"]["value"], .79)
        self.assertEqual(degradation["checks"]["residual_profit_ratio"]["top_months_removed"], 1)

    def test_oos_recovery_only_has_to_be_measurable_and_positive(self) -> None:
        """El nivel se probo en base; cuanto sobrevivio lo mide la retencion.

        net 5.0 con equity DD 2.0 sobre una cuenta de 1000 es un DD del 0.2%:
        por debajo del suelo del 2% que ya usa `dd_inflation`, asi que el
        recovery se reporta con ese suelo y no decide nada por si mismo.
        """

        self.insert_oos_pair()
        checks = json.loads(self.scan().robustness[0]["metrics_json"])["risk_profit_audit"]["checks"]
        recovery = checks["recovery"]
        self.assertEqual(recovery["threshold"], 0.0)
        self.assertEqual(recovery["basis"], "per_year_floored_drawdown")
        self.assertEqual(recovery["judged_by"], "recovery_retention")
        self.assertEqual(recovery["years"], 1.0)
        self.assertEqual(recovery["raw"], 2.5)
        self.assertAlmostEqual(recovery["floored"], 5.0 / (2.0 * 2.0 / .2))
        self.assertTrue(recovery["accepted"])

    def test_oos_recovery_must_still_be_positive(self) -> None:
        self.insert_oos_pair()
        self.conn.execute(
            "update candidate_robustness set metrics_json=? where candidate_id=1",
            (stored(self.oos_result(net_profit=-5.0, normalized_net_profit=-5.77)),),
        )
        self.conn.commit()
        plan = self.scan()
        self.assertEqual(plan.robustness, [])

    def test_oos_row_without_monthly_series_is_reported(self) -> None:
        self.insert_oos_pair()
        self.evidence.pop("oos.htm")
        plan = self.scan()
        self.assertEqual(plan.robustness, [])
        self.assertEqual(plan.skipped.get("reporte_ilegible"), 1)

    def test_oos_row_shadow_mode_audits_without_writing(self) -> None:
        self.insert_oos_pair()
        plan = self.scan(policy=RiskProfitConfig(mode="shadow"))
        self.assertEqual(plan.robustness, [])
        self.assertTrue(plan.is_empty())
        audit = json.loads(plan.audit_only[-1]["metrics_json"])["risk_profit_audit"]
        self.assertTrue(audit["would_rescue"])
        self.assertEqual(audit["selected_route"], "none")


class ScaleFreeConcentrationTests(unittest.TestCase):
    """El test de concentracion tiene que medir lo mismo en 17 y en 60 meses."""

    def test_share_reproduces_the_historical_three_months_on_a_five_year_window(self) -> None:
        self.assertEqual(top_month_count(60, .05), 3)
        self.assertEqual([top_month_count(n, .05) for n in (3, 17, 30, 72)], [1, 1, 2, 4])
        self.assertIsNone(top_month_count(0, .05))

    def test_a_full_window_gives_the_same_number_as_the_fixed_measure(self) -> None:
        monthly = [1.0] * 55 + [8.0, 6.0, 4.0, 2.0, -3.0]
        net = sum(monthly)
        fixed = residual_profit_ratio_after_top_months(monthly, net, 3)
        scaled = residual_profit_ratio_after_top_months(monthly, net, top_month_count(60, .05))
        self.assertEqual(fixed, scaled)

    def test_short_window_keeps_profit_the_fixed_measure_erases(self) -> None:
        monthly = [0.5] * 14 + [4.0, 3.0, 2.0]  # 17 meses, beneficio 16.0
        net = sum(monthly)
        self.assertAlmostEqual(residual_profit_ratio_after_top_months(monthly, net, 3), .4375)
        self.assertAlmostEqual(
            residual_profit_ratio_after_top_months(monthly, net, top_month_count(17, .05)), .75
        )

    def test_oos_row_without_the_scaled_field_falls_back_to_the_stricter_measure(self) -> None:
        metrics = dict(
            net_profit=5.0, equity_drawdown=2.0, equity_drawdown_pct=.2, profit_factor=2.8,
            trades=250, active_months=12, positive_month_ratio=.9, residual_profit_ratio=.15,
        )
        legacy = evaluate_risk_profit(metrics, RiskProfitConfig(), stage="oos")
        scaled = evaluate_risk_profit(
            {**metrics, "scaled_residual_profit_ratio": .55, "scaled_residual_top_months": 1},
            RiskProfitConfig(), stage="oos",
        )
        self.assertFalse(legacy["eligible"])
        self.assertEqual(legacy["checks"]["residual_profit_ratio"]["basis"], "fixed_top3_fallback")
        self.assertTrue(scaled["eligible"])
        self.assertEqual(scaled["checks"]["residual_profit_ratio"]["top_months"], 1)


class Repairer(UBSUniverseLogicMixin):
    """Solo el mixin: el boton de Universo no necesita widgets para decidir."""

    def __init__(self, memory_path: Path) -> None:
        self.memory_path = memory_path
        self.messages: list[str] = []
        self.refreshed: list[str] = []
        self.status_text = SimpleNamespace(set=self.messages.append)

    def _ubs_memory_path(self) -> Path:
        return self.memory_path

    def _safe_refresh(self, label: str, callback) -> None:
        self.refreshed.append(label)

    def _refresh_ubs_universe(self) -> None:
        pass


class RiskProfitRepairButtonTests(unittest.TestCase):
    """El boton: confirmar, escribir auditoria, aplicar y refrescar."""

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.memory_path = Path(self.folder.name) / "memory.sqlite"
        conn = sqlite3.connect(self.memory_path)
        conn.executescript(SCHEMA)
        result = metrics()
        conn.execute(
            """insert into candidates
                   (id, run_id, generation, symbol, target_symbol, period, report_path,
                    score, accepted, metrics_json, status)
               values (1, 7, 1, 'USDCAD', 'USDCAD', 'H1', 'base.htm', ?, 0, ?, 'rejected')""",
            (result.score, stored(result)),
        )
        conn.commit()
        conn.close()
        self.app = Repairer(self.memory_path)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def plan(self):
        conn = sqlite3.connect(self.memory_path)
        conn.row_factory = sqlite3.Row
        try:
            return scan_risk_profit_restatements(
                conn,
                read_equity=lambda path: (5.7, .56),
                read_oos_evidence=lambda path, share: {},
            )
        finally:
            conn.close()

    def stored_status(self) -> str:
        conn = sqlite3.connect(self.memory_path)
        try:
            return conn.execute("select status from candidates where id=1").fetchone()[0]
        finally:
            conn.close()

    def audits(self) -> list[Path]:
        return sorted((self.memory_path.parent / "diagnostics").glob("risk_profit_repair_*.json"))

    def test_confirming_applies_writes_the_audit_and_refreshes(self) -> None:
        plan = self.plan()
        with patch("ui.ubs_universe_logic.messagebox") as box:
            box.askyesno.return_value = True
            self.app._finish_risk_profit_repair(self.memory_path, plan)
        self.assertEqual(self.stored_status(), "accepted")
        self.assertIn("ubs_universe", self.app.refreshed)
        audit = json.loads(self.audits()[0].read_text(encoding="utf-8"))
        self.assertEqual(audit["rule"], "risk_profit_v2")
        self.assertEqual(audit["policy"]["mode"], "enforce")
        self.assertEqual(audit["base"][0]["stored_status"], "rejected")
        self.assertIn("previous_metrics_json", audit["base"][0])

    def test_declining_writes_nothing(self) -> None:
        plan = self.plan()
        with patch("ui.ubs_universe_logic.messagebox") as box:
            box.askyesno.return_value = False
            self.app._finish_risk_profit_repair(self.memory_path, plan)
        self.assertEqual(self.stored_status(), "rejected")
        self.assertEqual(self.audits(), [])
        self.assertEqual(self.app.refreshed, [])

    def test_rows_the_route_leaves_alone_are_reported_not_written(self) -> None:
        conn = sqlite3.connect(self.memory_path)
        conn.execute(
            "update candidates set metrics_json=? where id=1",
            (stored(metrics(active_months=12)),),
        )
        conn.commit()
        conn.close()
        plan = self.plan()
        with patch("ui.ubs_universe_logic.messagebox") as box:
            self.app._finish_risk_profit_repair(self.memory_path, plan)
            box.askyesno.assert_not_called()
        self.assertEqual(self.stored_status(), "rejected")
        self.assertEqual(self.audits(), [], "nada que escribir, nada que auditar")

    def test_nothing_to_do_reports_and_skips_the_confirmation(self) -> None:
        conn = sqlite3.connect(self.memory_path)
        conn.execute("update candidates set status='no_trades' where id=1")
        conn.commit()
        conn.close()
        plan = self.plan()
        with patch("ui.ubs_universe_logic.messagebox") as box:
            self.app._finish_risk_profit_repair(self.memory_path, plan)
            box.askyesno.assert_not_called()
            box.showinfo.assert_called_once()
        self.assertEqual(self.audits(), [])

    def test_missing_memory_never_reaches_the_scan(self) -> None:
        self.app.memory_path = self.memory_path.with_name("gone.sqlite")
        with patch("ui.ubs_universe_logic.messagebox") as box:
            self.app._repair_risk_profit_states()
            box.showinfo.assert_called_once()
            box.askyesno.assert_not_called()


if __name__ == "__main__":
    unittest.main()
