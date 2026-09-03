import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ubs.mt5_symbol_extract import AssetUniverseSyncResult, ExtractedSymbol, SymbolExtractionResult
from ui.ubs_universe_logic import UBSUniverseLogicMixin


class StatusVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class UniverseSyncHarness(UBSUniverseLogicMixin):
    """Expone solo lo que toca el flujo de sincronizacion, sin Tk."""

    def __init__(self, policy_path: Path, removed: tuple[str, ...]) -> None:
        self.status_text = StatusVar()
        self.ubs_universe_checked = set()
        self.refreshed = 0
        self._policy_path = policy_path
        self._removed = removed
        self._memory = policy_path.with_suffix(".sqlite")
        self.info_messages: list[str] = []

    # --- ganchos del mixin ---
    def _disabled_symbols_path(self) -> Path:
        return self._policy_path

    def _ubs_memory_path(self) -> Path:
        return self._memory

    def _ubs_broker(self) -> str:
        return "ICTRADING"

    def _ubs_account_type(self) -> str:
        return "STANDARD"

    def _ubs_trade_mode_snapshot_path(self) -> Path:
        return self._policy_path.parent / "trade_modes.json"

    def _load_ubs_asset_universe(self):
        return [], {"US100": "USTEC"}

    def _refresh_ubs_universe(self) -> None:
        self.refreshed += 1

    def update_idletasks(self) -> None:
        pass

    def _ask_mt5_symbol_extract_credentials(self):
        return {"mt5_path": "", "login": None, "password": "", "server": ""}

    def _extract_mt5_universe_into_asset_file(self, title, confirm_message):
        self.confirm_message = confirm_message
        extraction = SymbolExtractionResult(
            symbols=(), terminal_path=None, account_login=11637157, server="Broker-MT5-4"
        )
        sync_result = AssetUniverseSyncResult(
            backup_path=Path("assets/x.ini.bak_1"),
            counts={"Forex": 2, "Stocks": 3},
            added_symbols=("NEW1", "NEW2"),
            removed_symbols=self._removed,
        )
        return extraction, sync_result


def write_policy(path: Path, disabled: list[str], seed_enabled: list[str] | None = None) -> None:
    path.write_text(
        json.dumps({"disabled": disabled, "seed_enabled_when_disabled": seed_enabled or []}),
        encoding="utf-8",
    )


class SyncMt5UniverseSymbolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.policy = Path(self.temp.name) / "ubs_disabled_symbols_ICTRADING_STANDARD.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_sync(self, removed: tuple[str, ...]) -> UniverseSyncHarness:
        harness = UniverseSyncHarness(self.policy, removed)
        with (
            patch("ui.ubs_universe_logic.messagebox.showinfo") as info,
            patch("ui.ubs_universe_logic.messagebox.showerror") as error,
        ):
            harness._sync_mt5_universe_symbols()
        harness.info_messages = [call.args[1] for call in info.call_args_list]
        self.assertFalse(error.called)
        return harness

    def test_disables_removed_symbols_on_top_of_existing_policy(self) -> None:
        write_policy(self.policy, ["OLD.NAS"], ["XAUUSD"])

        harness = self.run_sync(("EEX.NYSE", "Corn_U6"))

        policy = json.loads(self.policy.read_text(encoding="utf-8"))
        # Union con lo que ya habia: nada se pierde.
        self.assertEqual(policy["disabled"], ["CORN_U6", "EEX.NYSE", "OLD.NAS"])
        self.assertEqual(harness.refreshed, 1)
        self.assertIn("Deshabilitados en GEN ahora: 2", harness.info_messages[0])

    def test_keeps_seed_enabled_exception_for_untouched_symbols(self) -> None:
        write_policy(self.policy, ["XAUUSD"], ["XAUUSD"])

        self.run_sync(("EEX.NYSE",))

        policy = json.loads(self.policy.read_text(encoding="utf-8"))
        self.assertEqual(policy["seed_enabled_when_disabled"], ["XAUUSD"])

    def test_drops_seed_enabled_exception_for_a_retired_symbol(self) -> None:
        write_policy(self.policy, ["EEX.NYSE"], ["EEX.NYSE"])

        self.run_sync(("EEX.NYSE", "Corn_U6"))

        policy = json.loads(self.policy.read_text(encoding="utf-8"))
        self.assertEqual(policy["disabled"], ["CORN_U6", "EEX.NYSE"])
        self.assertEqual(policy["seed_enabled_when_disabled"], [])

    def test_writes_a_backup_before_overwriting_the_policy(self) -> None:
        write_policy(self.policy, ["OLD.NAS"])

        self.run_sync(("EEX.NYSE",))

        backups = list(self.policy.parent.glob(f"{self.policy.name}.bak_*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8"))["disabled"], ["OLD.NAS"])

    def test_no_removed_symbols_leaves_policy_untouched(self) -> None:
        write_policy(self.policy, ["OLD.NAS"])
        before = self.policy.read_text(encoding="utf-8")

        harness = self.run_sync(())

        self.assertEqual(self.policy.read_text(encoding="utf-8"), before)
        self.assertEqual(list(self.policy.parent.glob(f"{self.policy.name}.bak_*")), [])
        self.assertIn("Deshabilitados en GEN ahora: 0", harness.info_messages[0])

    def test_message_points_at_the_next_step(self) -> None:
        write_policy(self.policy, [])

        harness = self.run_sync(("EEX.NYSE",))

        self.assertIn("Probar history GEN", harness.info_messages[0])
        self.assertIn("Deshabilitar simbolos sin history", harness.info_messages[0])
        self.assertIn("No lanza backtests", harness.confirm_message)


class DisabledPolicyBackupPruneTests(unittest.TestCase):
    def test_keeps_only_the_newest_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = Path(temp_dir) / "policy.json"
            write_policy(policy, ["A"])
            for stamp in range(1, 15):
                (policy.parent / f"{policy.name}.bak_202608{stamp:02d}_000000").write_text("{}", encoding="utf-8")
            harness = UniverseSyncHarness(policy, ())

            harness._prune_disabled_symbols_backups(policy, keep=10)

            remaining = sorted(p.name for p in policy.parent.glob(f"{policy.name}.bak_*"))
            self.assertEqual(len(remaining), 10)
            # Se conservan los mas recientes por nombre (timestamp).
            self.assertEqual(remaining[0], f"{policy.name}.bak_20260805_000000")


class TradeDisabledPolicyTests(unittest.TestCase):
    def test_button_queries_live_trade_modes_and_ignores_journals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = Path(temp_dir) / "policy.json"
            write_policy(policy, ["OLD"])
            harness = UniverseSyncHarness(policy, ())
            extraction = SymbolExtractionResult(
                symbols=(
                    ExtractedSymbol("US100", trade_mode=3),
                    ExtractedSymbol("EURUSD", trade_mode=4),
                ),
                terminal_path=None,
                account_login=11637157,
                server="Broker-MT5",
            )

            with (
                patch("ui.ubs_universe_logic.extract_symbols_from_mt5", return_value=extraction) as extract,
                patch("ui.ubs_universe_logic.messagebox.askyesno", return_value=True) as confirm,
            ):
                harness._disable_trade_disabled_universe_symbols()

            extract.assert_called_once_with(terminal_path=None)
            saved = json.loads(policy.read_text(encoding="utf-8"))
            self.assertEqual(saved["disabled"], ["OLD", "USTEC"])
            self.assertEqual(harness.refreshed, 1)
            self.assertIn("1 nuevos", harness.status_text.value)
            confirm.assert_called_once()
            self.assertIn("DISABLED o CLOSEONLY", confirm.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
