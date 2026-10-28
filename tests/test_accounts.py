import json
import os
import sys
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agentforeman.accounts import Accounts,profile_environment
from agentforeman.connections import Connections
from agentforeman.pool_worker import Worker

SCHEMA={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer']}


class AccountTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.a=Accounts(self.root)
    def tearDown(self):self.tmp.cleanup()
    def test_official_client_profiles_are_independent_and_never_export_tokens(self):
        for provider in ('codex','claude-code'):
            one=self.a.create(provider,'First');two=self.a.create(provider,'Second')
            self.assertNotEqual(self.a.directory(one['id'],provider),self.a.directory(two['id'],provider))
            (self.a.directory(one['id'],provider)/'auth.json').write_text('{"token":"TEST PRIVATE TOKEN"}')
        self.assertNotIn('TEST PRIVATE TOKEN',json.dumps(self.a.list()))
        self.assertNotIn(str(self.a.root),json.dumps(self.a.list()))
        with self.assertRaises(ValueError):self.a.create('antigravity','Unsupported independent profile')
        with self.assertRaises(ValueError):self.a.directory(one['id'],'codex')
    def test_isolated_workers_run_with_selected_profile_and_scrub_ambient_credentials(self):
        for provider in ('codex','claude-code'):
            profile=self.a.create(provider,'Alternate account');directory=self.a.directory(profile['id'],provider)
            default=self.root/('default-'+provider);default.mkdir();(default/'auth.json').write_text('DO NOT COPY OR CHANGE')
            settings={'provider':provider,'account_dir':str(directory),'state_dir':str(self.root/('worker-'+provider)),
                'token_env':'FOREMAN_ACCOUNT_TEST','timeout_seconds':10,
                'command':[str(Path(__file__).with_name('fixture_account_cli.py').resolve())]}
            original={'FOREMAN_ACCOUNT_TEST':'t'*40,'CODEX_HOME':str(default),'CLAUDE_CONFIG_DIR':str(default),
                'OPENAI_API_KEY':'TEST SECRET','CODEX_API_KEY':'TEST SECRET','ANTHROPIC_API_KEY':'TEST SECRET',
                'ANTHROPIC_AUTH_TOKEN':'TEST SECRET','CLAUDE_CODE_OAUTH_TOKEN':'TEST SECRET'}
            with patch.dict(os.environ,original):
                worker=Worker(settings)
                result=worker.execute({'id':'test_account','attempts':1,'deadline':time.time()+30,
                       'request':{'prompt':'TEST ONLY profile selection','schema':SCHEMA}})
                self.assertEqual(os.environ['CODEX_HOME'],str(default))
                self.assertEqual(os.environ['CLAUDE_CONFIG_DIR'],str(default))
            self.assertEqual(result['outcome'],'success',provider)
            self.assertEqual(result['result']['answer'],profile['id'])
            self.assertTrue((directory/'fixture-used-profile').exists())
            self.assertFalse((default/'fixture-used-profile').exists())
            self.assertEqual((default/'auth.json').read_text(),'DO NOT COPY OR CHANGE')
            for p in (worker.root/'tasks').rglob('*.json'):self.assertNotIn('TEST SECRET',p.read_text())
    @unittest.skipUnless(sys.platform=='linux','Linux terminal fallback uses POSIX script permissions')
    def test_headless_linux_login_guidance_keeps_the_selected_profile(self):
        c=Connections(self.root)
        try:
            profile=self.a.create('codex','Independent')
            with patch('sys.platform','linux'),patch('agentforeman.agents.command',return_value=['/usr/bin/codex']), \
                 patch('shutil.which',return_value=None),patch('subprocess.Popen') as launch:
                with self.assertRaisesRegex(ValueError,profile['id']):c.login('codex',profile['id'])
                launch.assert_not_called()
            script=c.root/('codex_'+profile['id']+'-login.command')
            self.assertIn('CODEX_HOME=',script.read_text());self.assertIn(str(self.a.directory(profile['id'],'codex')),script.read_text())
            self.assertIn('-u OPENAI_API_KEY',script.read_text())
        finally:c.close()

    def test_login_and_connection_test_use_same_profile_on_windows(self):
        c=Connections(self.root)
        try:
            profile=self.a.create('codex','Another ChatGPT account')
            with patch.dict(os.environ,{'CODEX_HOME':'default-home','OPENAI_API_KEY':'TEST SECRET'}), \
                 patch('sys.platform','win32'),patch('agentforeman.agents.command',return_value=['codex.exe']), \
                 patch('subprocess.CREATE_NEW_CONSOLE',512,create=True),patch('subprocess.Popen') as start:
                c.login('codex',profile['id'])
                env=start.call_args.kwargs['env']
                self.assertEqual(env['CODEX_HOME'],str(self.a.directory(profile['id'],'codex')))
                self.assertNotIn('OPENAI_API_KEY',env)
                self.assertEqual(os.environ['CODEX_HOME'],'default-home')
            first=c.create(provider='codex',label='First model',account_id=profile['id'])
            second=c.create(provider='codex',label='Second model',account_id=profile['id'])
            default=c.create(provider='codex',label='Default account')
            self.assertEqual(c.group(first),c.group(second));self.assertNotEqual(c.group(first),c.group(default))
            self.assertEqual(c.settings(first)['account_dir'],str(self.a.directory(profile['id'],'codex')))
            with self.assertRaises(ValueError):c.create(provider='claude-code',account_id=profile['id'])
            with self.assertRaises(ValueError):c.create(provider='codex',password='never-ingest')
        finally:c.close()
