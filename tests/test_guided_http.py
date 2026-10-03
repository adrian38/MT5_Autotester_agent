"""Real HTTP manager -> embedded node -> prepared entry -> result return, no MT5."""
import json
import sys
import os
import subprocess
import time
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tests import test_prepared_candidates as prepared_fixture
from manager_node_runtime.node import JobController, NodeHandler
from ubs.prepared import run_prepared
from ubs.score import ScoreConfig
from manager_node_runtime import guided_batches as protocol


MANAGER_SCRIPT = '''import json,sys
from pathlib import Path
from http.server import ThreadingHTTPServer
from mt5_manager.manager import ManagerHandler
server=ThreadingHTTPServer(('127.0.0.1',0),ManagerHandler)
server.nodes=json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(str(server.server_port))
server.serve_forever()
'''


class GuidedHTTPTests(unittest.TestCase):
    def _start_node(self, fixture, root):
        config = {
            'node_id': 'ic', 'project_dir': str(root), 'broker': 'ICTRADING',
            'account_type': 'STANDARD', 'token': 'test-secret',
            'memory_path': str(fixture.memory.path),
        }
        controller = JobController(config, root / 'node.json')
        node = ThreadingHTTPServer(('127.0.0.1', 0), NodeHandler)
        node.controller = controller
        thread = threading.Thread(target=node.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(node.server_close)
        self.addCleanup(node.shutdown)
        return controller, node

    def _start_manager(self, manager_root, root, nodes):
        config_path = root / 'manager-test.json'
        config_path.write_text(json.dumps(nodes))
        port_path = root / 'manager-test.port'
        log = (root / 'manager-test.log').open('w')
        self.addCleanup(log.close)
        process = subprocess.Popen(
            [sys.executable, '-u', '-c', MANAGER_SCRIPT, str(config_path), str(port_path)],
            cwd=manager_root, env={**os.environ, 'PYTHONPATH': str(manager_root)},
            stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        self.addCleanup(lambda: (process.terminate(), process.wait(timeout=5)))
        deadline = time.monotonic() + 8
        while not port_path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(port_path.exists(), (root / 'manager-test.log').read_text())
        return f'http://127.0.0.1:{port_path.read_text()}/api/nodes/ic/guided-batches'

    @staticmethod
    def _post(endpoint, payload):
        request = urllib.request.Request(
            endpoint, json.dumps(payload).encode(), headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    def test_manager_routes_to_embedded_node_without_remutation_and_returns_results(self):
        fixture = prepared_fixture.PreparedTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        manager_root = Path(__file__).resolve().parents[3] / 'MT5_Autotester_agent_manager'
        if not manager_root.is_dir():
            self.skipTest('Manager checkout not available for transport integration')
        root = fixture.root
        (root / 'ubs_agent.py').write_text('print("stub")')
        (root / 'ui_settings.ini').write_text(
            '[Paths]\nubs_ex5_file=ubs.ex5\n[General]\nubs_generation_mode=discovery\n'
        )
        controller, node = self._start_node(fixture, root)
        nodes = [{'id': 'ic', 'url': f'http://127.0.0.1:{node.server_port}', 'token': 'test-secret',
                  'portfolio_project_dir': str(root), 'portfolio_broker': 'ICTRADING',
                  'portfolio_account_type': 'STANDARD'}]
        endpoint = self._start_manager(manager_root, root, nodes)
        status = {'node': {'broker': 'ICTRADING', 'account_type': 'STANDARD', 'project_dir': str(root)},
                  'capabilities': {'guided_batches_v1': True, 'guided_launch_options_v1': True}}
        with mock.patch.object(controller, 'status', return_value=status), \
             mock.patch.object(controller, '_schedule_queue_drain'), mock.patch.object(NodeHandler, 'log_message'):
            launch = {'max_workers': 5, 'repair_after_generation': True, 'repair_max_workers': 4,
                      'repair_phase2_max_workers': 1, 'repair_attempts': 2}
            submission = {'package': fixture.package, 'launch_options': launch}
            self.assertFalse(self._post(endpoint, submission)['duplicate'])
            self.assertTrue(self._post(endpoint, submission)['duplicate'])
            self.assertEqual(len(controller.queue), 1)
            with mock.patch.object(controller, '_launch_step'):
                controller._start_generation(controller.queue.pop()['payload'])
            pipeline = controller.state['pipeline']
            self.assertEqual(pipeline[0]['action'],'generation')
            repairs = [step for step in pipeline if 'phase' in step]
            self.assertEqual({step['attempt'] for step in repairs},{1,2})
            self.assertEqual({step['max_workers'] for step in repairs if step['phase']==1},{4})
            self.assertEqual({step['max_workers'] for step in repairs if step['phase']==2},{1})
            run_prepared(fixture.args, fixture.memory, ScoreConfig(), fixture.api)
            fixture.api.create_variant.assert_not_called()
            checkpoint = protocol.read_run(root, fixture.package['batch_id'])
            candidate_id = next(iter(checkpoint['candidate_ids'].values()))
            # Synthetic stage result: checks transport/attribution only, never real profitability.
            fixture.memory.conn.execute("insert into candidate_final_tick_6m(candidate_id,run_id,status,evaluated_at) values (?,?,'accepted','test')",(candidate_id,checkpoint['run_id']))
            fixture.memory.conn.commit()
            controller.state['status'] = 'completed'
            controller.guided_completed()
            with urllib.request.urlopen(endpoint + '/' + fixture.package['batch_id'], timeout=5) as response:
                result = json.loads(response.read())
            self.assertEqual(result['run_id'],checkpoint['run_id'])
            self.assertEqual(result['candidates'][0]['fingerprint'],fixture.package['candidates'][0]['fingerprint'])
            self.assertEqual(result['positives'],1)
            self.assertEqual(result['receipt']['status'],'completed')


if __name__=='__main__':unittest.main()
