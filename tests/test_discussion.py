from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from agentforeman.campaign import create_campaign, drive_campaign
from agentforeman import discussion
from agentforeman.engine import load_config
from agentforeman.files import atomic_text, read_json
from agentforeman.job import wait_for_job
from agentforeman.observe import statistics
from agentforeman.store import Store
from agentforeman.webserver import ControlPanel
from tests import test_campaign as helpers


class DiscussionTests(unittest.TestCase):
    setUp = helpers.CampaignTests.setUp
    tearDown = helpers.CampaignTests.tearDown
    root = helpers.CampaignTests.root
    state = helpers.CampaignTests.state
    drive = helpers.CampaignTests.drive

    def create(self, **kwargs):
        os.environ['FOREMAN_CAMPAIGN_SCRIPT'] = '["good", "stop"]'
        return create_campaign(self.project, 'Improve within the frozen validation protocol', **{
            'max_rounds': 2, 'budget_seconds':180,
            'review_policy':{'enabled':True,'max_reviews':2}, **kwargs})

    def update(self, cid, fn):
        store=Store(self.project)
        try:
            state=store.get_campaign(cid);fn(state);store.save_campaign(state,'test_update')
        finally:store.close()

    def test_unchanged_revision_stops_without_another_review_or_worker(self):
        original = discussion.run_model
        def model(root, state):
            if state['phase'] == 'revise':
                answer = copy.deepcopy(state['discussion']['proposal'])
                answer.update(rationale='Only rhetoric changed', review_response='I insist this is fine')
                return answer
            return original(root, state)
        with patch.dict(os.environ, {'FOREMAN_TEST_REVIEWS':'["revise"]'}), patch('agentforeman.discussion.run_model', side_effect=model):
            cid = self.create()
            state = self.drive(cid)
        self.assertEqual(state['stop_reason'], 'debate_stalled')
        self.assertIsNone(state['pending'])
        self.assertEqual(state['rounds'], [])
        record = state['discussions'][0]
        self.assertEqual(len(record['reviews']), 1)
        self.assertEqual(record['stall']['changed_fields'], [])
        self.assertEqual(record['issues'][0]['status'], 'open')
        self.assertEqual(record['final_response']['review_response'], 'I insist this is fine')
        self.assertEqual(read_json(self.root(cid)/'discussion.json')['completed'][0], record)
        self.assertEqual(self.drive(cid)['stop_reason'], 'debate_stalled')

    def test_progress_detects_whitespace_rhetoric_and_cycles_but_allows_plan_change(self):
        a = {'action':'experiment', 'instruction':'train   baseline', 'based_on':[],
             'plan':{'comparison':'fixed budget'}, 'rationale':'first', 'review_response':''}
        active = {'proposal':a, 'reviews':[{'proposal':a}]}
        b = copy.deepcopy(a)
        b.update(instruction='train baseline', rationale='new claim', review_response='answered')
        self.assertTrue(discussion.revision_progress(active, b)['repeated_proposal'])
        b['plan']['comparison'] = 'matched steps and seeds'
        self.assertEqual(discussion.revision_progress(active, b)['changed_fields'], ['plan'])
        self.assertFalse(discussion.revision_progress(active, b)['repeated_proposal'])
        active.update(proposal=b, reviews=[{'proposal':a}, {'proposal':b}])
        self.assertTrue(discussion.revision_progress(active, a)['repeated_proposal'])

    def test_issue_ledger_preserves_unresolved_absence_and_deduplicates_repetition(self):
        c = {'severity':'major', 'evidence':'Budget   confound', 'suggestion':'Run matched steps'}
        def entry(verdict, concerns):
            return {'review':{'verdict':verdict, 'concerns':concerns}}
        first = entry('revise', [c, c])
        other = {**c, 'evidence':'Budget confound', 'severity':'blocking'}
        reviews = [first, entry('revise', [other])]
        item = discussion.issue_ledger(reviews)[0]
        self.assertEqual(item['occurrences'], [1, 2])
        self.assertEqual(item['severity'], 'blocking')
        self.assertEqual(item['id'], discussion.issue_ledger([first])[0]['id'])
        reviews.append(entry('reject', []))
        self.assertEqual(discussion.issue_ledger(reviews)[0]['status'], 'not_raised_latest')
        reviews.append(entry('approve', []))
        self.assertEqual(discussion.issue_ledger(reviews)[0]['status'], 'reviewer_cleared')

    def test_revise_then_approve_runs_real_training_with_bound_provenance(self):
        with patch.dict(os.environ, {'FOREMAN_TEST_REVIEWS':'["revise","approve"]'}):
            cid=self.create();state=self.drive(cid)
        self.assertEqual(state['status'],'completed')
        self.assertEqual(state['stop_reason'],'manager_stop')
        self.assertGreater(state['best']['metric'],state['initial_metric'])
        history=state['discussions'][0]
        self.assertEqual([e['review']['verdict'] for e in history['reviews']],['revise','approve'])
        self.assertIn('explicit comparison',history['proposal']['review_response'])
        self.assertEqual(history['reviews'][-1]['proposal_sha256'],discussion.proposal_hash(state['rounds'][0]['decision']))
        self.assertEqual(history['revisions'][0]['changed_fields'], ['plan'])
        self.assertEqual(history['issues'][0]['status'], 'reviewer_cleared')
        self.assertEqual(len(state['rounds']),1)
        transcript=read_json(self.root(cid)/'discussion.json')
        self.assertEqual(transcript['completed'][0]['status'],'approved')
        stats=statistics(self.project)
        self.assertEqual(stats['by_role']['reviewer']['jobs'],2)
        self.assertEqual(stats['by_role']['worker']['jobs'],1)
        self.assertIn('Automatic proposal review',(self.root(cid)/'report.md').read_text())
        server=ControlPanel(self.project,0)
        try:
            detail=server.detail(cid)
            self.assertEqual(detail['discussions'][0]['status'],'approved')
            self.assertEqual(server.logs(cid,'reviewer')['kind'],'reviewer')
            self.assertEqual(server.logs(cid,'revision')['kind'],'revision')
        finally:server.server_close()

    def test_rejection_and_exhaustion_never_allocate_worker(self):
        for replies, reason, expected in [('["reject"]','review_rejected',1),('["revise"]','review_budget_exhausted',2)]:
            with self.subTest(reason=reason),patch.dict(os.environ,{'FOREMAN_TEST_REVIEWS':replies}):
                cid=self.create();state=self.drive(cid)
                self.assertEqual(state['stop_reason'],reason)
                self.assertEqual(state['rounds'],[])
                self.assertIsNone(state['pending'])
                self.assertEqual(len(state['discussions'][0]['reviews']),expected)
                self.assertEqual(len(list((self.root(cid)/'project'/'.foreman'/'runs').iterdir())),1)
                self.assertEqual(state['best']['metric'],state['initial_metric'])

    def test_completed_review_receipt_survives_controller_interruption(self):
        cid=self.create();self.drive(cid,until='review')
        folder=discussion.control(self.root(cid),self.state(cid))
        def interrupt_after_receipt(job,spec):
            wait_for_job(job,spec)
            raise KeyboardInterrupt
        with patch('agentforeman.discussion.wait_for_job',side_effect=interrupt_after_receipt):
            with self.assertRaises(KeyboardInterrupt):self.drive(cid)
        receipt=read_json(folder/'job'/'result.json')
        self.assertEqual(self.state(cid)['phase'],'review')
        self.assertEqual(self.state(cid)['discussion']['reviews'],[])
        state=self.drive(cid)
        self.assertEqual(state['status'],'completed')
        self.assertEqual(read_json(folder/'job'/'result.json'),receipt)
        self.assertEqual(len(state['discussions'][0]['reviews']),1)

    def test_review_and_revision_quota_wait_is_durable_and_model_free(self):
        for scope in ('reviewer','revision'):
            marker=Path(self.temp.name)/('calls-'+scope)
            with self.subTest(scope=scope),patch.dict(os.environ,{
                'FOREMAN_TEST_REVIEWS':'["revise","approve"]','FOREMAN_TEST_QUOTA_SCOPE':scope,
                'FOREMAN_TEST_QUOTA_MARKER':str(marker)}):
                cid=self.create(quota_policy={'probe':False,'minimum_seconds':.01})
                waiting=self.drive(cid)
                self.assertEqual(waiting['status'],'waiting_quota')
                self.assertEqual(waiting['quota_wait']['scope'],scope)
                self.assertIsNone(waiting['pending'])
                before=(marker/'calls.jsonl').read_text()
                for _ in range(2):self.drive(cid)
                self.assertEqual((marker/'calls.jsonl').read_text(),before)
                self.update(cid,lambda s:s['quota_wait'].update(wake_at=time.time()-1))
                done=self.drive(cid)
                self.assertEqual(done['status'],'completed')
                self.assertEqual(done['quota_retries'],1)
                self.assertGreater(done['best']['metric'],done['initial_metric'])
                self.assertEqual(len(done['discussions'][0]['reviews']),2)

    def test_deadline_and_stop_before_review_start_no_further_models(self):
        for stop in (False,True):
            with self.subTest(stop=stop):
                cid=self.create();self.drive(cid,until='review')
                if stop:atomic_text(self.root(cid)/'stop.request','stop\n')
                else:self.update(cid,lambda s:s.update(deadline_at=time.time()-1))
                with patch('agentforeman.discussion.make_spec',side_effect=AssertionError('Started expired/stopped work')):
                    state=self.drive(cid)
                self.assertEqual(state['status'],'completed')
                self.assertEqual(state['stop_reason'],'stop_requested' if stop else 'wall_clock_budget')
                self.assertEqual(state['rounds'],[])

    def test_tampered_approved_instruction_cannot_execute(self):
        cid=self.create();self.drive(cid,until='execute')
        self.update(cid,lambda s:s['pending']['decision'].update(instruction='Changed after approval'))
        with self.assertRaisesRegex(ValueError,'does not match'):
            self.drive(cid)
        self.assertEqual(self.state(cid)['rounds'],[])
        self.assertEqual(len(list((self.root(cid)/'project'/'.foreman'/'runs').iterdir())),1)

    def test_contradictory_review_fails_closed_and_explicit_retry_uses_new_receipt(self):
        cid=self.create()
        with patch.dict(os.environ,{'FOREMAN_TEST_REVIEW_CONTRADICTION':'1'}):
            with self.assertRaisesRegex(ValueError,'contradicts'):self.drive(cid)
        self.assertEqual(self.state(cid)['phase'],'review')
        self.assertIsNone(self.state(cid)['pending'])
        done=self.drive(cid,retry_failed=True)
        self.assertEqual(done['status'],'completed')
        self.assertTrue((self.root(cid)/'discussions'/'000'/'review-00-00'/'job'/'result.json').exists())
        self.assertTrue((self.root(cid)/'discussions'/'000'/'review-00-01'/'job'/'result.json').exists())

    def test_readonly_reviewer_model_override_and_revision_context(self):
        cfg=self.project/'foreman.toml';cfg.write_text(cfg.read_text()+'\n[reviewer]\nmodel = "test-reviewer-model"\ntimeout_seconds = 45\n')
        with patch.dict(os.environ,{'FOREMAN_TEST_REVIEWS':'["revise","approve"]'}):
            cid=self.create();self.drive(cid,until='revise')
            root=self.root(cid)/'discussions'/'000'
            spec=read_json(root/'review-00-00'/'job'/'spec.json')
            self.assertEqual(spec['argv'][spec['argv'].index('--sandbox')+1],'read-only')
            self.assertEqual(spec['argv'][spec['argv'].index('--model')+1],'test-reviewer-model')
            self.assertLessEqual(spec['timeout_seconds'],45)
            self.assertTrue((root/'review-00-00'/'context'/'best_code'/'model.py').exists())
            review_input=read_json(root/'review-00-00'/'context'/'evidence.json')
            self.assertEqual(review_input['baseline_metric'],self.state(cid)['initial_metric'])
            self.assertEqual(review_input['incumbent']['metric'],self.state(cid)['best']['metric'])
            self.assertEqual(review_input['incumbent']['code_hashes'],self.state(cid)['best']['hashes'])
            done=self.drive(cid)
            evidence=read_json(root/'revise-01-00'/'context'/'evidence.json')
            self.assertEqual(evidence['review_feedback']['review']['verdict'],'revise')
            self.assertEqual(done['status'],'completed')

    def test_failed_candidate_repair_and_retest_are_reviewed_against_real_seed(self):
        cid=self.create(max_rounds=4)
        with patch.dict(os.environ,{'FOREMAN_CAMPAIGN_SCRIPT':'["bad","repair","retest","stop"]'}):
            state=self.drive(cid)
        self.assertEqual([r['status'] for r in state['rounds']],['failed','completed','completed'])
        self.assertTrue(state['rounds'][1]['promoted'])
        self.assertFalse(state['rounds'][2]['promoted'])
        self.assertEqual(len(state['discussions']),3)
        context=self.root(cid)/'discussions'/'001'/'review-00-00'/'context'
        seed=read_json(context/'proposal_code'/'config.json')
        healthy=read_json(context/'best_code'/'config.json')
        self.assertEqual(seed['epochs'],'intentional training failure')
        self.assertIsInstance(healthy['epochs'],int)

    def test_schema_policy_and_reviewer_config_validation(self):
        for policy in ({'enabled':'true'},{'max_reviews':0},{'max_reviews':True},{'max_reviews':6},{'unexpected':1}):
            with self.assertRaises(ValueError):self.create(review_policy=policy)
        good={'verdict':'approve','summary':'Supported by the current evidence','concerns':[]}
        self.assertEqual(discussion.validate(good),good)
        for change in ({'verdict':[]},{'summary':''},{'verdict':'revise'},{'concerns':[{'severity':'minor','evidence':'','suggestion':'fix'}]}):
            with self.assertRaises(ValueError):discussion.validate({**good,**change})
        cfg=self.project/'foreman.toml';cfg.write_text(cfg.read_text()+'\n[reviewer]\ntimeout_seconds = -1\n')
        with self.assertRaisesRegex(ValueError,'reviewer.timeout'):load_config(self.project)
