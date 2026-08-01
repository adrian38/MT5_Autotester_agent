import unittest

from ubs.mt5_symbol_extract import SymbolSpec
from ubs.normalization_gen import (
    build_normalization_config,
    build_symbol_specs_payload,
    clamp_lot,
    compute_symbol_factors,
    implied_currency_rates,
    notional_per_lot,
    specs_from_payload,
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


def _gbx_spec(name="3iGroup+", price=2905.93):
    # LSE share: quoted in pence, 100-share minimum lot, and MT5 leaves the tick
    # value at 0 because it has no GBX->USD conversion loaded.
    return SymbolSpec(
        name=name,
        volume_min=100.0,
        volume_step=100.0,
        volume_max=20000.0,
        contract_size=1.0,
        tick_value=0.0,
        tick_size=0.01,
        price=price,
        currency_profit="GBX",
    )


def _gbp_pair_spec(name="EURGBP.sa"):
    # Supplies the GBP rate the GBX symbols need: 1 GBP = 1.34 USD.
    return SymbolSpec(
        name=name,
        volume_min=0.01,
        volume_step=0.01,
        contract_size=100000.0,
        tick_value=1.34,
        tick_size=0.00001,
        price=0.86,
        currency_profit="GBP",
    )


class CurrencyRateTests(unittest.TestCase):
    def test_rate_is_one_for_the_account_currency(self):
        # A USD symbol on a USD account: 1 USD tick value per 0.00001 * 100000.
        usd = SymbolSpec(
            name="EURUSD.sa",
            volume_min=0.01,
            contract_size=100000.0,
            tick_value=1.0,
            tick_size=0.00001,
            price=1.1472,
            currency_profit="USD",
        )
        self.assertAlmostEqual(implied_currency_rates([usd])["USD"], 1.0, places=6)

    def test_minor_currency_is_derived_from_its_parent(self):
        rates = implied_currency_rates([_gbp_pair_spec(), _gbx_spec()])
        self.assertAlmostEqual(rates["GBP"], 1.34, places=6)
        self.assertAlmostEqual(rates["GBX"], 0.0134, places=6)

    def test_account_currency_is_assumed_when_nothing_measures_it(self):
        rates = implied_currency_rates([_gbp_pair_spec()], account_currency="usd")
        self.assertEqual(rates["USD"], 1.0)

    def test_pence_spelled_gbp_lowercase_p_is_not_read_as_pounds(self):
        # MT5 also spells pence "GBp"; upper-casing it would value a 2905 pence
        # share at 2905 pounds and undervalue its factor by 100x.
        pence = SymbolSpec(**{**_gbx_spec().__dict__, "currency_profit": "GBp"})
        rates = implied_currency_rates([_gbp_pair_spec(), pence])
        factors, skipped = compute_symbol_factors(
            [_gbp_pair_spec(), pence],
            {"EURGBP.SA": "Forex", "3IGROUP+": "Stocks"},
            reference_notional=1000.0,
            min_notional=100.0,
        )
        self.assertEqual(skipped, [])
        self.assertAlmostEqual(rates["GBX"], 0.0134, places=6)
        share = next(item for item in factors if item.name == "3iGroup+")
        self.assertAlmostEqual(share.actual_notional, 3893.95, delta=1.0)


class ReconstructedNotionalTests(unittest.TestCase):
    def test_notional_falls_back_to_contract_times_rate(self):
        # 2905.93 pence * 1 share * 0.0134 USD/pence = 38.94 USD per share
        self.assertAlmostEqual(
            notional_per_lot(2905.93, 0.0, 0.01, contract_size=1.0, currency_rate=0.0134),
            38.9395,
            places=3,
        )

    def test_tick_value_wins_when_present(self):
        self.assertAlmostEqual(
            notional_per_lot(300.0, 0.0092, 0.01, contract_size=1.0, currency_rate=99.0),
            276.0,
            places=4,
        )

    def test_gbx_share_is_measured_instead_of_skipped(self):
        specs = [_gbp_pair_spec(), _gbx_spec()]
        factors, skipped = compute_symbol_factors(
            specs,
            {"EURGBP.SA": "Forex", "3IGROUP+": "Stocks"},
            reference_notional=1000.0,
            min_notional=100.0,
        )
        self.assertEqual(skipped, [])
        share = next(item for item in factors if item.name == "3iGroup+")
        # 100 shares * 38.94 USD = 3894 USD of exposure -> factor well under 1,
        # not the 10.0 the Stocks group median used to hand it.
        self.assertAlmostEqual(share.actual_notional, 3893.95, delta=1.0)
        self.assertAlmostEqual(share.factor, 0.2568, delta=0.001)
        self.assertEqual(share.source, "contract_rate")

    def test_symbol_without_any_price_is_still_skipped(self):
        blind = SymbolSpec(name="GBXUSD.sa", volume_min=0.01, contract_size=100000.0, price=0.0)
        factors, skipped = compute_symbol_factors([blind], {"GBXUSD.SA": "Forex"})
        self.assertEqual(factors, [])
        self.assertEqual(skipped, ["GBXUSD.sa"])


class SpecsPayloadTests(unittest.TestCase):
    def test_reads_the_terminal_dump_map(self):
        payload = {
            "account_currency": "USD",
            "symbols": {
                "EURUSD.sa": {"price": 1.14, "volume_min": 0.01, "contract_size": 100000.0,
                              "tick_value": 1.0, "tick_size": 0.00001, "currency_profit": "USD",
                              "margin_min_lot": 11.47, "observed_leverage": 100.0},
            },
        }
        specs = specs_from_payload(payload)
        self.assertEqual([spec.name for spec in specs], ["EURUSD.sa"])
        self.assertAlmostEqual(specs[0].price, 1.14)

    def test_reads_the_plain_list_dump(self):
        specs = specs_from_payload([{"name": "XAUUSD.sa", "price": 4025.18, "tick_size": 0.01}])
        self.assertEqual(specs[0].name, "XAUUSD.sa")

    def test_ignores_rows_that_are_not_objects(self):
        self.assertEqual(specs_from_payload({"symbols": {"BAD": 3}}), [])


class SymbolSpecsPayloadTests(unittest.TestCase):
    """The dump feeds the portfolio manager's margin model; it must not lose fields."""

    def test_money_fields_are_in_the_account_currency(self):
        payload = build_symbol_specs_payload(
            [_gbp_pair_spec(), _gbx_spec()],
            account_currency="USD",
            account_leverage=100,
            group_by_symbol={"3IGROUP+": "Stocks"},
        )
        share = payload["symbols"]["3iGroup+"]
        # 100 shares * 2905.93 pence * 0.0134 = 3894 USD, not the 290593 a raw
        # price * volume would report.
        self.assertAlmostEqual(share["notional_min_lot"], 3893.95, delta=1.0)
        self.assertAlmostEqual(share["currency_rate"], 0.0134, places=6)
        self.assertEqual(share["group"], "Stocks")
        self.assertEqual(payload["account_leverage"], 100)
        self.assertEqual(payload["notional_currency"], "USD")

    def test_margin_survives_an_extraction_that_could_not_measure_it(self):
        previous = {
            "symbols": {"3iGroup+": {"margin_min_lot": 39.0, "custom_field": "keep me"}},
            "note_from_before": "kept",
        }
        payload = build_symbol_specs_payload([_gbp_pair_spec(), _gbx_spec()], previous=previous)
        share = payload["symbols"]["3iGroup+"]
        self.assertEqual(share["margin_min_lot"], 39.0)
        self.assertEqual(share["custom_field"], "keep me")
        self.assertEqual(payload["note_from_before"], "kept")
        # Leverage is derivable again once the margin is back.
        self.assertAlmostEqual(share["observed_leverage"], 3893.95 / 39.0, delta=0.1)

    def test_symbols_the_extraction_missed_are_carried_not_deleted(self):
        # MT5 could not resolve Roche+ on the last read; dropping it would take
        # its margin away from the portfolio manager.
        previous = {"symbols": {"Roche+": {"margin_min_lot": 12.5, "price": 250.0, "volume_min": 1.0}}}
        payload = build_symbol_specs_payload([_gbp_pair_spec()], previous=previous)
        self.assertIn("Roche+", payload["symbols"])
        self.assertEqual(payload["symbols"]["Roche+"]["margin_min_lot"], 12.5)
        self.assertEqual(payload["carried_symbols"], ["Roche+"])
        self.assertEqual(payload["measured_symbol_count"], 1)
        self.assertEqual(payload["symbol_count"], 2)

    def test_measured_margin_wins_over_the_previous_dump(self):
        spec = SymbolSpec(**{**_gbx_spec().__dict__, "margin_min_lot": 41.5})
        payload = build_symbol_specs_payload(
            [_gbp_pair_spec(), spec],
            previous={"symbols": {"3iGroup+": {"margin_min_lot": 39.0}}},
        )
        self.assertEqual(payload["symbols"]["3iGroup+"]["margin_min_lot"], 41.5)

    def test_symbols_without_price_are_listed_and_have_no_money_fields(self):
        blind = SymbolSpec(name="GBXUSD.sa", volume_min=0.01, contract_size=100000.0, price=0.0)
        payload = build_symbol_specs_payload([blind], missing_symbols=["Roche+"])
        self.assertEqual(payload["symbols_without_price"], ["GBXUSD.sa"])
        self.assertEqual(payload["missing_symbols"], ["Roche+"])
        self.assertIsNone(payload["symbols"]["GBXUSD.sa"]["notional_min_lot"])
        self.assertIsNone(payload["symbols"]["GBXUSD.sa"]["observed_leverage"])

    def test_the_dump_round_trips_back_into_specs(self):
        payload = build_symbol_specs_payload([_gbp_pair_spec(), _gbx_spec()], account_currency="USD")
        names = sorted(spec.name for spec in specs_from_payload(payload))
        self.assertEqual(names, ["3iGroup+", "EURGBP.sa"])


class BuildConfigTests(unittest.TestCase):
    def test_keys_uppercased_and_group_fallback_is_conservative(self):
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
        self.assertAlmostEqual(config["group_net_profit_factors"]["Forex"], 1.0, delta=0.05)
        # The group fallback is the smallest measured factor of the group, so an
        # unmeasured symbol can only ever be understated.
        stocks = [config["symbol_net_profit_factors"][key] for key in ("COSTCO+", "SHOPIFY+")]
        self.assertAlmostEqual(config["group_net_profit_factors"]["Stocks"], min(stocks), places=6)

    def test_unmeasured_symbol_is_skipped(self):
        bad = SymbolSpec(name="BROKEN+", volume_min=1.0, volume_step=1.0, price=0.0)
        factors, skipped = compute_symbol_factors([bad], {"BROKEN+": "Stocks"})
        self.assertEqual(factors, [])
        self.assertEqual(skipped, ["BROKEN+"])

    def test_previous_factor_is_carried_for_symbols_measured_before(self):
        factors, skipped = compute_symbol_factors([_forex_spec()], {"EURUSD.SA": "Forex"})
        config = build_normalization_config(
            factors,
            skipped_symbols=skipped + ["AUDCAD.sa"],
            previous_factors={"AUDCAD.SA": 1.4271},
        )
        self.assertAlmostEqual(config["symbol_net_profit_factors"]["AUDCAD.SA"], 1.4271)
        self.assertEqual(config["carried_symbols"], ["AUDCAD.SA"])
        self.assertEqual(config["skipped_symbols"], [])
        self.assertEqual(config["measured_symbol_count"], 1)

    def test_symbol_never_measured_stays_in_skipped(self):
        factors, _ = compute_symbol_factors([_forex_spec()], {"EURUSD.SA": "Forex"})
        config = build_normalization_config(
            factors, skipped_symbols=["GBXUSD.sa"], previous_factors={"AUDCAD.SA": 1.4}
        )
        self.assertEqual(config["skipped_symbols"], ["GBXUSD.sa"])
        self.assertNotIn("GBXUSD.SA", config["symbol_net_profit_factors"])

    def test_reconstructed_symbols_are_listed(self):
        factors, _ = compute_symbol_factors(
            [_gbp_pair_spec(), _gbx_spec()], {"EURGBP.SA": "Forex", "3IGROUP+": "Stocks"}
        )
        config = build_normalization_config(factors)
        self.assertEqual(config["reconstructed_symbols"], ["3IGROUP+"])


if __name__ == "__main__":
    unittest.main()
