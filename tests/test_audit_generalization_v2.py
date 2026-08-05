import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.audit_generalization_v2 import DEGRADATION_VERSION, FORMULA_VERSION, build_audit


def create_memory(path: Path, *, current: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        create table candidates (
            id integer primary key,
            run_id integer not null,
            symbol text,
            period text,
            set_path text,
            status text
        );
        create table candidate_robustness (
            candidate_id integer primary key,
            run_id integer not null,
            status text not null,
            accepted integer,
            score real,
            report_path text,
            metrics_json text,
            degradation_json text
        );
        create table candidate_final_tick (candidate_id integer primary key, status text);
        create table candidate_final_tick_6m (candidate_id integer primary key, status text);
        create table candidate_regression (candidate_id integer primary key, status text);
        create table portfolio_members (candidate_id integer);
        """
    )
    metrics = {"score_formula_version": FORMULA_VERSION if current else "1"}
    degradation = {}
    if current:
        checks = {
            "net_retention": 0.50,
            "pf_edge_retention": 0.50,
            "recovery_retention": 0.50,
            "dd_inflation": 2.00,
            "trade_rate_retention": 0.50,
            "residual_profit_ratio": 0.20,
            "oos_positive_month_ratio": 0.50,
            "trade_curve_stability": 0.60,
            "stability_retention": 0.75,
            "bootstrap_net_positive_probability": 0.95,
            "bootstrap_pf_p05": 1.05,
        }
        degradation = {
            "version": DEGRADATION_VERSION,
            "absolute_accepted": True,
            "accepted": True,
            "final_accepted": True,
            "checks": {
                name: {
                    "enabled": True,
                    "available": True,
                    "accepted": True,
                    "threshold": threshold,
                    **(
                        {"base_annualized": 1.0, "oos_annualized": 1.0}
                        if name == "recovery_retention"
                        else {}
                    ),
                }
                for name, threshold in checks.items()
            },
        }
    conn.execute("insert into candidates values (1, 7, 'EURUSD', 'H1', 'candidate.set', 'accepted')")
    conn.execute(
        "insert into candidate_robustness values (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            1,
            7,
            "accepted" if current else "rejected",
            1 if current else 0,
            10.0,
            "missing-report.html",
            json.dumps(metrics),
            json.dumps(degradation),
        ),
    )
    return conn


class GeneralizationV2AuditTests(unittest.TestCase):
    def test_detects_new_pass_with_complete_prior_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            before_path = Path(temp_dir) / "before.sqlite"
            current_path = Path(temp_dir) / "current.sqlite"
            before = create_memory(before_path, current=False)
            current = create_memory(current_path, current=True)
            try:
                for conn in (before, current):
                    conn.execute("insert into candidate_final_tick values (1, 'accepted')")
                    conn.execute("insert into candidate_final_tick_6m values (1, 'accepted')")
                    conn.execute("insert into candidate_regression values (1, 'accepted')")
                    conn.execute("insert into portfolio_members values (1)")
                    conn.commit()
            finally:
                before.close()
                current.close()

            audit = build_audit(current_path, before_path, integrity_check=True)

            self.assertEqual(audit["verdict"], "PASS_WITH_WARNINGS")
            self.assertEqual(audit["transition_counts"]["rejected->accepted"], 1)
            self.assertEqual(audit["newly_accepted"]["count"], 1)
            self.assertEqual(audit["newly_accepted"]["with_prior_complete_downstream"], 1)
            self.assertEqual(audit["newly_accepted"]["with_prior_full_pass_chain"], 1)
            self.assertEqual(audit["newly_accepted"]["currently_missing_final_tick"], 0)

    def test_fails_when_required_degradation_check_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            before_path = Path(temp_dir) / "before.sqlite"
            current_path = Path(temp_dir) / "current.sqlite"
            before = create_memory(before_path, current=False)
            current = create_memory(current_path, current=True)
            try:
                row = current.execute(
                    "select degradation_json from candidate_robustness where candidate_id=1"
                ).fetchone()
                degradation = json.loads(row[0])
                del degradation["checks"]["bootstrap_pf_p05"]
                current.execute(
                    "update candidate_robustness set degradation_json=? where candidate_id=1",
                    (json.dumps(degradation),),
                )
                before.commit()
                current.commit()
            finally:
                before.close()
                current.close()

            audit = build_audit(current_path, before_path, integrity_check=False)

            self.assertEqual(audit["verdict"], "FAIL")
            codes = {item["code"] for item in audit["issues"]}
            self.assertIn("rows_missing_required_checks", codes)


if __name__ == "__main__":
    unittest.main()
