import os
import time
import unittest
from unittest.mock import patch

from agentforeman.council import Council
from agentforeman.council_experiment import sync
from agentforeman.store import Store
from tests import test_campaign as helpers
from tests import test_council as council_helpers


class CouncilExperimentTests(unittest.TestCase):
    setUp=helpers.CampaignTests.setUp
    tearDown=helpers.CampaignTests.tearDown
    root=helpers.CampaignTests.root
    drive=helpers.CampaignTests.drive

    def completed(self):
        self.council=Council(self.project)
        for m in ('a','b','c','chair'):self.council.pool.register(m,m*40)
        cid=self.council.create('Compare the model under fixed training budget',['a','b'],'chair',
            budget_seconds=600,stage_seconds=60,experiment_policy={'enabled':True,'max_rounds':1,'budget_seconds':120})
        for _ in range(4):council_helpers.CouncilTests.complete_stage(self,cid)
        return cid

    def test_opted_in_council_runs_real_training_and_records_verified_archive(self):
        with patch.dict(os.environ,{'FOREMAN_CAMPAIGN_SCRIPT':'["good","stop"]'}):
            cid=self.completed()
            with patch('agentforeman.overnight.launch',return_value={'status':'test_foreground'}):
                linked=sync(self.council,cid)
            child=linked['experiment']['campaign_id']
            result=self.drive(child)
            self.assertEqual(result['status'],'completed')
            final=sync(self.council,cid)
        self.assertEqual(final['experiment']['campaign_id'],child)
        self.assertTrue(final['experiment']['archive_integrity_checked'])
        self.assertTrue(final['quality']['experiments_executed'])
        self.assertFalse(final['quality']['factual_correctness_verified'])
        self.assertGreater(final['experiment']['best_metric'],final['experiment']['baseline_metric'])
        self.assertIn('Local verified experiment handoff',(self.council.path(cid)/'report.md').read_text())

    def test_crash_after_campaign_creation_before_link_does_not_duplicate_work(self):
        cid=self.completed();original=self.council.save
        def fail_link(state):
            if state.get('experiment',{}).get('campaign_id'):
                raise RuntimeError('TEST crash after campaign commit')
            original(state)
        with patch.object(self.council,'save',side_effect=fail_link):
            with self.assertRaises(RuntimeError):sync(self.council,cid)
        with patch('agentforeman.overnight.launch',return_value={'status':'test'}):
            recovered=sync(self.council,cid)
            repeated=sync(self.council,cid)
        store=Store(self.project)
        try:self.assertEqual(len(store.campaigns()),1)
        finally:store.close()
        self.assertEqual(recovered['experiment']['campaign_id'],repeated['experiment']['campaign_id'])

    def test_immediate_stop_during_child_creation_never_launches_detached_campaign(self):
        from agentforeman.campaign import create_campaign
        from agentforeman.files import atomic_text
        cid=self.completed()
        def create_then_stop(*args,**kwargs):
            child=create_campaign(*args,**kwargs)
            atomic_text(self.council.path(cid)/'terminate.request','terminate\n')
            return child
        with patch('agentforeman.campaign.create_campaign',side_effect=create_then_stop), \
             patch('agentforeman.overnight.launch') as launch:
            state=sync(self.council,cid)
            launch.assert_not_called()
        self.assertEqual(state['experiment']['status'],'stopped')
        self.assertTrue((self.project/'.foreman/campaigns'/state['experiment']['campaign_id']/'terminate.request').exists())

    def test_changed_project_blocks_automatic_experiment(self):
        cid=self.completed()
        path=self.project/'workspace'/'config.json';path.write_text(path.read_text()+'\n')
        with patch('agentforeman.overnight.launch',side_effect=AssertionError('launched changed project')):
            state=sync(self.council,cid)
        self.assertEqual(state['experiment']['status'],'blocked')
        self.assertEqual(state['experiment']['reason'],'project_changed_since_submission')

    def test_delayed_council_start_queues_no_model_work(self):
        council=Council(self.project)
        for m in ('a','b','c','chair'):council.pool.register(m,m*40)
        cid=council.create('q',['a','b'],'chair',start_at=time.time()+3600)
        council.advance_all()
        self.assertEqual(council.pool.tasks(cid),[])
        self.assertEqual(council.get(cid)['stage'],'explore')
