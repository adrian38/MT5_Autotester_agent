import json
import tempfile
import unittest
from pathlib import Path

from ubs.normalization import net_profit_normalization
from ubs.score import SCORE_FORMULA_VERSION, ScoreConfig, ScoreResult, rescore_result


class UBSScoreTests(unittest.TestCase):
    def test_score_result_accepts_legacy_json_without_version_metadata(self) -> None:
        payload = {
            "report_path": "report.htm",
            "name": "report",
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "score": 100.0,
            "accepted": True,
            "net_profit": 100.0,
            "raw_net_profit": 100.0,
            "normalized_net_profit": 100.0,
            "net_profit_factor": 1.0,
            "net_profit_basis": "legacy",
            "normalization_group": "legacy",
            "history_quality": 99.0,
            "profit_factor": 1.5,
            "recovery_factor": 2.0,
            "drawdown": 10.0,
            "drawdown_pct": 1.0,
            "trades": 100,
            "positive_month_ratio": 1.0,
            "max_month_concentration": 0.2,
            "avg_trade": 1.0,
            "sqn": 2.0,
            "reasons": [],
        }

        result = ScoreResult(**json.loads(json.dumps(payload)))

        self.assertEqual(result.score_formula_version, SCORE_FORMULA_VERSION)
        self.assertEqual(result.score_config, {})
        self.assertEqual(result.score_config_hash, "")

    def test_rescore_result_reclassifies_persisted_metrics_without_report(self) -> None:
        payload = {
            "report_path": "missing-report.htm",
            "name": "report",
            "symbol": "EURUSD",
            "timeframe": "H1",
            "score": -999.0,
            "accepted": False,
            "net_profit": 150.0,
            "raw_net_profit": 150.0,
            "normalized_net_profit": 150.0,
            "net_profit_factor": 1.0,
            "net_profit_basis": "stored",
            "normalization_group": "stored",
            "history_quality": 99.0,
            "profit_factor": 1.4,
            "recovery_factor": 1.5,
            "drawdown": 10.0,
            "drawdown_pct": 10.0,
            "trades": 60,
            "positive_month_ratio": 0.6,
            "max_month_concentration": 0.2,
            "avg_trade": 2.5,
            "sqn": 1.2,
            "reasons": ["old_rule"],
        }

        stored = ScoreResult.from_json(json.dumps(payload))
        config = ScoreConfig(min_net_profit=100.0)
        rescored = rescore_result(stored, config)

        self.assertTrue(rescored.accepted)
        self.assertEqual(rescored.reasons, ())
        self.assertNotEqual(rescored.score, -999.0)
        self.assertEqual(rescored.score_config_hash, config.stable_hash())
        self.assertEqual(rescored.normalized_net_profit, 150.0)

    def test_score_config_hash_is_stable_and_changes_with_thresholds(self) -> None:
        base = ScoreConfig()
        same = ScoreConfig()
        changed = ScoreConfig(min_profit_factor=1.3)

        self.assertEqual(base.stable_hash(), same.stable_hash())
        self.assertNotEqual(base.stable_hash(), changed.stable_hash())

    def test_net_profit_normalization_is_broker_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            assets = base / "assets"
            assets.mkdir()
            (assets / "roboforex_assets.ini").write_text("[Stocks]\nsymbols=BA\n", encoding="utf-8")
            (assets / "axi_assets.ini").write_text("[Stocks]\nsymbols=BA\n", encoding="utf-8")
            (assets / "roboforex_normalization.json").write_text(
                json.dumps(
                    {
                        "basis": "roboforex_test_basis",
                        "default_net_profit_factor": 1.0,
                        "group_net_profit_factors": {"Stocks": 5.0},
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                net_profit_normalization("BA", broker="ROBOFOREX", base_dir=base),
                (5.0, "Stocks", "roboforex_test_basis"),
            )
            self.assertEqual(
                net_profit_normalization("BA", broker="AXI", base_dir=base),
                (1.0, "Stocks", "raw_net_profit"),
            )

    def test_ictrading_normalization_splits_indices_energies_and_stocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            assets = base / "assets"
            assets.mkdir()
            (assets / "ictrading_assets.ini").write_text(
                "[Indices]\nsymbols=USTEC,US500\n\n"
                "[Energies]\nsymbols=XTIUSD,XBRUSD\n\n"
                "[Stocks]\nsymbols=ABN.AMS\n\n"
                "[CommonAliases]\nUS100=USTEC\nWTI=XTIUSD\n",
                encoding="utf-8",
            )
            (assets / "ictrading_normalization.json").write_text(
                json.dumps(
                    {
                        "basis": "ictrading_lot_0.01_equivalent_net_profit",
                        "default_net_profit_factor": 1.0,
                        "group_net_profit_factors": {
                            "Indices": 0.1,
                            "Energies": 0.02,
                            "Stocks": 0.01,
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                net_profit_normalization("US100", broker="ICTRADING", base_dir=base),
                (0.1, "Indices", "ictrading_lot_0.01_equivalent_net_profit"),
            )
            self.assertEqual(
                net_profit_normalization("WTI", broker="ICTRADING", base_dir=base),
                (0.02, "Energies", "ictrading_lot_0.01_equivalent_net_profit"),
            )
            self.assertEqual(
                net_profit_normalization("ABN.AMS", broker="ICTRADING", base_dir=base),
                (0.01, "Stocks", "ictrading_lot_0.01_equivalent_net_profit"),
            )

    def test_axi_normalization_keeps_cash_suffix_rules_off_futures_and_shares(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            assets = base / "assets"
            assets.mkdir()
            (assets / "axi_assets.ini").write_text(
                "[Indices]\nsymbols=US30.sa,IT40.sa,NAS100.fs\n\n"
                "[Energies]\nsymbols=USOIL.sa,WTI.fs,EnergySPDR+\n\n"
                "[Stocks]\nsymbols=NasdaqInc+\n\n"
                "[Crypto]\nsymbols=BTCUSD.sa,BCHUSD.sa\n",
                encoding="utf-8",
            )
            (assets / "axi_normalization.json").write_text(
                json.dumps(
                    {
                        "basis": "axi_seed_report_lot_audit",
                        "default_net_profit_factor": 1.0,
                        "group_net_profit_factors": {
                            "Indices": 1.0,
                            "Energies": 1.0,
                            "Stocks": 1.0,
                            "Crypto": 1.0,
                        },
                        "group_suffix_net_profit_factors": {
                            "Indices": {
                                ".sa": 0.01,
                                ".fs": 1.0,
                            },
                            "Energies": {
                                ".sa": 0.1,
                                ".fs": 1.0,
                            },
                            "Stocks": {
                                "+": 0.01,
                            },
                        },
                        "symbol_net_profit_factors": {
                            "BCHUSD.SA": 0.02,
                            "IT40.SA": 0.1,
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                net_profit_normalization("US30.sa", broker="AXI", base_dir=base),
                (0.01, "Indices", "axi_seed_report_lot_audit"),
            )
            self.assertEqual(
                net_profit_normalization("IT40.sa", broker="AXI", base_dir=base),
                (0.1, "Indices", "axi_seed_report_lot_audit"),
            )
            self.assertEqual(
                net_profit_normalization("USOIL.sa", broker="AXI", base_dir=base),
                (0.1, "Energies", "axi_seed_report_lot_audit"),
            )
            self.assertEqual(
                net_profit_normalization("NAS100.fs", broker="AXI", base_dir=base),
                (1.0, "Indices", "axi_seed_report_lot_audit"),
            )
            self.assertEqual(
                net_profit_normalization("EnergySPDR+", broker="AXI", base_dir=base),
                (1.0, "Energies", "axi_seed_report_lot_audit"),
            )
            self.assertEqual(
                net_profit_normalization("NasdaqInc+", broker="AXI", base_dir=base),
                (0.01, "Stocks", "axi_seed_report_lot_audit"),
            )
            self.assertEqual(
                net_profit_normalization("BTCUSD.sa", broker="AXI", base_dir=base),
                (1.0, "Crypto", "axi_seed_report_lot_audit"),
            )
            self.assertEqual(
                net_profit_normalization("BCHUSD.sa", broker="AXI", base_dir=base),
                (0.02, "Crypto", "axi_seed_report_lot_audit"),
            )


if __name__ == "__main__":
    unittest.main()
