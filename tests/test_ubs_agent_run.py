import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ubs_agent_run


class AgentRunTests(unittest.TestCase):
    def test_prepared_mode_receives_the_facade_api(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(
                source_dir=root, output_dir=root, memory=root/'memory.sqlite',
                prepared_manifest=root/'batch.json')
            api = SimpleNamespace(load_mutation_overrides=Mock())
            memory = Mock()
            with (patch.object(ubs_agent_run, '_score_config_from_args', return_value=Mock()),
                  patch.object(ubs_agent_run, 'AgentMemory', return_value=memory),
                  patch('ubs.prepared.run_prepared', return_value=7) as prepared):
                result = ubs_agent_run.run_agent(args, api)
        self.assertEqual(result, 7)
        self.assertIs(prepared.call_args.args[3], api)
        memory.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
