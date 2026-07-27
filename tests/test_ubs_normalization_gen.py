import unittest

from ubs.mt5_symbol_extract import SymbolSpec
from ubs.normalization_gen import (
    build_normalization_config,
    clamp_lot,
    compute_symbol_factors,
    notional_per_lot,
    symbol_factor,
)


def _forex_spec(name="EURUSD.sa"):
    # 0.01-lot EURUSD ~= 1000 units notional (~993 EUR here) -> factor ~= 1.0
    return SymbolSpec(
        name=name,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
        contract_size=100000.0,
        tick_value=0.92,
        tick_size=0.00001,
        price=1.08,
        currency_profit="USD",
    )


def _stock_spec(name="Costco+", price=300.0):
    # Share CFD: contract size 1, broker min lot 1.0 -> backtest runs at 1.0 lot.
    return SymbolSpec(
        name=name,
        volume_min=1.0,
        volume_step=1.0,
        volume_max=1000.0,
        contract_size=1.0,
        tick_value=0.0092,  # 0.01 tick * 1 contract * 0.92 fx (USD->EUR)
        tick_size=0.01,
        price=price,
        currency_profit="USD",
    )


class ClampLotTests(unittest.TestCase):
    def test_forex_keeps_requested_lot(self):
        self.assertAlmostEqual(clamp_lot(0.01, 0.01, 0.01, 100.0), 0.01)

    def test_stock_clamps_up_to_min_volume(self):
        self.assertAlmostEqual(clamp_lot(0.01, 1.0, 1.0, 1000.0), 1.0)

    def test_step_tenth_clamps_to_min(self):
        self.assertAlmostEqual(clamp_lot(0.01, 0.1, 0.1, 50.0), 0.1)

    def test_respects_volume_max(self):
        self.assertAlmostEqual(clamp_lot(5.0, 1.0, 1.0, 2.0), 2.0)


class NotionalTests(unittest.TestCase):
    def test_forex_notional_matches_units(self):
        self.assertAlmostEqual(notional_per_lot(1.08, 0.92, 0.00001), 99360.0, places=1)

    def test_stock_notional_is_price_times_fx(self):
        self.assertAlmostEqual(notional_per_lot(300.0, 0.0092, 0.01), 276.0, places=4)

    def test_zero_inputs_return_zero(self):
        self.assertEqual(notional_per_lot(0.0, 1.0, 0.01), 0.0)
        self.assertEqual(notional_per_lot(300.0, 0.0, 0.01), 0.0)
        self.assertEqual(notional_per_lot(300.0, 1.0, 0.0), 0.0)


class SymbolFactorTests(unittest.TestCase):
    def test_forex_factor_is_about_one(self):
        result = symbol_factor(_forex_spec(), reference_notional=1000.0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.factor, 1.0, delta=0.05)
        self.assertAlmostEqual(result.lot_used, 0.01)

    def test_stock_factor_scales_up_small_notional(self):
        result = symbol_factor(_stock_spec(price=300.0), reference_notional=1000.0)
        self.assertIsNotNone(result)
        # 1000 / (1.0 lot * 276 notional) ~= 3.62 (NOT the old 0.01)
        self.assertAlmostEqual(result.factor, 3.62, delta=0.05)
        self.assertAlmostEqual(result.lot_used, 1.0)

    def test_cheap_and_expensive_stocks_get_different_factors(self):
        cheap = symbol_factor(_stock_spec(name="Shopify+", price=30.0), reference_notional=1000.0)
        pricey = symbol_factor(_stock_spec(name="Meta+", price=600.0), reference_notional=1000.0)
        # A flat per-group factor cannot do this; per-symbol must.
        self.assertGreater(cheap.factor, pricey.factor * 5)

    def test_missing_price_returns_none(self):
        spec = _stock_spec()
        spec = SymbolSpec(**{**spec.__dict__, "price": 0.0})
        self.assertIsNone(symbol_factor(spec))

    def test_min_notional_caps_cheap_instrument_amplification(self):
        penny = _stock_spec(name="Aurora+", price=2.71)  # ~2.71 notional -> factor ~369 uncapped
        uncapped = symbol_factor(penny, reference_notional=1000.0, min_notional=0.0)
        capped = symbol_factor(penny, reference_notional=1000.0, min_notional=100.0)
        self.assertGreater(uncapped.factor, 100)
        # floor of 100 caps the factor at reference/floor = 10x
        self.assertAlmostEqual(capped.factor, 10.0, delta=0.01)

    def test_min_notional_does_not_touch_large_notional(self):
        # A $935 share already exceeds the $100 floor, so its factor is unchanged.
        big = _stock_spec(name="Costco+", price=935.0)
        base = symbol_factor(big, reference_notional=1000.0, min_notional=0.0)
        floored = symbol_factor(big, reference_notional=1000.0, min_notional=100.0)
        self.assertAlmostEqual(base.factor, floored.factor)


class BuildConfigTests(unittest.TestCase):
    def test_keys_uppercased_and_groups_medianed(self):
        specs = [_forex_spec("EURUSD.sa"), _stock_spec("Costco+", 300.0), _stock_spec("Shopify+", 30.0)]
        group_by_symbol = {"EURUSD.SA": "Forex", "COSTCO+": "Stocks", "SHOPIFY+": "Stocks"}
        factors, skipped = compute_symbol_factors(specs, group_by_symbol, reference_notional=1000.0)
        self.assertEqual(skipped, [])
        config = build_normalization_config(factors, broker="AXI", reference_notional=1000.0)

        self.assertIn("COSTCO+", config["symbol_net_profit_factors"])
        self.assertIn("SHOPIFY+", config["symbol_net_profit_factors"])
        self.assertIn("EURUSD.SA", config["symbol_net_profit_factors"])
        self.assertEqual(config["group_suffix_net_profit_factors"], {})
        self.assertEqual(config["symbol_count"], 3)
        self.assertIn("Stocks", config["group_net_profit_factors"])
        self.assertIn("Forex", config["group_net_profit_factors"])
        self.assertAlmostEqual(config["group_net_profit_factors"]["Forex"], 1.0, delta=0.05)

    def test_unmeasured_symbol_is_skipped(self):
        bad = SymbolSpec(name="BROKEN+", volume_min=1.0, volume_step=1.0, price=0.0)
        factors, skipped = compute_symbol_factors([bad], {"BROKEN+": "Stocks"})
        self.assertEqual(factors, [])
        self.assertEqual(skipped, ["BROKEN+"])


if __name__ == "__main__":
    unittest.main()
