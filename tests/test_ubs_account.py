import unittest
from pathlib import Path

from ubs.account import account_memory_path, account_output_dir, account_seed_dir, normalize_account_type
from ui.ubs_agent_logic import UBSAgentLogicMixin


class _FakeVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _FakeAgent(UBSAgentLogicMixin):
    def __init__(self, account_type: str, source: str, output: str, set_file: str = "") -> None:
        self.ubs_account_type = _FakeVar(account_type)
        self.set_files_root = _FakeVar(source)
        self.ubs_generation_output = _FakeVar(output)
        self.ubs_set_file = _FakeVar(set_file)

    def _ubs_account_type(self) -> str:
        return normalize_account_type(self.ubs_account_type.get())


class UBSAccountTests(unittest.TestCase):
    def test_normalize_account_type_defaults_to_ecn(self) -> None:
        self.assertEqual(normalize_account_type("pro"), "PRO")
        self.assertEqual(normalize_account_type("ECN"), "ECN")
        self.assertEqual(normalize_account_type(""), "ECN")
        self.assertEqual(normalize_account_type("demo"), "ECN")

    def test_account_paths_are_scoped_per_account(self) -> None:
        base = Path("project")

        self.assertEqual(account_memory_path(base, "PRO"), base / "outputs" / "ubs_memory_PRO.sqlite")
        self.assertEqual(account_output_dir(base, "ECN"), base / "outputs" / "ubs_agent" / "ECN")
        self.assertEqual(account_seed_dir(base, "PRO"), base / "sets" / "ubs_ready" / "PRO")

    def test_sync_switches_previous_account_defaults_to_active_account(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        agent = _FakeAgent(
            "PRO",
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN"),
            str(BASE_DIR / "outputs" / "ubs_agent" / "ECN"),
        )

        agent._sync_ubs_account_paths()

        self.assertEqual(agent.set_files_root.get(), str(BASE_DIR / "sets" / "ubs_ready" / "PRO"))
        self.assertEqual(agent.ubs_generation_output.get(), str(BASE_DIR / "outputs" / "ubs_agent" / "PRO"))

    def test_sync_keeps_custom_paths(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        custom_source = str(BASE_DIR / "custom_sets")
        custom_output = str(BASE_DIR / "custom_output")
        agent = _FakeAgent("PRO", custom_source, custom_output)

        agent._sync_ubs_account_paths()

        self.assertEqual(agent.set_files_root.get(), custom_source)
        self.assertEqual(agent.ubs_generation_output.get(), custom_output)

    def test_force_sync_replaces_custom_paths(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        agent = _FakeAgent(
            "PRO",
            str(BASE_DIR / "custom_sets"),
            str(BASE_DIR / "custom_output"),
        )

        agent._sync_ubs_account_paths(force=True)

        self.assertEqual(agent.set_files_root.get(), str(BASE_DIR / "sets" / "ubs_ready" / "PRO"))
        self.assertEqual(agent.ubs_generation_output.get(), str(BASE_DIR / "outputs" / "ubs_agent" / "PRO"))

    def test_maps_legacy_single_set_to_active_account(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        agent = _FakeAgent(
            "PRO",
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN"),
            str(BASE_DIR / "outputs" / "ubs_agent" / "ECN"),
            str(BASE_DIR / "sets" / "ubs_ready" / "XAUUSD" / "H1" / "seed.set"),
        )

        mapped = agent._account_scoped_set_file_path(agent.ubs_set_file.get())

        self.assertEqual(
            mapped,
            BASE_DIR / "sets" / "ubs_ready" / "PRO" / "XAUUSD" / "H1" / "seed.set",
        )

    def test_maps_previous_account_single_set_to_active_account(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        agent = _FakeAgent(
            "PRO",
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN"),
            str(BASE_DIR / "outputs" / "ubs_agent" / "ECN"),
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN" / "XAUUSD" / "H1" / "seed.set"),
        )

        mapped = agent._account_scoped_set_file_path(agent.ubs_set_file.get())

        self.assertEqual(
            mapped,
            BASE_DIR / "sets" / "ubs_ready" / "PRO" / "XAUUSD" / "H1" / "seed.set",
        )

    def test_force_sync_clears_missing_account_set_file(self) -> None:
        from ui.ubs_agent_logic import BASE_DIR

        agent = _FakeAgent(
            "PRO",
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN"),
            str(BASE_DIR / "outputs" / "ubs_agent" / "ECN"),
            str(BASE_DIR / "sets" / "ubs_ready" / "ECN" / "XAUUSD" / "H1" / "missing.set"),
        )

        agent._sync_ubs_account_paths(force=True)

        self.assertEqual(agent.ubs_set_file.get(), "")


if __name__ == "__main__":
    unittest.main()
