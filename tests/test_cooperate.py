import unittest
from unittest.mock import patch

from agentforeman.cooperate import Cooperation
from tests import test_debate as helpers


class CooperationTests(unittest.TestCase):
    setUpBase=helpers.DebateTests.setUp
    tearDown=helpers.DebateTests.tearDown
    result=helpers.DebateTests.result
    proposal=helpers.DebateTests.proposal
    chair=helpers.DebateTests.chair
    criticism=helpers.DebateTests.criticism

    def setUp(self):
        self.setUpBase();self.debate=Cooperation(self.project)
        for member in ('worker-a','worker-b'):self.debate.pool.register(member,(member+'-')*8)

    def create(self,**kwargs):
        return self.debate.create('Calculate and explain the sales total',['author','worker-a','worker-b'],'chair',
            reviewers=['critic'],checks=[{'id':'total','kind':'json_value','artifact_id':'report','pointer':'/total','expected':60}],
            stage_seconds=30,budget_seconds=120,**kwargs)

    def plan(self):
        return {'summary':'Split calculation and explanation',
                'assignments':[{'member':m,'goal':g,'deliverables':['answer','report'],
                                'acceptance':'Use only supplied data and flag assumptions'}
                               for m,g in [('worker-a','Calculate sum'),('worker-b','Explain assumptions')]],
                'integration_contract':'Combine calculation and explanation; answer plus report.total integer'}

    def contributions(self,did):
        self.result(did,'author',self.plan())
        self.debate.advance(did)
        tasks=[self.debate.pool.claim(m,m) for m in ('worker-a','worker-b')]
        self.assertTrue(all(tasks))
        self.assertNotEqual(tasks[0]['id'],tasks[1]['id'])
        for m,task in zip(('worker-a','worker-b'),tasks):
            self.assertTrue(self.debate.pool.complete(m,task['id'],task['lease'],'success',self.proposal(60)))
        self.debate=Cooperation(self.project)
        state=self.debate.advance(did)
        self.assertEqual(state['stage'],'propose')
        self.assertEqual(len(state['contributions']),2)

    def test_parallel_contributions_integrate_and_independently_accept(self):
        did=self.create();self.contributions(did)
        self.result(did,'author',self.proposal(60));self.debate.advance(did)
        self.assertIsNone(self.debate.pool.claim('worker-a','must-not-self-review'))
        self.result(did,'critic',{'verdict':'accept','summary':'Frozen check passes','issues':[]})
        state=self.chair(did)
        self.assertEqual(state['status'],'completed_scoped')
        self.assertFalse(state['quality']['factual_correctness_verified'])
        self.assertIn('Cooperative task',(self.debate.path(did)/'report.md').read_text())

    def test_bad_integration_is_retested_after_independent_criticism(self):
        did=self.create();self.contributions(did)
        self.result(did,'author',self.proposal(50));state=self.debate.advance(did)
        self.result(did,'critic',self.criticism(state));state=self.debate.get(did)
        self.result(did,'author',self.proposal(60,[{'issue_id':state['issues'][0]['id'],'action':'change','explanation':'Fixed integrated total'}]))
        self.debate.advance(did)
        self.result(did,'critic',{'verdict':'accept','summary':'Retest passes','issues':[]})
        self.assertEqual(self.chair(did)['status'],'completed_scoped')

    def test_missing_contribution_blocks_integration_and_retains_partial_results(self):
        did=self.create();self.result(did,'author',self.plan());self.debate.advance(did)
        task=self.debate.pool.claim('worker-a','one')
        self.debate.pool.complete('worker-a',task['id'],task['lease'],'success',self.proposal(60))
        state=self.debate.get(did)
        with patch('agentforeman.cooperate.time.time',return_value=state['stage_deadline']+1):
            state=self.debate.advance(did)
        self.assertEqual(state['status'],'needs_evidence')
        self.assertEqual(state['versions'],[])
        self.assertTrue(state['contribution_tasks'])
        self.assertTrue(state['pending_questions'])

    def test_plan_cannot_replace_or_omit_registered_contributor(self):
        did=self.create();plan=self.plan();plan['assignments'][1]['member']='chair'
        state=self.result(did,'author',plan)
        self.assertEqual(state['stop_reason'],'invalid_stage_result')
        self.assertEqual(state['contributions'],[])

    def test_reviewer_must_not_be_a_contributor(self):
        with self.assertRaisesRegex(ValueError,'distinct'):
            self.debate.create('task',['author','worker-a'],'chair',reviewers=['worker-a'])

    def test_stop_before_plan_finishes_cancels_queued_work(self):
        did=self.create();(self.debate.path(did)/'stop.request').write_text('stop')
        state=self.debate.advance(did)
        self.assertEqual(state['stop_reason'],'stop_requested')
        self.assertFalse(any(t['status']=='queued' for t in self.debate.pool.tasks(did)))

    def test_contribution_claim_requires_matching_source(self):
        did=self.create();self.result(did,'author',self.plan());self.debate.advance(did)
        for member in ('worker-a','worker-b'):
            task=self.debate.pool.claim(member,member);answer=self.proposal(60)
            if member=='worker-a':answer['claims']=[{'id':'claim','statement':'Unsupported source',
                'source_refs':[{'source_id':'invented','quote':'Never provided'}]}]
            self.debate.pool.complete(member,task['id'],task['lease'],'success',answer)
        state=self.debate.advance(did)
        self.assertEqual(state['stop_reason'],'invalid_stage_result')
        self.assertEqual(state['versions'],[])
