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


class GuidedHTTPTests(unittest.TestCase):
    def test_manager_routes_to_embedded_node_without_remutation_and_returns_results(self):
        fixture=prepared_fixture.PreparedTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        manager_root=Path(__file__).resolve().parents[3]/'MT5_Autotester_agent_manager'
        if not manager_root.is_dir():
            self.skipTest('Manager checkout not available for transport integration')
        root=fixture.root
        (root/'ubs_agent.py').write_text('print("stub")')
        (root/'ui_settings.ini').write_text('[Paths]\nubs_ex5_file=ubs.ex5\n[General]\nubs_generation_mode=discovery\n')
        config={'node_id':'ic','project_dir':str(root),'broker':'ICTRADING','account_type':'STANDARD',
                'token':'test-secret','memory_path':str(fixture.memory.path)}
        controller=JobController(config,root/'node.json')
        # Fixture already pinned the batch; it has no receipt/run and may enqueue once.
        node=ThreadingHTTPServer(('127.0.0.1',0),NodeHandler);node.controller=controller
        nodes=[{'id':'ic','url':f'http://127.0.0.1:{node.server_port}','token':'test-secret',
                        'portfolio_project_dir':str(root),'portfolio_broker':'ICTRADING','portfolio_account_type':'STANDARD'}]
        for server in (node,):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        # Manager and agent have different portfolio_manager modules; mirror the
        # deployed process boundary instead of mixing both packages in one Python.
        config_path=root/'manager-test.json';config_path.write_text(json.dumps(nodes))
        port_path=root/'manager-test.port'
        script='''import json,sys
from pathlib import Path
from http.server import ThreadingHTTPServer
from mt5_manager.manager import ManagerHandler
server=ThreadingHTTPServer(('127.0.0.1',0),ManagerHandler)
server.nodes=json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(str(server.server_port))
server.serve_forever()
'''
        log=(root/'manager-test.log').open('w');self.addCleanup(log.close)
        process=subprocess.Popen([sys.executable,'-u','-c',script,str(config_path),str(port_path)],
                    cwd=manager_root,env={**os.environ,'PYTHONPATH':str(manager_root)},stdout=log,stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        def stop_manager():
            process.terminate();process.wait(timeout=5)
        self.addCleanup(stop_manager)
        deadline=time.monotonic()+8
        while not port_path.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue(port_path.exists(),(root/'manager-test.log').read_text())
        endpoint=f'http://127.0.0.1:{port_path.read_text()}/api/nodes/ic/guided-batches'
        def post(payload):
            req=urllib.request.Request(endpoint,json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=5) as response:return json.loads(response.read())
        status={'node':{'broker':'ICTRADING','account_type':'STANDARD','project_dir':str(root)},'capabilities':{'guided_batches_v1':True}}
        with mock.patch.object(controller,'status',return_value=status),mock.patch.object(controller,'_schedule_queue_drain'), \
             mock.patch.object(NodeHandler,'log_message'):
            first=post(fixture.package);second=post(fixture.package)
            self.assertFalse(first['duplicate']);self.assertTrue(second['duplicate'])
            self.assertEqual(len(controller.queue),1)
            with mock.patch.object(controller,'_launch_step'):
                controller._start_generation(controller.queue.pop()['payload'])
            self.assertEqual([x['action'] for x in controller.state['pipeline']],['generation','robustness','final_tick','final_tick_6m'])
            run_prepared(fixture.args,fixture.memory,ScoreConfig(),fixture.api)
            fixture.api.create_variant.assert_not_called()
            checkpoint=protocol.read_run(root,fixture.package['batch_id']);candidate_id=next(iter(checkpoint['candidate_ids'].values()))
            # Synthetic stage result: checks transport/attribution only, never real profitability.
            fixture.memory.conn.execute("insert into candidate_final_tick_6m(candidate_id,run_id,status,evaluated_at) values (?,?,'accepted','test')",(candidate_id,checkpoint['run_id']))
            fixture.memory.conn.commit()
            controller.state['status']='completed';controller.guided_completed()
            with urllib.request.urlopen(endpoint+'/'+fixture.package['batch_id'],timeout=5) as response:
                result=json.loads(response.read())
            self.assertEqual(result['run_id'],checkpoint['run_id'])
            self.assertEqual(result['candidates'][0]['fingerprint'],fixture.package['candidates'][0]['fingerprint'])
            self.assertEqual(result['positives'],1)
            self.assertEqual(result['receipt']['status'],'completed')


if __name__=='__main__':unittest.main()
