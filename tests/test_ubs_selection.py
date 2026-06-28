import json
import unittest

from ubs.selection import SelectionFitnessModel, finalized_six_month_label


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


class UBSSelectionFitnessTests(unittest.TestCase):
    def test_final_label_accepts_probe_pending_operations_when_six_month_passes(self) -> None:
        row = {
            "status": "accepted",
            "robust_status": "accepted",
            "final_tick_status": "pending_ohlc_trades",
            "final_tick_6m_status": "accepted",
        }
        self.assertEqual(finalized_six_month_label(row), 1)

    def test_final_label_excludes_unresolved_technical_rows(self) -> None:
        row = {
            "status": "accepted",
            "robust_status": "accepted",
            "final_tick_status": "parse_error",
            "final_tick_6m_status": "",
        }
        self.assertIsNone(finalized_six_month_label(row))

    def test_model_learns_final_fitness_separately_from_raw_score(self) -> None:
        rows = []
        for index in range(100):
            rows.append(
                {
                    "status": "accepted",
                    "robust_status": "accepted",
                    "final_tick_status": "accepted",
                    "final_tick_6m_status": "accepted",
                    "score": 80.0 + index % 10,
                    "metrics_json": metrics(),
                    "period": "H4",
                }
            )
        for index in range(300):
            rows.append(
                {
                    "status": "accepted",
                    "robust_status": "rejected",
                    "final_tick_status": "",
                    "final_tick_6m_status": "",
                    "score": 190.0 + index % 10,
                    "metrics_json": metrics(profit_factor=3.5, recovery=15.0, drawdown=1.0),
                    "period": "H4",
                }
            )

        model = SelectionFitnessModel.train(rows)

        self.assertIsNotNone(model)
        assert model is not None
        compatible = model.predict(85.0, metrics(), "H4")
        extreme = model.predict(195.0, metrics(profit_factor=3.5, recovery=15.0, drawdown=1.0), "H4")
        self.assertGreater(compatible.probability, extreme.probability)
        self.assertGreater(compatible.weight, extreme.weight)


if __name__ == "__main__":
    unittest.main()

