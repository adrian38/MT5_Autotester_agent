from __future__ import annotations

import configparser
import tempfile
import unittest
from pathlib import Path

from manager_node_runtime.node_settings import memory_path


class ManagerNodeSettingsTests(unittest.TestCase):
    def test_split_agent_facade_uses_the_scoped_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "ubs_agent.py").write_text(
                "from ubs_agent_cli import main\n",
                encoding="utf-8",
            )
            config = {
                "project_dir": str(project),
                "broker": "ICTRADING",
                "account_type": "STANDARD",
            }

            resolved = memory_path(config, configparser.ConfigParser())

        self.assertEqual(
            resolved,
            project / "outputs" / "ubs_memory_ICTRADING_STANDARD.sqlite",
        )

    def test_legacy_parser_without_broker_keeps_the_legacy_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "ubs_agent.py").write_text(
                'OPTIONS = ["--memory", "--generations"]\n',
                encoding="utf-8",
            )

            resolved = memory_path(
                {"project_dir": str(project)},
                configparser.ConfigParser(),
            )

        self.assertEqual(resolved, project / "outputs" / "ubs_memory.sqlite")


if __name__ == "__main__":
    unittest.main()
