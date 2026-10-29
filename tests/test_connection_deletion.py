import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agentforeman.accounts import Accounts
from agentforeman.connections import Connections
from agentforeman.files import atomic_json
from agentforeman.workspace import preferences, save_preferences, usage
from tests.test_pool import SCHEMA


class ConnectionDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.c=Connections(self.root)
    def tearDown(self):self.c.close();self.tmp.cleanup()
    def add(self,**extra):
        return self.c.create(provider='openai-compatible',model='test',base_url='https://example.invalid/v1',api_key='TEST ONLY',**extra)

    def test_idle_delete_forgets_own_key_preserves_history_and_adjusts_preferences(self):
        a=self.add(label='First');b=self.add(label='Second');c=self.add(label='Zero share')
        save_preferences(self.root,version=0,weights={a['id']:60,b['id']:40,c['id']:0})
        member='local_'+a['id'];self.c.pool.register(member,'t'*40)
        tid=self.c.pool.enqueue('historical','answer',[member],{'prompt':'TEST ONLY','schema':SCHEMA},time.time()+60)
        task=self.c.pool.claim(member,'test')
        self.c.pool.complete(member,tid,task['lease'],'success',{'answer':'42'},usage={'provider':'openai-compatible','model':'test','input_tokens':100,'output_tokens':5,'complete':True,'source':'client_reported'})
        self.assertIn('FOREMAN_API_'+a['id'].upper(),os.environ)
        self.assertTrue(self.c.delete(a['id'])['history_preserved'])
        self.assertTrue(self.c.delete(a['id'])['deleted'])
        self.assertNotIn('FOREMAN_API_'+a['id'].upper(),os.environ)
        self.assertEqual({r['id'] for r in self.c.records()},{b['id'],c['id']})
        self.assertEqual(self.c.pool.tasks()[0]['result'],{'answer':'42'})
        stats=usage(self.root);self.assertEqual(stats['totals']['total_tokens'],105)
        self.assertEqual(stats['connections'][0]['id'],a['id']);self.assertEqual(stats['connections'][0]['label'],'First')
        shares={r['id']:r['target_share'] for r in preferences(self.root)['connections']}
        self.assertEqual(shares,{b['id']:100,c['id']:0})
        with self.assertRaises(ValueError):self.c.enable(a['id'],True)
        with self.assertRaises(ValueError):self.c.test(a['id'])

    def test_active_lease_is_paused_but_cannot_be_deleted_until_settled(self):
        a=self.add();member='local_'+a['id'];self.c.pool.register(member,'t'*40)
        tid=self.c.pool.enqueue('live','answer',[member],{'prompt':'TEST ONLY','schema':SCHEMA},time.time()+60)
        task=self.c.pool.claim(member,'test')
        with self.assertRaisesRegex(ValueError,'退出确认'):self.c.delete(a['id'])
        self.assertFalse(self.c.get(a['id'])['enabled'])
        with self.assertRaises(ValueError):self.c.pool.claim(member,'test')
        self.assertEqual(self.c.pool.tasks()[0]['status'],'leased')
        self.c.pool.complete(member,tid,task['lease'],'cancelled')
        self.assertTrue(self.c.delete(a['id'])['deleted'])

    def test_missing_native_receipt_and_running_smoke_cannot_claim_stopped(self):
        a=self.add();self.c.testing.add(a['id'])
        with self.assertRaisesRegex(ValueError,'测试仍在运行'):self.c.delete(a['id'])
        self.c.testing.remove(a['id'])
        job=self.c.path(a['id'])/'worker/tasks/test/1/job';atomic_json(job/'spec.json',{})
        with self.assertRaisesRegex(ValueError,'退出回执'):self.c.delete(a['id'])
        atomic_json(job/'result.json',{'exit_code':1,'cancelled':False,'error':None})
        self.assertTrue(self.c.delete(a['id'])['deleted'])

    def test_user_environment_and_shared_official_account_are_not_deleted(self):
        with patch.dict(os.environ,{'FOREMAN_TEST_SHARED_KEY':'TEST ONLY'}):
            c=self.c.create(provider='openai-compatible',model='test',base_url='https://example.invalid/v1',key_env='FOREMAN_TEST_SHARED_KEY')
            self.c.delete(c['id']);self.assertEqual(os.environ['FOREMAN_TEST_SHARED_KEY'],'TEST ONLY')
        accounts=Accounts(self.root);a=accounts.create('codex','Existing account')
        c=self.c.create(provider='codex',account_id=a['id'])
        config=(accounts.directory(a['id'],'codex')/'config.toml').read_bytes()
        self.c.delete(c['id'])
        self.assertEqual((accounts.directory(a['id'],'codex')/'config.toml').read_bytes(),config)

    def test_os_store_missing_entry_is_safe_but_backend_error_blocks_deletion(self):
        from unittest.mock import Mock
        a=self.add();record=self.c.get(a['id']);record['secret_storage']='os';self.c.save(record)
        backend=Mock();backend.get_password.return_value=None
        with patch.object(self.c,'keyring',return_value=backend):self.assertTrue(self.c.delete(a['id'])['deleted'])
        backend.delete_password.assert_not_called()
        b=self.add();record=self.c.get(b['id']);record['secret_storage']='os';self.c.save(record)
        backend.get_password.side_effect=RuntimeError('TEST ONLY credential backend error')
        with patch.object(self.c,'keyring',return_value=backend):
            with self.assertRaisesRegex(ValueError,'未能移除'):self.c.delete(b['id'])
        self.assertFalse(self.c.get(b['id'])['enabled'])

    def test_deleting_last_connection_does_not_disable_future_defaults(self):
        a=self.add();save_preferences(self.root,version=0,weights={a['id']:100})
        self.c.delete(a['id']);b=self.add()
        p=preferences(self.root)
        self.assertFalse(p['configured']);self.assertEqual(p['connections'][0]['id'],b['id'])
        self.assertEqual(p['connections'][0]['target_share'],100)
