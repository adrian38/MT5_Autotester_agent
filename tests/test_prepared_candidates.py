import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import ubs_agent as agent
from manager_node_runtime import guided_batches as protocol
from tests.test_guided_node import package, symbol_package
from ubs.memory import AgentMemory
from ubs.models import Seed, Variant
from ubs.prepared import run_prepared
from ubs.score import ScoreConfig


class PreparedTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.memory=AgentMemory(self.root/'memory.sqlite');self.addCleanup(self.memory.close)
        self.package=package();item=self.package['candidates'][0]
        parent=self.root/'accepted_parent.set';parent.write_bytes(base64.b64decode(item['parent_b64']))
        seed=Seed(parent,'US30','M15','Client_sets','1')
        old_run=self.memory.create_run(self.root,self.root/'old',1,1,1,True,False)
        self.memory.record_variant(old_run,1,Variant(parent,seed,'US30','M15',(),(),'old'))
        self.memory.conn.execute("insert into candidate_final_tick_6m(candidate_id,run_id,status,evaluated_at) values (1,?,'accepted','now')",(old_run,))
        self.memory.conn.commit()
        directory=protocol.store_batch(self.root,self.package,'ICTRADING','STANDARD')
        with mock.patch.object(sys,'argv',['ubs_agent.py']):
            self.args=agent.parse_args()
        self.args.prepared_manifest=directory/'batch.json';self.args.broker='ICTRADING';self.args.account_type='STANDARD'
        self.args.symbol_map=''
        self.args.output_dir=self.root/'outputs';self.args.execute_backtests=True;self.args.dry_run=False
        self.api=SimpleNamespace(**vars(agent));self.api.BASE_DIR=self.root
        self.api.broker_universe_symbols=lambda args:{'US30','EURUSD'}
        self.api.load_disabled_symbols=lambda path:set()
        self.api.target_timeframe_universe=lambda *args,**kwargs:['M15']
        self.api.load_mutation_overrides=lambda:({},set());self.api.load_global_params=lambda:{}
        self.api.evaluate_generation=mock.Mock(return_value=[])
        self.api.create_variant=mock.Mock(side_effect=AssertionError('Must not remutate'))

    def test_exact_set_enters_normal_evaluator_and_retry_does_not_create_another_run(self):
        self.assertEqual(run_prepared(self.args,self.memory,ScoreConfig(),self.api),0)
        self.api.evaluate_generation.assert_called_once()
        _,_,run_dir,_,variants,_=self.api.evaluate_generation.call_args.args
        self.assertEqual(variants[0].path.read_bytes(),base64.b64decode(self.package['candidates'][0]['set_b64']))
        self.assertEqual(variants[0].mutated_keys,('ATR_Period',))
        self.api.create_variant.assert_not_called()
        self.assertEqual(run_prepared(self.args,self.memory,ScoreConfig(),self.api),0)
        self.api.evaluate_generation.assert_called_once()
        self.assertEqual(self.memory.conn.execute('select count(*) from runs').fetchone()[0],2)
        result=protocol.results(self.root,self.package['batch_id'],self.memory.path)
        self.assertEqual(result['positives'],0)
        self.assertIsNone(result['candidates'][0]['final_6m'])

    def test_parent_rejection_prevents_new_run(self):
        self.memory.conn.execute("update candidate_final_tick_6m set status='rejected'");self.memory.conn.commit()
        with self.assertRaisesRegex(ValueError,'positivo final'):
            run_prepared(self.args,self.memory,ScoreConfig(),self.api)
        self.assertEqual(self.memory.conn.execute('select count(*) from runs').fetchone()[0],1)

    def test_current_universe_disables_import_without_relaxation(self):
        self.api.load_disabled_symbols=lambda path:{'US30'}
        with self.assertRaisesRegex(ValueError,'bloqueado'):
            run_prepared(self.args,self.memory,ScoreConfig(),self.api)
        self.api.evaluate_generation.assert_not_called()

    def test_parent_file_modification_prevents_import(self):
        p=self.root/'accepted_parent.set';p.write_bytes(p.read_bytes().replace(b'ATR_Period=10',b'ATR_Period=12'))
        with self.assertRaisesRegex(ValueError,'no coincide'):
            run_prepared(self.args,self.memory,ScoreConfig(),self.api)

    def test_unseen_enabled_symbol_enters_evaluator_without_numeric_remutation(self):
        self.package=symbol_package();directory=protocol.store_batch(self.root,self.package,'ICTRADING','STANDARD')
        self.args.prepared_manifest=directory/'batch.json'
        self.assertEqual(run_prepared(self.args,self.memory,ScoreConfig(),self.api),0)
        variants=self.api.evaluate_generation.call_args.args[4]
        self.assertEqual((variants[0].target_symbol,variants[0].mutated_keys),('EURUSD',('ForceSymbol',)))

    def test_ictrading_execution_preserves_broker_case_and_original_package(self):
        for symbol in ('TecDE30', 'MidDE50'):
            with self.subTest(symbol=symbol):
                value = symbol_package()
                item = value['candidates'][0]
                parent = base64.b64decode(item['parent_b64'])
                raw = parent.replace(b'ForceSymbol=US30', ('ForceSymbol='+symbol.upper()).encode())
                item.update(
                    target_symbol=symbol.upper(),
                    mutation={'kind':'symbol_exploration','key':'ForceSymbol','old':'US30','new':symbol.upper()},
                    fingerprint=protocol.fingerprint('ICTRADING','STANDARD',symbol.upper(),'M15',protocol.set_params(raw)),
                    set_sha256=protocol.digest(raw),
                    set_b64=base64.b64encode(raw).decode(),
                )
                value['batch_id'] = protocol.batch_identity(value)
                directory = protocol.store_batch(self.root,value,'ICTRADING','STANDARD')
                original_manifest = (directory/'batch.json').read_bytes()
                self.args.prepared_manifest = directory/'batch.json'
                self.api.broker_universe_symbols = lambda args:{symbol}
                self.assertEqual(run_prepared(self.args,self.memory,ScoreConfig(),self.api),0)
                variant = self.api.evaluate_generation.call_args.args[4][0]
                actual = protocol.set_params(variant.path.read_bytes())
                expected = protocol.set_params(raw)
                expected['ForceSymbol'] = symbol
                self.assertEqual(actual, expected)
                self.assertEqual(variant.target_symbol, symbol)
                self.assertEqual(variant.mutation_details[0]['new'], symbol)
                self.assertEqual((directory/(item['fingerprint']+'.set')).read_bytes(), raw)
                self.assertEqual((directory/'batch.json').read_bytes(), original_manifest)
                before = self.memory.conn.execute('select count(*) from candidates').fetchone()[0]
                self.assertEqual(run_prepared(self.args,self.memory,ScoreConfig(),self.api),0)
                self.assertEqual(self.memory.conn.execute('select count(*) from candidates').fetchone()[0], before)

    def test_ictrading_numeric_candidate_keeps_mutation_and_repairs_symbol(self):
        value = package()
        item = value['candidates'][0]
        parent = base64.b64decode(item['parent_b64']).replace(b'US30', b'TECDE30')
        raw = base64.b64decode(item['set_b64']).replace(b'US30', b'TECDE30')
        (self.root/'accepted_parent.set').write_bytes(parent)
        item.update(target_symbol='TECDE30',
                    fingerprint=protocol.fingerprint('ICTRADING','STANDARD','TECDE30','M15',protocol.set_params(raw)),
                    parent_sha256=protocol.digest(parent),parent_b64=base64.b64encode(parent).decode(),
                    set_sha256=protocol.digest(raw),set_b64=base64.b64encode(raw).decode())
        value['batch_id'] = protocol.batch_identity(value)
        directory = protocol.store_batch(self.root,value,'ICTRADING','STANDARD')
        self.args.prepared_manifest = directory/'batch.json'
        self.api.broker_universe_symbols = lambda args:{'TecDE30'}
        self.assertEqual(run_prepared(self.args,self.memory,ScoreConfig(),self.api),0)
        variant = self.api.evaluate_generation.call_args.args[4][0]
        expected = protocol.set_params(raw)
        expected['ForceSymbol'] = 'TecDE30'
        self.assertEqual(protocol.set_params(variant.path.read_bytes()), expected)
        self.assertEqual(variant.mutated_keys, ('ATR_Period',))
        self.assertEqual(variant.mutation_details[0]['new'], 11.0)
        self.assertEqual((self.root/'accepted_parent.set').read_bytes(), parent)

    def test_axi_prepared_symbol_uses_exact_universe_casing(self):
        self._check_other_broker_spelling('AXI', 'Apple+')

    def test_roboforex_prepared_execution_is_unchanged(self):
        self._check_other_broker_spelling('ROBOFOREX', 'APPLE+')

    def _check_other_broker_spelling(self, broker, expected):
        value = symbol_package()
        item = value['candidates'][0]
        parent = base64.b64decode(item['parent_b64'])
        raw = parent.replace(b'ForceSymbol=US30', b'ForceSymbol=APPLE+')
        item.update(
            target_symbol='APPLE+',
            mutation={'kind':'symbol_exploration','key':'ForceSymbol','old':'US30','new':'APPLE+'},
            fingerprint=protocol.fingerprint(broker,'STANDARD','APPLE+','M15',protocol.set_params(raw)),
            set_sha256=protocol.digest(raw),
            set_b64=base64.b64encode(raw).decode(),
        )
        value['broker'] = broker
        value['batch_id'] = protocol.batch_identity(value)
        directory = protocol.store_batch(self.root,value,broker,'STANDARD')
        self.args.prepared_manifest = directory/'batch.json'
        self.args.broker = broker
        self.api.broker_universe_symbols = lambda args:{'Apple+'}

        self.assertEqual(run_prepared(self.args,self.memory,ScoreConfig(),self.api),0)

        variants = self.api.evaluate_generation.call_args.args[4]
        self.assertEqual(variants[0].target_symbol,expected)
        self.assertEqual(variants[0].mutation_details[0]['new'],expected)
        self.assertEqual(variants[0].path.read_bytes(), raw)


if __name__=='__main__':unittest.main()
