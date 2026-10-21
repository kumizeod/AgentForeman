from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from agentforeman.files import read_json
from agentforeman.report import verify_archive
from agentforeman.webserver import ControlPanel
from tests import test_campaign as helpers


class PanelTests(unittest.TestCase):
    setUpProject = helpers.CampaignTests.setUp
    tearDownProject = helpers.CampaignTests.tearDown

    def setUp(self):
        self.setUpProject()
        self.server = ControlPanel(self.project, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tearDownProject()

    def request(self, path, body=None, *, token=True, origin=True, headers=None):
        h = {"X-Foreman-Token": self.server.token} if token else {}
        if origin:
            h["Origin"] = self.server.origin
        if headers:
            h.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = Request(self.server.origin + path, data=data, headers=h)
        try:
            response = urlopen(req, timeout=15)
        except HTTPError as e:
            response = e
        raw = response.read()
        result = json.loads(raw) if response.headers.get_content_type() == "application/json" else raw
        return response.status, result, response.headers

    def wait(self, cid, status, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            result = self.request('/api/campaigns/' + cid)[1]
            if result["status"] == status:
                return result
            time.sleep(0.1)
        self.fail(f"Campaign did not become {status}: {result}")

    def test_static_assets_auth_host_origin_and_no_state_side_effects(self):
        code, body, headers = self.request('/', token=False)
        self.assertEqual(code, 200)
        self.assertIn(b'AgentForeman', body)
        self.assertNotIn(self.server.token.encode(), body)
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/api/overview', token=False)[0], 403)
        self.assertEqual(self.request('/api/overview', headers={'Host':'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/campaigns', {'goal':'bad'}, origin=False)[0], 403)
        self.assertEqual(self.request('/api/campaigns', {'goal':'bad'}, headers={'Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/static/../../pyproject.toml')[0], 404)
        self.assertEqual(self.request('/static/upstream/LICENSE', token=False)[0], 200)
        self.assertEqual(self.request('/api/unknown')[0], 404)
        for path in ('/api/overview','/api/stats','/api/diagnostics'):
            self.assertEqual(self.request(path)[0], 200)
        self.assertFalse((self.project / '.foreman').exists())

    def test_input_validation_and_quota_refresh_is_explicit(self):
        for body in ({}, {'goal':'x','max_rounds':True}, {'goal':'x','preflight':'yes'},
                     {'goal':'x','deadline':'yesterday'}, {'goal':'x','target':float('nan')}):
            self.assertEqual(self.request('/api/campaigns', body)[0], 400)
        self.assertFalse((self.project / '.foreman').exists())
        with patch('agentforeman.usage.probe_limits', side_effect=AssertionError('Read-only panel launched Codex')):
            self.assertEqual(self.request('/api/overview')[0], 200)
            self.assertEqual(self.request('/api/stats')[0], 200)
        with patch('agentforeman.webserver.usage_snapshot', return_value={'available':False,'error':'unavailable'}) as refresh:
            self.assertEqual(self.request('/api/usage', {'role':'worker'})[0], 200)
            refresh.assert_called_once_with(self.project, 'worker', refresh=True)
        self.assertEqual(self.request('/api/usage', {'role':'invalid'})[0], 400)

    def test_submit_real_training_detail_logs_report_and_archive(self):
        code, result, _ = self.request('/api/campaigns', {'goal':'Improve accuracy <script>alert(1)</script>', 'target':.95, 'max_rounds':2})
        self.assertEqual(code, 201)
        self.assertTrue(result['launched'])
        cid = result['id']
        detail = self.wait(cid, 'completed')
        self.assertGreater(detail['best_metric'], detail['initial_metric'])
        self.assertTrue(detail['has_archive'])
        self.assertTrue(detail['metric_criterion_met'])
        self.assertIn('Best independently verified', detail['report'])
        self.assertGreater(len(detail['events']), 0)
        self.assertNotIn('config', detail)
        for kind in ('controller','manager','worker'):
            code, log, _ = self.request(f'/api/campaigns/{cid}/logs?kind={kind}')
            self.assertEqual(code, 200)
            self.assertTrue(log['bounded'])
        self.assertEqual(self.request(f'/api/campaigns/{cid}/logs?kind=../../foreman.toml')[0], 400)
        code, archive, headers = self.request(f'/api/campaigns/{cid}/archive')
        self.assertEqual(code, 200)
        self.assertEqual(headers.get_content_type(), 'application/zip')
        path = Path(self.temp.name) / 'downloaded.zip'
        path.write_bytes(archive)
        self.assertTrue(verify_archive(path)['valid'])
        self.assertEqual(self.request('/api/overview')[1]['campaigns'][0]['goal'], detail['goal'])
        # Wait until the detached runner has closed every native handle.
        from agentforeman.platforms import alive
        root = self.project / '.foreman' / 'campaigns' / cid
        deadline = time.monotonic() + 5
        while alive(read_json(root / 'runner.json')['process']) and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertEqual(self.request(f'/api/campaigns/{cid}/resume', {})[0], 400)
        self.assertEqual(self.request(f'/api/campaigns/{cid}/stop', {})[0], 400)

    def test_quota_wait_stop_and_idempotent_resume_existing_runner(self):
        code, result, _ = self.request('/api/campaigns', {'goal':'Wait for quota', 'preflight':True})
        self.assertEqual(code, 201)
        cid = result['id']
        try:
            self.wait(cid, 'waiting_quota')
            root = self.project / '.foreman' / 'campaigns' / cid
            before = read_json(root / 'runner.json')
            self.assertEqual(self.request(f'/api/campaigns/{cid}/resume', {})[0], 200)
            self.assertEqual(read_json(root / 'runner.json')['token'], before['token'])
            self.assertEqual(self.request('/api/overview')[1]['campaigns'][0]['status'], 'waiting_quota')
        finally:
            self.request(f'/api/campaigns/{cid}/stop', {})
            detail = self.wait(cid, 'completed')
        self.assertEqual(detail['stop_reason'], 'stop_requested')
        self.assertEqual(detail['rounds'], [])
        from agentforeman.platforms import alive
        end = time.monotonic() + 5
        while alive(read_json(root / 'runner.json')['process']) and time.monotonic() < end:
            time.sleep(.05)

    def test_failed_start_returns_durable_id_instead_of_losing_submission(self):
        with patch('agentforeman.webserver.launch', side_effect=RuntimeError('startup uncertain')):
            code, result, _ = self.request('/api/campaigns', {'goal':'Keep this submission'})
        self.assertEqual(code, 201)
        self.assertFalse(result['launched'])
        self.assertEqual(self.request('/api/campaigns/' + result['id'])[1]['goal'], 'Keep this submission')

    def test_archive_integrity_guard_and_explicit_retry_contract(self):
        from agentforeman.campaign import create_campaign
        from agentforeman.store import Store
        cid = create_campaign(self.project, 'guard', 1, 60)
        with StoreContext(self.project) as store:
            state = store.get_campaign(cid)
            state['best'] = {'archive':'../../foreman.toml','archive_sha256':'not-a-hash'}
            state['status'] = 'failed'
            store.save_campaign(state, 'test_failure')
        self.assertEqual(self.request(f'/api/campaigns/{cid}/archive')[0], 400)
        self.assertEqual(self.request(f'/api/campaigns/{cid}/resume', {})[0], 400)
        with patch('agentforeman.webserver.launch', return_value={'status':'running'}) as resume:
            self.assertEqual(self.request(f'/api/campaigns/{cid}/resume', {'retry_failed':True})[0], 200)
            resume.assert_called_once_with(self.project, cid, retry_failed=True)


class StoreContext:
    def __init__(self, project):
        from agentforeman.store import Store
        self.store = Store(project)
    def __enter__(self): return self.store
    def __exit__(self, *args): self.store.close()
