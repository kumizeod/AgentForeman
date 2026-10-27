import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentforeman.cli import main
from agentforeman.files import read_json
from agentforeman.smoke import run_smoke


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.config=self.root/'worker.toml'
        fixture=Path(__file__).with_name('fixture_smoke.py').resolve().as_posix()
        self.config.write_text('[worker]\ncoordinator="http://127.0.0.1:1"\nprovider="codex"\n'
                               'state_dir="production-worker"\nactive_hours="23:00-23:01"\n'
                               'command=['+json.dumps(fixture)+']\n',encoding='utf-8')
    def tearDown(self):self.tmp.cleanup()
    def test_real_http_native_process_and_independent_check_with_scripted_provider(self):
        result=run_smoke(self.root,self.config,10)
        self.assertTrue(result['passed']);self.assertEqual(result['status'],'passed')
        self.assertEqual(result['diagnostic'],'independent_check_passed')
        self.assertFalse((self.root/'production-worker').exists())
        self.assertFalse((self.root/'.foreman/pool.sqlite3').exists())
        stored=read_json(self.root/'.foreman/smoke'/result['id']/'report.json')
        self.assertEqual(stored,result)
    def test_wrong_answer_is_not_accepted_despite_successful_provider_exit(self):
        with patch.dict(os.environ,{'FOREMAN_SMOKE_FIXTURE_MODE':'wrong'}):result=run_smoke(self.root,self.config,10)
        self.assertFalse(result['passed']);self.assertEqual(result['diagnostic'],'independent_check_failed')
    def test_authentication_failure_has_no_raw_provider_body_in_report(self):
        with patch.dict(os.environ,{'FOREMAN_SMOKE_FIXTURE_MODE':'auth'}):result=run_smoke(self.root,self.config,10)
        self.assertFalse(result['passed']);self.assertEqual(result['diagnostic'],'authentication_failed')
        self.assertNotIn('secret-value',json.dumps(result))
    def test_quota_is_reported_without_automatic_retry(self):
        with patch.dict(os.environ,{'FOREMAN_SMOKE_FIXTURE_MODE':'quota'}):result=run_smoke(self.root,self.config,10)
        self.assertFalse(result['passed']);self.assertEqual(result['status'],'waiting_quota')
        self.assertIn('retry_at',result)
    def test_missing_command_is_blocked_before_task_submission(self):
        self.config.write_text('[worker]\ncoordinator="http://127.0.0.1:1"\ncommand=["foreman-no-such-command"]\n')
        result=run_smoke(self.root,self.config,10)
        self.assertEqual(result['diagnostic'],'provider_command_unavailable')
        self.assertFalse((self.root/'.foreman/smoke'/result['id']/'.foreman/pool.sqlite3').exists())
    def test_worker_once_cli_exits_nonzero_after_failed_execution(self):
        from contextlib import redirect_stdout
        import io
        # Test the CLI contract without requiring another live model call.
        with patch('agentforeman.pool_worker.Worker.run',return_value={'status':'failed','diagnostic':'authentication_failed'}), \
             patch('agentforeman.pool_worker.Worker.__init__',return_value=None),redirect_stdout(io.StringIO()) as out:
            code=main(['pool','worker','--config',str(self.config),'--once'])
        self.assertEqual(code,2);self.assertIn('authentication_failed',out.getvalue())
