import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agentforeman.connections import Connections
from agentforeman.briefing import Briefing
from agentforeman.debate import Debate
from agentforeman.cooperate import Cooperation
from agentforeman.files import read_json
from agentforeman.platforms import file_lock
from tests.fixture_connection_api import FixtureAPI


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.api=FixtureAPI();self.api_thread=threading.Thread(target=self.api.serve_forever,daemon=True);self.api_thread.start()
        self.c=Connections(self.root)
    def tearDown(self):
        self.c.close()
        end=time.time()+20
        while self.c.owner and time.time()<end:time.sleep(.05)
        self.api.shutdown();self.api.server_close();self.api_thread.join()
        self.tmp.cleanup()
    def until(self,predicate):
        end=time.time()+60
        while time.time()<end:
            result=predicate()
            if result:return result
            time.sleep(.1)
        self.fail('Timed out waiting for connection workflow')
    def add(self,provider='openai-compatible',**kw):
        return self.c.create(label='TEST <img src=x>',provider=provider,model='test/model',base_url=self.api.url,api_key='TEST-ONLY-NOT-A-REAL-KEY',**kw)
    def connect_tested(self,**kw):
        c=self.add(**kw);self.c.test(c['id']);self.until(lambda:c['id'] not in self.c.testing)
        self.assertTrue(self.c.get(c['id'])['test']['passed']);return self.c.get(c['id'])
    def test_real_http_smoke_and_keys_never_enter_metadata_or_public_listing(self):
        c=self.connect_tested();listing=self.c.listing()
        self.assertTrue(listing['connections'][0]['worker_running'])
        self.assertNotIn('TEST-ONLY-NOT-A-REAL-KEY',json.dumps(listing))
        for p in self.root.rglob('*.json'):
            self.assertNotIn('TEST-ONLY-NOT-A-REAL-KEY',p.read_text())
        self.assertEqual(len(self.api.requests),1)
        self.assertEqual(self.c.pool.tasks(),[])
        self.c.enable(c['id'],False)
        self.assertFalse(self.c.get(c['id'])['enabled'])
    def test_bad_answer_and_auth_failure_never_enable(self):
        for code,correct in ((None,False),(401,True),(429,True)):
            self.api.error_code=code;self.api.correct=correct
            c=self.add();self.c.test(c['id']);self.until(lambda:c['id'] not in self.c.testing)
            self.assertFalse(self.c.get(c['id'])['enabled'])
            self.assertFalse(self.c.get(c['id'])['test']['passed'])
            with self.assertRaises(ValueError):self.c.enable(c['id'],True)
    def test_singleton_and_no_secret_backend_fallback_to_plaintext(self):
        other=Connections(self.root)
        self.assertIsNotNone(other.error)
        with self.assertRaises(ValueError):other.create(provider='codex',label='x')
        other.close()
        with patch.object(Connections,'keyring',side_effect=ValueError('No native store')):
            with self.assertRaises(ValueError):self.add(remember=True)
        self.assertEqual(self.c.records(),[])
    def test_shared_account_lock_busy_test_and_disable_during_test(self):
        c=self.add()
        with file_lock(self.c.root/(self.c.group(c)+'.lock')):
            self.c.test(c['id']);self.until(lambda:c['id'] not in self.c.testing)
        self.assertFalse(self.c.get(c['id'])['enabled'])
        self.assertEqual(self.c.get(c['id'])['last_test_attempt']['status'],'busy')
        entered=threading.Event();release=threading.Event()
        def smoke(*a,**k):entered.set();release.wait(5);return {'passed':True,'status':'passed','diagnostic':'test'}
        with patch('agentforeman.smoke.run_smoke',side_effect=smoke):
            self.c.test(c['id']);self.assertTrue(entered.wait(5));self.c.enable(c['id'],False)
            release.set();self.until(lambda:c['id'] not in self.c.testing)
        self.assertFalse(self.c.get(c['id'])['enabled'])
    def test_restore_connections_pause_and_credential_refresh(self):
        c=self.connect_tested();self.c.enable(c['id'],False);self.c.enable(c['id'],True)
        self.c.close();self.until(lambda:self.c.owner is None)
        self.c=Connections(self.root);self.assertTrue(self.c.listing()['connections'][0]['worker_running'])
        self.c.update_key(c['id'],'TEST-ROTATED-KEY')
        self.assertFalse(self.c.get(c['id'])['enabled'])
        self.c.test(c['id']);self.until(lambda:c['id'] not in self.c.testing)
        self.assertTrue(self.c.get(c['id'])['enabled'])
    def test_missing_session_key_after_restart_is_not_a_candidate(self):
        c=self.connect_tested();self.c.close();self.until(lambda:self.c.owner is None)
        os.environ.pop(c['key_env'],None);self.c=Connections(self.root)
        self.assertFalse(self.c.listing()['connections'][0]['key_available']);self.assertEqual(self.c.candidates(),[])
    def test_api_url_and_secret_validation(self):
        for url in ('http://example.com/v1','https://key@example.com/v1','https://example.com/v1?key=secret'):
            with self.assertRaises(ValueError):self.c.create(provider='openai-compatible',model='x',base_url=url,api_key='test')
        with self.assertRaises(ValueError):self.c.create(provider='codex',api_key='do-not-import-tokens')
        with self.assertRaises(ValueError):self.add(unknown='x')
    def test_chair_selects_roles_then_both_modes_execute_through_real_workers_and_checks(self):
        for i in range(4):self.connect_tested(chair_preferred=i==0)
        candidates=[{k:v for k,v in c.items() if k in {'member','label','provider','model','resource_group'}} for c in self.c.candidates()]
        for mode in ('adversarial','cooperative'):
            execution=self.c.plan(mode=mode);before=execution['members'][:]
            controller=Briefing(self.root)
            bid=controller.create('Calculate supplied sales total',execution,auto_candidates=candidates,
                evidence=[{'id':'sales','title':'Sales','content':'A,10\nB,20\nC,30'}],
                checks=[{'id':'total','kind':'json_value','artifact_id':'report','pointer':'/total','expected':60}])
            state=self.until(lambda:(s if (s:=controller.get(bid))['status']=='ready' else None))
            self.assertEqual(state['execution']['members'],list(reversed(before)))
            self.assertEqual((Cooperation if mode=='cooperative' else Debate)(self.root).list(),[])
            state=controller.confirm(bid,state['version'],state['sha256'])
            child=(Cooperation if mode=='cooperative' else Debate)(self.root)
            result=self.until(lambda:(s if (s:=child.get(state['launch']['id']))['status']!='running' else None))
            self.assertEqual(result['status'],'completed_scoped',result.get('protocol_errors'))
            self.assertFalse(result['quality']['factual_correctness_verified'])
            self.assertEqual(result['task_contract']['sha256'],state['sha256'])
            self.assertEqual(len(child.list()),1)
    def test_quota_cools_all_connections_sharing_the_resource(self):
        a=self.connect_tested();b=self.connect_tested()
        self.api.error_code=429
        self.c.pool.enqueue('quota','answer',['local_'+a['id']],{'prompt':'TEST ONLY quota',
            'schema':{'type':'object','properties':{'answer':{'type':'string'}},'required':['answer']}},time.time()+120)
        self.until(lambda:all(m['not_before']>time.time() for m in self.c.pool.members()))
        self.assertEqual(self.c.candidates(),[])
        self.assertEqual(len(self.api.requests),3)
        new=self.add();self.c.pool.register('local_'+new['id'],'n'*40)
        self.c.sync_cooldown(new)
        member=next(m for m in self.c.pool.members() if m['id']=='local_'+new['id'])
        self.assertGreater(member['not_before'],time.time())

    def test_windows_login_uses_native_argv_in_a_new_console(self):
        with patch('sys.platform','win32'),patch('agentforeman.agents.command',return_value=['node.exe','C:/client path/codex.js']), \
             patch('subprocess.CREATE_NEW_CONSOLE',512,create=True),patch('subprocess.Popen') as launch:
            self.c.login('codex')
        self.assertEqual(launch.call_args.args[0],['node.exe','C:/client path/codex.js','login'])
        self.assertNotIn('shell',launch.call_args.kwargs)
        with self.assertRaises(ValueError):self.c.login('openai-compatible')

    def test_connection_test_finishes_only_after_worker_is_published(self):
        c=self.add();entered=threading.Event();release=threading.Event();original=self.c.start
        def start(connection):
            entered.set();release.wait(10);return original(connection)
        with patch.object(self.c,'start',side_effect=start):
            self.c.test(c['id'])
            try:
                self.assertTrue(entered.wait(15))
                self.assertIn(c['id'],self.c.testing)
            finally:release.set()
            self.until(lambda:c['id'] not in self.c.testing)
        self.assertTrue(self.c.public(self.c.get(c['id']))['worker_running'])
        self.assertIn('local_'+c['id'],[r['member'] for r in self.c.candidates()])

    def test_anthropic_messages_compatible_relay(self):
        self.connect_tested(provider='anthropic')
