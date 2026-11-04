import unittest
from pathlib import Path

from ubs.seed_metadata import is_general_ubs_set, seed_trace_from_path


class UBSSeedMetadataTests(unittest.TestCase):
    def test_client_strategy_and_nested_family_from_source_path(self) -> None:
        path = Path("Client sets/BK/BTC_CommunityShare/BTCUSD_D1_A.set")

        trace = seed_trace_from_path(path, {"Run_Strategy": "2"})

        self.assertEqual(trace.client, "BK")
        self.assertEqual(trace.strategy_type, "Volatility Breakout")
        self.assertEqual(trace.family, "BTC CommunityShare")

    def test_profalgo_and_strategy_family_from_source_path(self) -> None:
        path = Path("Breakout_Support_Resistance/GOLD/Gold Phantom/a.set")

        trace = seed_trace_from_path(path, {"Run_Strategy": "1"})

        self.assertEqual(trace.client, "Profalgo")
        self.assertEqual(trace.strategy_type, "Soporte/Resistencia")
        self.assertEqual(trace.family, "GOLD / Gold Phantom")

    def test_imported_flattened_client_path_remains_traceable(self) -> None:
        path = Path("XAUUSD/H1/Client_sets/MisterMember_(Xmas)__gold.set")

        trace = seed_trace_from_path(path, {"Run_Strategy": "1"})

        self.assertEqual(trace.client, "MisterMember (Xmas)")
        self.assertEqual(trace.strategy_type, "Soporte/Resistencia")
        self.assertEqual(trace.family, "")

    def test_unknown_metadata_stays_empty(self) -> None:
        trace = seed_trace_from_path(Path("legacy/seed.set"))

        self.assertEqual((trace.client, trace.strategy_type, trace.family), ("", "", ""))

    def test_onechart_main_settings_is_a_general_set(self) -> None:
        path = Path("UBS live portfolio/ubs_onechart_live_MainEA_Settings.set")

        self.assertTrue(is_general_ubs_set(path, {}))
        self.assertTrue(is_general_ubs_set(Path("other.set"), {"UseAutoLoader": "true"}))
        self.assertFalse(is_general_ubs_set(Path("optimization.set"), {"UseAutoLoader": "false"}))
