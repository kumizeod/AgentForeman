import json
from pathlib import Path
import tempfile
import unittest

from agentforeman.debate import Debate
from agentforeman.files import read_json


class DebateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.project=Path(self.tmp.name)
        self.debate=Debate(self.project)
        for m in ('author','critic','chair'):self.debate.pool.register(m,(m+'-')*10)
    def tearDown(self):self.tmp.cleanup()
    def create(self,**kwargs):
        return self.debate.create('Compute the supplied sales total',['author','critic'],'chair',
            checks=[{'id':'total','kind':'json_value','artifact_id':'report','pointer':'/total','expected':60}],
            stage_seconds=30,budget_seconds=120,**kwargs)
    def result(self,did,member,answer):
        self.debate.advance(did)
        task=self.debate.pool.claim(member,member)
        self.assertIsNotNone(task)
        self.debate.pool.complete(member,task['id'],task['lease'],'success',answer)
        return self.debate.advance(did)
    def proposal(self,total,responses=None):
        return {'summary':'Sales analysis','artifacts':[{'id':'answer','media_type':'text/markdown','content':'Total: '+str(total)},
            {'id':'report','media_type':'application/json','content':json.dumps({'total':total})}],
            'claims':[],'responses':responses or []}
    def criticism(self,state,valid=True):
        receipt=state['versions'][-1]['checks'][0]
        lines=json.dumps(receipt,ensure_ascii=False,indent=2).splitlines()
        n=next(i+1 for i,s in enumerate(lines) if '"passed"' in s)
        return {'verdict':'revise','summary':'Wrong total','issues':[{'severity':'major','problem':'The supplied total fails the frozen check',
            'anchor':{'kind':'check','ref':'total' if valid else 'invented','quote':'"passed": false','line_start':n,'line_end':n},
            'suggested_change':'Recompute the supplied rows'}]}
    def chair(self,did):
        state=self.debate.get(did)
        selected=self.debate.selected(state)
        return self.result(did,'chair',{'candidate_version':selected['id'],'summary':'Advisory summary',
                                      'open_questions':[],'proposed_checks':[]})
    def reviewed_failure(self,**kwargs):
        did=self.create(**kwargs)
        self.result(did,'author',self.proposal(50));state=self.debate.advance(did)
        self.result(did,'critic',self.criticism(state))
        return did
    def test_criticism_revision_retest_is_scoped_and_chair_cannot_declare_facts(self):
        did=self.reviewed_failure()
        state=self.debate.get(did);iid=state['issues'][0]['id']
        self.result(did,'author',self.proposal(60,[{'issue_id':iid,'action':'change','explanation':'Recomputed total'}]))
        self.debate.advance(did)
        self.result(did,'critic',{'verdict':'accept','summary':'Configured check passes','issues':[]})
        state=self.chair(did)
        self.assertEqual(state['status'],'completed_scoped')
        self.assertFalse(state['quality']['factual_correctness_verified'])
        self.assertEqual(state['issues'][0]['status'],'check_passed_scoped')
        self.assertEqual(state['selected_version'],'v2')
        self.assertEqual(read_json(self.debate.path(did)/'transcript.json')['status'],'completed_scoped')
    def test_rhetoric_without_artifact_change_stops_and_keeps_disagreement(self):
        did=self.reviewed_failure();state=self.debate.get(did)
        answer=self.proposal(50,[{'issue_id':state['issues'][0]['id'],'action':'unresolved','explanation':'I still disagree'}])
        answer['summary']='New rhetoric only'
        self.result(did,'author',answer)
        state=self.chair(did)
        self.assertEqual(state['stop_reason'],'no_progress')
        self.assertEqual(len(state['versions']),1)
        self.assertTrue(state['quality']['unresolved_issues'])
    def test_invented_criticism_anchor_is_preserved_as_protocol_error(self):
        did=self.create();self.result(did,'author',self.proposal(50));state=self.debate.advance(did)
        self.result(did,'critic',self.criticism(state,False));state=self.chair(did)
        self.assertEqual(state['stop_reason'],'invalid_or_missing_criticism')
        self.assertTrue(state['protocol_errors'])
        self.assertEqual(state['status'],'needs_evidence')
    def test_round_limit_stops_with_failed_check_and_open_question(self):
        did=self.reviewed_failure(max_rounds=1);state=self.chair(did)
        self.assertEqual(state['stop_reason'],'round_budget')
        self.assertTrue(state['quality']['unresolved_issues'])
    def test_frozen_verifier_script_executes_for_real_and_receipt_is_reused(self):
        script=self.project/'check.py'
        example=Path(__file__).parents[1]/'examples/debate/check_total.py'
        script.write_text(example.read_text())
        did=self.debate.create('Calculate total',['author','critic'],'chair',
            evidence=[{'id':'sales','title':'rows','content':'item,amount\nA,10\nB,20\nC,30\n'}],
            checks=[{'id':'sum','kind':'python','script':'check.py','timeout_seconds':10}],stage_seconds=30,budget_seconds=120)
        self.result(did,'author',self.proposal(60))
        state=self.debate.advance(did)
        self.assertTrue(state['versions'][0]['checks'][0]['passed'])
        from agentforeman.debate_evidence import verify
        before=list(self.debate.path(did).rglob('result.json'))
        verify(self.debate.path(did),state,state['versions'][0])
        self.assertEqual(before,list(self.debate.path(did).rglob('result.json')))
    def test_chair_cannot_substitute_another_candidate(self):
        did=self.create(max_rounds=1);self.result(did,'author',self.proposal(60));self.debate.advance(did)
        self.result(did,'critic',{'verdict':'accept','summary':'Scoped check passes','issues':[]})
        state=self.result(did,'chair',{'candidate_version':'invented','summary':'Everything is true','open_questions':[],'proposed_checks':[]})
        self.assertEqual(state['stop_reason'],'invalid_chair_result')
        self.assertFalse(state['quality']['factual_correctness_verified'])

    def test_restart_between_revision_and_retest_keeps_issue_and_tasks(self):
        did=self.reviewed_failure();iid=self.debate.get(did)['issues'][0]['id']
        self.result(did,'author',self.proposal(60,[{'issue_id':iid,'action':'change','explanation':'Fixed sum'}]))
        before=len(self.debate.pool.tasks(did))
        self.debate=Debate(self.project)
        state=self.debate.advance(did)
        self.assertEqual(len(self.debate.pool.tasks(did)),before)
        self.assertEqual(state['issues'][0]['status'],'check_passed_scoped')
        self.result(did,'critic',{'verdict':'accept','summary':'Check passes after restart','issues':[]})
        state=self.chair(did)
        self.assertEqual(state['status'],'completed_scoped')
        self.assertEqual(state['pending_questions'],[])
        self.assertEqual([p['version'] for p in state['progress'] if p.get('stage')=='verify'],['v1','v2'])

    def test_restart_after_receipt_before_state_save_reuses_verification(self):
        did=self.create();self.result(did,'author',self.proposal(60))
        state=self.debate.get(did)
        from agentforeman.debate_evidence import verify
        verify(self.debate.path(did),state,state['versions'][0])
        self.debate=Debate(self.project)
        state=self.debate.advance(did)
        self.assertEqual(state['progress'][-1]['reused_receipts'],['total'])
        self.assertTrue(state['versions'][0]['checks'][0]['passed'])

    def test_source_criticism_stays_open_despite_model_agreement(self):
        did=self.create();self.result(did,'author',self.proposal(60));self.debate.advance(did)
        critique={'verdict':'revise','summary':'Interpretation still unsupported','issues':[{
            'severity':'major','problem':'The total alone does not establish completeness of the supplied data',
            'anchor':{'kind':'artifact','ref':'answer','quote':'Total: 60','line_start':1,'line_end':1},
            'suggested_change':'Independently audit whether input rows are complete'}]}
        self.result(did,'critic',critique);iid=self.debate.get(did)['issues'][0]['id']
        answer=self.proposal(60,[{'issue_id':iid,'action':'change','explanation':'Added limitation'}])
        answer['artifacts'][0]['content']+='\nInput completeness has not been audited.'
        self.result(did,'author',answer);self.debate.advance(did)
        self.result(did,'critic',{'verdict':'accept','summary':'I agree now','issues':[]})
        state=self.chair(did)
        self.assertEqual(state['status'],'needs_evidence')
        self.assertEqual(state['issues'][0]['status'],'open')
        self.assertEqual(state['pending_questions'][0]['ref'],iid)
        self.assertIn('Questions requiring verification',(self.debate.path(did)/'report.md').read_text())

    def test_agreement_without_checks_does_not_establish_completion(self):
        did=self.debate.create('Write an answer',['author','critic'],'chair',stage_seconds=30,budget_seconds=120)
        self.result(did,'author',self.proposal(60));self.debate.advance(did)
        self.result(did,'critic',{'verdict':'accept','summary':'Looks correct','issues':[]})
        state=self.chair(did)
        self.assertEqual(state['status'],'needs_evidence')
        self.assertEqual(state['pending_questions'][0]['kind'],'verification_gap')

    def test_failed_check_is_a_pending_question_even_if_critics_accept(self):
        did=self.create();self.result(did,'author',self.proposal(50));self.debate.advance(did)
        self.result(did,'critic',{'verdict':'accept','summary':'I consider it correct','issues':[]})
        state=self.chair(did)
        self.assertEqual(state['status'],'needs_evidence')
        self.assertEqual(state['pending_questions'][0]['ref'],'total')

    def test_wall_clock_budget_finishes_without_waiting_for_chair(self):
        from unittest.mock import patch
        did=self.reviewed_failure();state=self.debate.get(did)
        with patch('agentforeman.debate.time.time',return_value=state['deadline']+1):
            state=self.debate.advance(did)
        self.assertEqual(state['stop_reason'],'wall_clock_budget')
        self.assertTrue(state['pending_questions'])
        self.assertEqual(state['stage'],'done')

    def test_frozen_inputs_cannot_be_silently_changed_on_resume(self):
        did=self.create();root=self.debate.path(did)
        (root/'checks.json').write_text('[]',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Frozen verification inputs changed'):
            Debate(self.project).advance(did)

    def test_criticism_line_range_must_match_actual_quote(self):
        did=self.create();self.result(did,'author',self.proposal(50));state=self.debate.advance(did)
        critique=self.criticism(state)
        critique['issues'][0]['anchor'].update(line_start=1,line_end=1)
        self.result(did,'critic',critique);state=self.chair(did)
        self.assertEqual(state['stop_reason'],'invalid_or_missing_criticism')
        self.assertEqual(state['pending_questions'][-1]['kind'],'protocol')
