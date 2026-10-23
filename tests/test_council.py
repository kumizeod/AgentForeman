import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agentforeman.council import Council
from agentforeman.files import atomic_json, read_json


class CouncilTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.council=Council(self.root)
        for m in ('a','b','c','chair'):self.council.pool.register(m,m*40)
    def tearDown(self):self.tmp.cleanup()
    def create(self,sources=True,**kwargs):
        return self.council.create('Which falsifiable experiment should we run?',['a','b'],'chair',
            sources=[{'id':'S1','title':'Supplied paper','url':'https://example.org/paper','excerpt':'Supplied unverified evidence'}] if sources else [],
            budget_seconds=600,stage_seconds=60,**kwargs)
    def complete_stage(self,cid,kind=None):
        state=self.council.advance(cid)
        for member in ('a','b','c','chair'):
            task=self.council.pool.claim(member,'session-'+member)
            if not task:continue
            stage=state['stage'];schema=task['request']['schema']
            if stage=='explore':
                self.assertNotIn('answers',json.loads(task['request']['prompt'].split('\n')[-1]))
                result={'answer':'Independent '+member,'claims':[{'statement':'Hypothesis','source_ids':['S1'] if state['sources'] else [],'verification':'Not experimentally verified'}],
                        'risks':['Budget confound'],'experiments':['Run a matched-step baseline']}
            elif stage=='peer_review':
                context=json.loads(task['request']['prompt'].split('\n')[-1]);labels=list(context['answers'])
                self.assertEqual(len(labels),1)
                self.assertNotIn('Independent '+member,str(context))
                result={'evaluations':[{'label':label,'correctness':3,'novelty':2,'impact':4,'feasibility':4,
                         'findings':[{'severity':'major','evidence':'No matched-budget measurement','suggested_test':'Run matched steps'}]} for label in labels],
                        'ranking':labels}
                if kind=='bad_ranking':result['ranking']=['unknown']
            elif stage=='synthesize':
                context=json.loads(task['request']['prompt'].split('\n')[-1])
                self.assertEqual(len(context['required_findings']),2)
                result={'answer':'Chair recommends controlled comparison, not a proven improvement',
                        'claims':[{'statement':'Hypothesis','source_ids':['S1'] if state['sources'] else [],'supporting_answers':[next(iter(context['answers']))]}],
                        'finding_responses':[{'finding_id':f['id'],'resolution':'addressed','explanation':'Plan now matches training steps'} for f in context['required_findings']],
                        'unresolved':[],'experiments':['Run equal training steps'],'confidence':'medium'}
                if kind=='omit_findings':result['finding_responses']=[]
            else:
                self.assertNotEqual(member,'chair')
                result={'verdict':'accept','reasons':['Plan is internally consistent; measurements still pending'],'unsupported_claims':[],'missing_tests':[]}
                if kind=='reject':result.update(verdict='reject',reasons=['Invalid conclusion'])
            self.council.pool.complete(member,task['id'],task['lease'],'success',result)
        return self.council.advance(cid)
    def test_full_council_has_independent_exploration_anonymous_nonself_review_chair_and_audit(self):
        cid=self.create()
        for _ in range(4):state=self.complete_stage(cid)
        self.assertEqual(state['status'],'completed_candidate')
        self.assertFalse(state['quality']['factual_correctness_verified'])
        self.assertFalse(state['quality']['experiments_executed'])
        self.assertEqual(len(self.council.pool.tasks(cid)),6)
        self.assertIn('Chair recommends',(self.council.path(cid)/'report.md').read_text())
        self.assertEqual(read_json(self.council.path(cid)/'transcript.json')['status'],'completed_candidate')
    def test_missing_sources_never_promotes_consensus_to_verified_answer(self):
        cid=self.create(sources=False)
        for _ in range(4):state=self.complete_stage(cid)
        self.assertEqual(state['status'],'needs_evidence')
        self.assertFalse(state['quality']['source_references_complete'])
    def test_chair_omitting_major_findings_cannot_reach_audit(self):
        cid=self.create()
        self.complete_stage(cid);self.complete_stage(cid)
        state=self.complete_stage(cid,'omit_findings')
        self.assertEqual(state['status'],'incomplete')
        self.assertIn('missing_or_unknown_finding_response',str(state['protocol_errors']))
        self.assertNotIn('audit',state['stages'])
    def test_invalid_peer_ranking_blocks_synthesis(self):
        cid=self.create();self.complete_stage(cid)
        state=self.complete_stage(cid,'bad_ranking')
        self.assertEqual(state['status'],'incomplete')
        self.assertNotIn('synthesize',state['stages'])
    def test_independent_audit_can_reject_chair(self):
        cid=self.create()
        for _ in range(3):self.complete_stage(cid)
        state=self.complete_stage(cid,'reject')
        self.assertEqual(state['status'],'rejected')
    def test_controller_restart_enqueues_same_immutable_tasks_and_report_is_repaired(self):
        cid=self.create();before=self.council.pool.tasks(cid)
        for _ in range(3):self.council=Council(self.root);self.council.advance(cid)
        self.assertEqual(self.council.pool.tasks(cid),before)
        for _ in range(4):self.complete_stage(cid)
        report=self.council.path(cid)/'report.md';report.unlink()
        self.council.advance(cid);self.assertTrue(report.exists())
    def test_crash_during_enqueue_reuses_deterministic_requests_and_labels(self):
        cid=self.create();self.complete_stage(cid)
        original=self.council.pool.enqueue;calls=[]
        def crash(*args,**kw):
            value=original(*args,**kw);calls.append(value)
            if len(calls)==1:raise RuntimeError('interrupted after durable enqueue')
            return value
        with patch.object(self.council.pool,'enqueue',side_effect=crash):
            with self.assertRaises(RuntimeError):self.council.advance(cid)
        state=self.council.advance(cid)
        self.assertEqual(len(self.council.pool.tasks(cid)),4)
        self.assertEqual(state['stage'],'peer_review')
    def test_no_quorum_deadline_and_stop_make_no_new_jobs(self):
        for stopped in (True,False):
            cid=self.create();state=self.council.get(cid)
            if stopped:(self.council.path(cid)/'stop.request').write_text('stop')
            else:
                state['stage_deadline']=time.time()-1
                atomic_json(self.council.path(cid)/'state.json',state)
            result=self.council.advance(cid)
            self.assertEqual(result['status'],'stopped' if stopped else 'incomplete')
            self.assertEqual(len(self.council.pool.tasks(cid)),2)
    def test_validation_rejects_shared_chair_duplicates_and_fake_source_shape(self):
        with self.assertRaises(ValueError):self.council.create('q',['a','b'],'a')
        with self.assertRaises(ValueError):self.council.create('q',['a','a'],'chair')
        with self.assertRaises(ValueError):self.council.create('q',['a','b'],'chair',sources=[{'id':'x'}])
