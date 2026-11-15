import json
import tempfile
import unittest
from pathlib import Path

from tests import PROJECT_ROOT
from ubs.agent import config as agent_config
from ubs.memory import AgentMemory


class UBSRunConfigTests(unittest.TestCase):
    def test_agent_base_dir_remains_the_repository_root(self) -> None:
        self.assertEqual(agent_config.BASE_DIR, PROJECT_ROOT)

    def test_create_run_persists_config_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            memory = AgentMemory(Path(temp_dir) / "memory.sqlite")
            try:
                run_id = memory.create_run(
                    Path("sets"),
                    Path("outputs/run"),
                    generations=3,
                    variants_per_seed=5,
                    max_seeds=20,
                    execute_backtests=True,
                    dry_run=False,
                    config={
                        "generation": {"force_unseeded_universe": True},
                        "execution": {"from_date": "2020.01.01", "to_date": "2024.12.31"},
                    },
                )

                row = memory.conn.execute("select config_json from runs where id=?", (run_id,)).fetchone()
                self.assertIsNotNone(row)
                config = json.loads(row["config_json"])
                self.assertTrue(config["generation"]["force_unseeded_universe"])
                self.assertEqual(config["execution"]["from_date"], "2020.01.01")
            finally:
                memory.close()

