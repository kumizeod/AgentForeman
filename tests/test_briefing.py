import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from agentforeman.briefing import Briefing
from agentforeman.cooperate import Cooperation
from agentforeman.debate import Debate
from agentforeman.pool import Pool
from agentforeman.files import read_json


def contract(manual=False):
    return {'goal':'Calculate supplied sales total','inputs':['Supplied sales rows'],
            'deliverables':[{'id':'answer','description':'Human-readable explanation'},
                            {'id':'report','description':'JSON with integer total'}],
            'acceptance':[{'criterion':'Total equals the supplied row sum',
                           'verification':'manual' if manual else 'automated','check_ids':[] if manual else ['total']}],
            'constraints':['Use supplied rows only'],'priorities':['Correctness before speed'],
            'exclusions':['No external data collection'],
            'autonomy':{'allowed_decisions':['Choose wording and task distribution'],
                        'pause_conditions':['Changing the goal or acceptance protocol'],
                        'defaults':['Describe input limitations']}}


def reply(ready=True,manual=False):
    return {'reply':'Ready for human review' if ready else 'Please clarify output requirements',
            'questions':[] if ready else ['Should the output include JSON?'],
            'contract':contract(manual),'open_questions':[] if ready else ['Output format'], 'ready':ready}


class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.project=Path(self.tmp.name);self.c=Briefing(self.project)
        for m in ('author','critic','worker','chair'):self.c.pool.register(m,(m+'-')*10)
    def tearDown(self):self.tmp.cleanup()
    def execution(self,cooperative=False):
        value={'mode':'cooperative' if cooperative else 'adversarial',
               'members':['author','worker'] if cooperative else ['author','critic'],
               'chair':'chair','budget_seconds':120,'stage_seconds':30,'max_rounds':2}
        if cooperative:value['reviewers']=['critic']
        return value
    def create(self,**kwargs):
        return self.c.create('Analyze sales',self.execution(kwargs.pop('cooperative',False)),
            checks=[{'id':'total','kind':'json_value','artifact_id':'report','pointer':'/total','expected':60}],
            turn_seconds=30,**kwargs)
    def chair(self,bid,value=None):
        self.c.advance(bid);task=self.c.pool.claim('chair','chair')
        self.assertIsNotNone(task)
        self.c.pool.complete('chair',task['id'],task['lease'],'success',value or reply())
        return self.c.advance(bid)
    def confirm(self,bid):
        state=self.c.get(bid);return self.c.confirm(bid,state['version'],state['sha256'])
    def test_only_chair_is_dispatched_before_explicit_human_confirmation(self):
        bid=self.create();self.assertIsNone(self.c.pool.claim('author','author'))
        self.assertEqual(Debate(self.project).list(),[])
        state=self.chair(bid)
        self.assertEqual(state['status'],'ready');self.assertEqual(Debate(self.project).list(),[])
        for _ in range(2):Briefing(self.project).advance(bid)
        self.assertEqual(Debate(self.project).list(),[])
        state=self.confirm(bid);child=Debate(self.project).get(state['launch']['id'])
        self.assertEqual(child['task_contract']['sha256'],state['sha256'])
        self.assertIn('task_contract',child['task_specs'][0]['request']['prompt'])
        self.assertIsNotNone(self.c.pool.claim('author','author'))
    def test_question_reply_ready_and_no_implicit_confirmation(self):
        bid=self.create();state=self.chair(bid,reply(False))
        with self.assertRaises(ValueError):self.confirm(bid)
        self.c.message(bid,state['version'],'Yes, return JSON and an explanation.')
        state=self.chair(bid)
        self.assertEqual(state['status'],'ready');self.assertEqual(len(state['messages']),4)
        self.assertEqual(Debate(self.project).list(),[])
    def test_human_edits_new_version_and_stale_confirmation_is_rejected(self):
        bid=self.create();old=self.chair(bid);edited=copy.deepcopy(old['draft']['contract'])
        edited['priorities']=['Accuracy over speed, within the configured time budget']
        state=self.c.edit(bid,old['version'],edited)
        self.assertNotEqual(state['sha256'],old['sha256'])
        with self.assertRaises(ValueError):self.c.confirm(bid,old['version'],old['sha256'])
        self.assertEqual(Debate(self.project).list(),[])
        self.assertEqual(self.confirm(bid)['status'],'launched')
    def test_repeat_confirmation_returns_same_child_and_never_enqueues_twice(self):
        bid=self.create();self.chair(bid);a=self.confirm(bid)
        b=self.confirm(bid)
        self.assertEqual(a['launch'],b['launch']);self.assertEqual(len(Debate(self.project).list()),1)
        self.assertEqual(len(self.c.pool.tasks(a['launch']['id'])),1)
    def test_crash_after_launch_intent_recovers_same_child(self):
        bid=self.create();self.chair(bid)
        with patch.object(Debate,'create',side_effect=RuntimeError('TEST interruption')):
            with self.assertRaises(RuntimeError):self.confirm(bid)
        planned=self.c.get(bid)['launch']['id'];self.c=Briefing(self.project)
        state=self.c.advance(bid)
        self.assertEqual(state['launch']['id'],planned);self.assertEqual(len(Debate(self.project).list()),1)
    def test_crash_after_child_creation_before_parent_save_does_not_duplicate(self):
        bid=self.create();self.chair(bid);save=self.c.save
        def interrupted(state):
            if state['status']=='launched':raise RuntimeError('TEST interruption after child creation')
            return save(state)
        with patch.object(self.c,'save',side_effect=interrupted):
            with self.assertRaises(RuntimeError):self.confirm(bid)
        first=Debate(self.project).list()[0]['id'];state=Briefing(self.project).advance(bid)
        self.assertEqual(state['launch']['id'],first);self.assertEqual(len(Debate(self.project).list()),1)
    def test_cooperative_launch_enters_plan_only_after_confirmation(self):
        bid=self.create(cooperative=True);self.chair(bid)
        self.assertEqual(Cooperation(self.project).list(),[])
        state=self.confirm(bid);child=Cooperation(self.project).get(state['launch']['id'])
        self.assertEqual(child['stage'],'plan');self.assertEqual(child['task_contract']['briefing_id'],bid)
    def test_unknown_automated_check_prevents_ready_contract(self):
        bid=self.create();value=reply();value['contract']['acceptance'][0]['check_ids']=['invented']
        state=self.chair(bid,value)
        self.assertEqual(state['status'],'waiting_human');self.assertTrue(state['errors'])
        with self.assertRaises(ValueError):self.confirm(bid)
    def test_ready_flag_cannot_override_unanswered_questions(self):
        bid=self.create();value=reply(False);value['ready']=True
        state=self.chair(bid,value)
        self.assertEqual(state['status'],'waiting_human')
    def test_confirmation_rejects_tampered_snapshot(self):
        bid=self.create();state=self.chair(bid)
        path=self.c.path(bid)/'versions'/f'v{state["version"]}.json'
        changed=read_json(path);changed['bundle']['contract']['goal']='Different goal'
        path.write_text(__import__('json').dumps(changed))
        with self.assertRaises(ValueError):self.confirm(bid)
        self.assertEqual(Debate(self.project).list(),[])
    def test_python_checker_is_snapshotted_before_human_wait(self):
        source=self.project/'check.py';source.write_text('print("original")')
        bid=self.c.create('Check data',self.execution(),checks=[{'id':'total','kind':'python','script':'check.py','timeout_seconds':5}])
        self.chair(bid);source.write_text('raise RuntimeError("replaced")')
        state=self.confirm(bid);child=Debate(self.project);root=child.path(state['launch']['id'])
        self.assertIn('original',(root/'verifiers/total.py').read_text())
    def test_execution_budget_starts_after_confirmation_not_human_wait(self):
        bid=self.create();self.chair(bid)
        import time
        now=time.time()+3600
        with patch('time.time',return_value=now):state=self.confirm(bid)
        child=Debate(self.project).get(state['launch']['id'])
        self.assertGreater(child['deadline'],now)
    def test_scope_change_pauses_without_accepting_replacement_goal(self):
        bid=self.create();self.chair(bid);state=self.confirm(bid)
        c=Debate(self.project);did=state['launch']['id'];task=self.c.pool.claim('author','author')
        from tests.test_debate import DebateTests
        answer=DebateTests.proposal(self,60);answer['scope_change']={'required':True,'reason':'Need external data beyond the supplied inputs'}
        self.c.pool.complete('author',task['id'],task['lease'],'success',answer)
        child=c.advance(did)
        self.assertEqual(child['status'],'waiting_clarification');self.assertEqual(child['versions'],[])
        self.assertTrue(any(q['kind']=='contract_change' for q in child['pending_questions']))
    def test_manual_acceptance_cannot_be_certified_by_model_agreement(self):
        bid=self.create();self.chair(bid,reply(manual=True));state=self.confirm(bid)
        c=Debate(self.project);did=state['launch']['id'];task=self.c.pool.claim('author','author')
        from tests.test_debate import DebateTests
        answer=DebateTests.proposal(self,60);answer['scope_change']={'required':False,'reason':'Within agreed scope'}
        self.c.pool.complete('author',task['id'],task['lease'],'success',answer);c.advance(did);c.advance(did);c.advance(did)
        task=self.c.pool.claim('critic','critic');self.c.pool.complete('critic',task['id'],task['lease'],'success',{'verdict':'accept','summary':'Passed configured sum check','issues':[]});c.advance(did);c.advance(did)
        task=self.c.pool.claim('chair','chair');self.c.pool.complete('chair',task['id'],task['lease'],'success',{'candidate_version':'v1','summary':'Advisory','open_questions':[],'proposed_checks':[]})
        child=c.advance(did);self.assertEqual(child['status'],'needs_evidence')
        self.assertTrue(any(q['kind']=='contract_acceptance' for q in child['pending_questions']))

    def test_timeout_stops_chair_work_and_never_starts_execution(self):
        bid=self.create();state=self.c.get(bid)
        with patch('time.time',return_value=state['deadline']+1):state=self.c.advance(bid)
        self.assertEqual(state['status'],'waiting_human')
        self.assertEqual(Debate(self.project).list(),[])
        before=len(self.c.pool.tasks(bid));self.c.advance(bid)
        self.assertEqual(len(self.c.pool.tasks(bid)),before)
        self.assertFalse(any(t['status']=='queued' for t in self.c.pool.tasks(bid)))

    def test_turn_budget_prevents_unbounded_clarification(self):
        bid=self.create(max_turns=1);state=self.chair(bid,reply(False))
        with self.assertRaisesRegex(ValueError,'budget exhausted'):
            self.c.message(bid,state['version'],'One more question')
        self.assertEqual(len(self.c.pool.tasks(bid)),1)

    def test_child_contract_tamper_is_rejected_on_resume(self):
        bid=self.create();self.chair(bid);state=self.confirm(bid)
        c=Debate(self.project);did=state['launch']['id']
        (c.path(did)/'task-contract.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'contract changed'):c.advance(did)

    def test_stopped_clarification_cannot_launch_or_schedule_more_work(self):
        bid=self.create();self.chair(bid);state=self.c.stop(bid)
        with self.assertRaises(ValueError):self.confirm(bid)
        self.assertEqual(self.c.advance(bid)['status'],'stopped')
        self.assertEqual(Debate(self.project).list(),[])
