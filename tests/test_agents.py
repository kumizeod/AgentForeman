import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agentforeman.agents import make_spec,read_answer,detect
from agentforeman.pool_worker import Worker,load_worker
from agentforeman import codex

SCHEMA={'type':'object','properties':{'answer':{'type':'string','minLength':1}},'required':['answer'],'additionalProperties':False}


class AgentAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_both_cli_workers_execute_native_supervision_and_reuse_receipts(self):
        for provider in ('claude-code','gemini-cli'):
            settings={'state_dir':str(self.root/provider),'token_env':'FOREMAN_TEST_TOKEN','provider':provider,
                      'timeout_seconds':10,'command':[str(Path(__file__).with_name('fixture_agent_cli.py').resolve())]}
            with patch.dict(os.environ,{'FOREMAN_TEST_TOKEN':'t'*40}):worker=Worker(settings)
            task={'id':'test_task','attempts':1,'deadline':time.time()+30,'request':{'prompt':'TEST ONLY','schema':SCHEMA}}
            result=worker.execute(task)
            self.assertEqual(result['outcome'],'success')
            self.assertIn('multi CLI',result['result']['answer'])
            receipt=worker.root/'tasks/test_task/1/job/result.json';saved=receipt.read_bytes()
            self.assertEqual(worker.execute(task),result)
            self.assertEqual(saved,receipt.read_bytes())
    def test_read_only_cli_flags_and_schema_envelopes_fail_closed(self):
        for provider in ('claude-code','gemini-cli'):
            settings={'provider':provider,'timeout_seconds':10,'command':[str(Path(__file__).with_name('fixture_agent_cli.py').resolve())]}
            spec=make_spec(self.root,self.root/'context',{'prompt':'do work','schema':SCHEMA},settings)
            self.assertNotIn('--dangerously-skip-permissions',spec['argv'])
            if provider=='claude-code':
                self.assertEqual(spec['argv'][spec['argv'].index('--tools')+1],'')
            else:self.assertIn('plan',spec['argv'])
        job=self.root/'job';job.mkdir();(job/'stdout.log').write_text('{"is_error":true,"result":"fake"}')
        with self.assertRaises(ValueError):read_answer(job,self.root,{'schema':SCHEMA},'claude-code')
        (job/'stdout.log').write_text('{"response":"```json\\n{}\\n```"}')
        with self.assertRaises(ValueError):read_answer(job,self.root,{'schema':SCHEMA},'gemini-cli')
    def test_windows_npm_shim_supports_each_provider_without_shell(self):
        for package,binary in (('@anthropic-ai/claude-code','claude'),('@google/gemini-cli','gemini')):
            base=self.root/binary;base.mkdir();shim=base/(binary+'.cmd');shim.write_text('not executed')
            directory=base/'node_modules'/package;directory.mkdir(parents=True)
            (directory/'package.json').write_text(json.dumps({'bin':{binary:'entry.js'}}))
            (directory/'entry.js').write_text('')
            with patch('sys.platform','win32'),patch('agentforeman.codex.shutil.which',return_value='node.exe'):
                argv=codex.resolve_command({'command':[str(shim)]},npm_package=package,bin_name=binary)
            self.assertEqual(argv,['node.exe',str((directory/'entry.js').resolve())])
    def test_config_accepts_cli_without_api_model_and_detection_never_launches(self):
        for provider in ('claude-code','gemini-cli'):
            path=self.root/'worker.toml';path.write_text('[worker]\ncoordinator="http://127.0.0.1:8770"\nprovider="'+provider+'"')
            self.assertEqual(load_worker(path)['provider'],provider)
        with patch('agentforeman.agents.command',side_effect=ValueError('absent')):
            self.assertTrue(all(not x['installed'] for x in detect()))

    def test_local_proxy_uses_local_key_and_remote_cleartext_is_rejected(self):
        from agentforeman.providers import request_json
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return b'{"choices":[{"finish_reason":"stop","message":{"content":"{\\"answer\\":\\"proxy\\"}"}}]}'
        class Opener:
            def open(inner,request,timeout):
                self.assertEqual(request.full_url,'http://127.0.0.1:8310/v1/chat/completions')
                self.assertEqual(request.get_header('Authorization'),'Bearer test-only-key')
                return Response()
        settings={'provider':'openai-compatible','base_url':'http://127.0.0.1:8310/v1','model':'local-alias','timeout_seconds':5}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test-only-key'}),patch('urllib.request.build_opener',return_value=Opener()):
            self.assertEqual(request_json(settings,{'prompt':'test','schema':SCHEMA})['answer']['answer'],'proxy')
            settings['base_url']='http://example.com/v1'
            with self.assertRaises(ValueError):request_json(settings,{'prompt':'test','schema':SCHEMA})
