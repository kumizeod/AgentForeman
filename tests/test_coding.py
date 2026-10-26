import copy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from agentforeman.coding import CodingRound
from agentforeman.files import read_json
from agentforeman import engine
from tests import test_campaign as helpers


class CodingTests(unittest.TestCase):
    setUp=helpers.CampaignTests.setUp
    tearDown=helpers.CampaignTests.tearDown

    def create(self):
        self.coding=CodingRound(self.project)
        for m in ('codex-member','claude-member'):self.coding.pool.register(m,m*4)
        cid=self.coding.create('Improve validation under fixed inputs',['codex-member','claude-member'],budget_seconds=120,proposal_seconds=20)
        self.coding.advance(cid);self.coding.advance(cid)
        return cid

    def complete(self,cid,member,kind='good'):
        state=self.coding.get(cid);task=self.coding.pool.claim(member,member)
        source=self.coding.path(cid)/'project'/state['config']['source']
        config=json.loads((source/'config.json').read_text())
        config.update(hidden=32,epochs=500,learning_rate=.3)
        answer={'summary':'TEST code proposal','hypothesis':'TEST larger MLP','base_sha256':state['base_sha256'],
                'files':[{'path':'config.json','content':json.dumps(config)}]}
        if kind=='worse':
            config.update(epochs=1,learning_rate=0);answer['files'][0]['content']=json.dumps(config)
        elif kind=='stale':answer['base_sha256']='stale'
        elif kind=='protected':answer['files'][0]['path']='../trusted/verify.py'
        self.coding.pool.complete(member,task['id'],task['lease'],'success',answer)
        return task

    def finish(self,cid):
        for _ in range(6):
            state=self.coding.advance(cid)
            if state['status']!='running':return state
        self.fail('coding round did not finish')

    def test_independent_candidates_real_training_verification_and_best_retention(self):
        before=(self.project/'workspace'/'config.json').read_bytes()
        cid=self.create();self.complete(cid,'codex-member');self.complete(cid,'claude-member','worse')
        result=self.finish(cid)
        self.assertEqual([c['status'] for c in result['candidates']],['verified','verified'])
        self.assertEqual(result['best']['member'],'codex-member')
        self.assertGreater(result['best']['metric'],result['baseline']['metric'])
        self.assertEqual(before,(self.project/'workspace'/'config.json').read_bytes())
        receipts=list(self.coding.path(cid).rglob('result.json'))
        self.coding.advance(cid)
        self.assertEqual(receipts,list(self.coding.path(cid).rglob('result.json')))

    def test_stale_and_protected_candidates_never_train(self):
        cid=self.create();self.complete(cid,'codex-member','stale');self.complete(cid,'claude-member','protected')
        with patch.object(self.coding,'evaluate',side_effect=AssertionError('trained rejected proposal')):
            result=self.finish(cid)
        self.assertTrue(all(c['status']=='rejected' for c in result['candidates']))
        self.assertIsNone(result['best']['member'])

    def test_controller_crash_after_evaluation_reuses_native_job_receipts(self):
        cid=self.create();self.complete(cid,'codex-member');self.complete(cid,'claude-member','worse')
        self.coding.advance(cid);original=self.coding.save
        with patch.object(self.coding,'save',side_effect=RuntimeError('TEST crash after evaluation')):
            with self.assertRaises(RuntimeError):self.coding.advance(cid)
        receipts={str(p):p.read_bytes() for p in self.coding.path(cid).rglob('result.json')}
        self.coding.advance(cid)
        for path,value in receipts.items():self.assertEqual(Path(path).read_bytes(),value)
        self.assertEqual(self.coding.get(cid)['candidates'][0]['status'],'verified')

    def test_noop_duplicate_paths_and_frozen_tampering_are_rejected(self):
        cid=self.create();state=self.coding.get(cid)
        src=self.coding.path(cid)/'project'/'workspace'/'config.json'
        answer={'summary':'noop','hypothesis':'noop','base_sha256':state['base_sha256'],
                'files':[{'path':'config.json','content':src.read_text()}]}
        with self.assertRaisesRegex(ValueError,'no code changes'):self.coding.apply(state,answer,self.coding.path(cid)/'temp')
        answer['files']*=2
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.coding.apply(state,answer,self.coding.path(cid)/'temp')
        src.write_text(src.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'Frozen'):self.coding.advance(cid)

    def test_stop_cancels_queued_candidates_and_preserves_baseline(self):
        cid=self.create();(self.coding.path(cid)/'stop.request').write_text('stop')
        result=self.coding.advance(cid)
        self.assertEqual(result['stop_reason'],'stop_requested')
        self.assertIsNotNone(result['baseline'])
        self.assertTrue(all(t['status']=='cancelled' for t in self.coding.pool.tasks(cid)))

    def test_best_archive_tampering_is_not_silently_reported_as_verified(self):
        cid=self.create();state=self.coding.get(cid)
        (self.coding.path(cid)/state['best']['archive']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'archive changed'):self.coding.advance(cid)

    def test_failed_agents_leave_baseline_and_explicit_missing_proposal_reason(self):
        cid=self.create()
        for member in ('codex-member','claude-member'):
            task=self.coding.pool.claim(member,member)
            self.coding.pool.complete(member,task['id'],task['lease'],'failed')
        with patch.object(self.coding,'evaluate',side_effect=AssertionError('trained absent candidate')):
            state=self.finish(cid)
        self.assertEqual(state['stop_reason'],'no_code_proposals')
        self.assertEqual(state['candidates'],[])
        self.assertEqual([x['status'] for x in state['proposal_outcomes']],['failed','failed'])
        self.assertIsNone(state['best']['member'])
