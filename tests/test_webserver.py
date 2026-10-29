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

    def test_workspace_usage_and_preferences_are_local_read_only_and_versioned(self):
        for route in ('/api/workspace', '/api/token-usage', '/api/preferences'):
            self.assertEqual(self.request(route,token=False)[0],403)
            self.assertEqual(self.request(route)[0],200)
        self.assertFalse((self.project/'.foreman').exists())
        self.assertEqual(self.request('/api/token-usage?days=2')[0],400)
        self.assertEqual(self.request('/api/token-usage?days=7&days=30')[0],400)
        body={'provider':'codex','label':'GPT test'}
        cid=self.request('/api/connections',body)[1]['id']
        prefs={'version':0,'weights':{cid:100}}
        self.assertEqual(self.request('/api/preferences',prefs,origin=False)[0],403)
        self.assertEqual(self.request('/api/preferences',{**prefs,'password':'NO'})[0],400)
        self.assertEqual(self.request('/api/preferences',prefs)[0],200)
        self.assertEqual(self.request('/api/preferences',prefs)[0],400)
        self.assertEqual(self.request('/api/preferences')[1]['version'],1)

    def test_connection_routes_keep_capability_origin_and_secret_boundaries(self):
        self.assertEqual(self.request('/api/connections',token=False)[0],403)
        body={'provider':'openai-compatible','label':'Test','model':'test',
              'base_url':'https://example.invalid/v1','api_key':'TEST-CONNECTION-KEY'}
        self.assertEqual(self.request('/api/connections',body,origin=False)[0],403)
        self.assertEqual(self.request('/api/connections',body,token=False)[0],403)
        code,c,_=self.request('/api/connections',body)
        self.assertEqual(code,201);self.assertFalse(c['enabled'])
        self.assertNotIn('TEST-CONNECTION-KEY',json.dumps(c))
        self.assertNotIn('key_env',c)
        listing=self.request('/api/connections')[1]
        self.assertTrue(listing['service_running']);self.assertEqual(len(listing['connections']),1)
        self.assertNotIn('TEST-CONNECTION-KEY',json.dumps(listing))
        self.assertEqual(self.request('/api/connections/'+c['id']+'/enable',{'enabled':True})[0],400)
        self.assertEqual(self.request('/api/quick-tasks',{'idea':'Test'})[0],400)
        self.assertEqual(self.request('/api/briefings')[1],{'briefings':[]})
        self.assertEqual(self.request('/api/connections/login',{'provider':'openai-compatible'})[0],400)
        self.assertEqual(self.request('/api/connections/'+c['id']+'/test',{'command':'invalid'})[0],400)

    def test_accounts_routes_reject_passwords_and_bind_only_matching_profiles(self):
        self.assertEqual(self.request('/api/accounts',token=False)[0],403)
        body={'provider':'codex','label':'Separate ChatGPT account'}
        self.assertEqual(self.request('/api/accounts',body,origin=False)[0],403)
        self.assertEqual(self.request('/api/accounts',{**body,'password':'DO NOT ACCEPT'})[0],400)
        self.assertEqual(self.request('/api/accounts',{**body,'provider':'openai-compatible'})[0],400)
        code,profile,_=self.request('/api/accounts',body);self.assertEqual(code,201)
        self.assertNotIn('password',json.dumps(profile));self.assertFalse(profile['login_verified'])
        self.assertEqual(len(self.request('/api/accounts')[1]['accounts']),1)
        code,c,_=self.request('/api/connections',{'provider':'codex','label':'Alternate','account_id':profile['id']})
        self.assertEqual(code,201);self.assertEqual(c['account_label'],body['label'])
        self.assertEqual(self.request('/api/connections',{'provider':'claude-code','account_id':profile['id']})[0],400)
        with patch('agentforeman.connections.Connections.login',return_value={'opened':True}) as login:
            self.assertEqual(self.request('/api/connections/login',{'provider':'codex','account_id':profile['id']})[0],200)
            login.assert_called_once_with(provider='codex',account_id=profile['id'])
        self.assertEqual(self.request('/api/connections/login',{'provider':'codex','password':'DO NOT ACCEPT'})[0],400)

    def test_immediate_termination_routes_are_authenticated_and_report_offline_pending(self):
        from agentforeman.debate import Debate
        from agentforeman.pool import Pool
        pool=Pool(self.project)
        for m in ('author','critic','chair'):pool.register(m,(m+'-')*10)
        did=Debate(self.project).create('TEST ONLY stop offline worker',['author','critic'],'chair')
        lease=pool.claim('author','offline')
        self.assertIsNotNone(lease)
        route='/api/debates/'+did
        self.assertEqual(self.request(route+'/terminate',{},token=False)[0],403)
        self.assertEqual(self.request(route+'/terminate',{},origin=False)[0],403)
        self.assertEqual(self.request(route+'/terminate',{'command':'invalid'})[0],400)
        code,result,_=self.request(route+'/terminate',{});self.assertEqual(code,202)
        self.assertFalse(result['all_execution_acknowledged']);self.assertEqual(result['status'],'waiting_for_confirmation')
        self.assertEqual(self.request(route+'/termination',token=False)[0],403)
        self.assertEqual(self.request(route+'/terminate',{})[0],202)
        pool.complete('author',lease['id'],lease['lease'],'cancelled')
        self.assertTrue(self.request(route+'/termination')[1]['all_execution_acknowledged'])
        self.assertEqual(pool.tasks(did)[0]['status'],'cancelled')

    def test_immediate_campaign_termination_uses_durable_store_and_blocks_resume(self):
        from agentforeman.campaign import create_campaign
        cid=create_campaign(self.project,'TEST ONLY immediate campaign stop',1,120)
        route='/api/campaigns/'+cid
        code,result,_=self.request(route+'/terminate',{})
        self.assertEqual(code,202);self.assertTrue(result['requested'])
        self.assertTrue((self.project/'.foreman/campaigns'/cid/'terminate.request').exists())
        with patch('agentforeman.webserver.launch') as launch:
            self.assertEqual(self.request(route+'/resume',{})[0],400);launch.assert_not_called()

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
            refresh.assert_called_once_with(self.project.resolve(), 'worker', refresh=True)
        self.assertEqual(self.request('/api/usage', {'role':'invalid'})[0], 400)

    def test_general_modes_routes_authorization_and_durable_stop(self):
        from agentforeman.pool import Pool
        for route in ('debates','cooperations'):
            self.assertEqual(self.request('/api/'+route,token=False)[0],403)
            self.assertEqual(self.request('/api/'+route)[1],{'tasks':[]})
        pool=Pool(self.project)
        for member in ('author','worker','critic','chair'):pool.register(member,(member+'-')*8)
        for route in ('debates','cooperations'):
            body={'question':'Analyze data <script>not executable</script>',
                  'members':['author','critic'] if route=='debates' else ['author','worker'],
                  'chair':'chair','budget_seconds':120,'stage_seconds':30}
            if route=='cooperations':body['reviewers']=['critic']
            self.assertEqual(self.request('/api/'+route,body,origin=False)[0],403)
            self.assertEqual(self.request('/api/'+route,{**body,'command':['unexpected']})[0],400)
            code,result,_=self.request('/api/'+route,body)
            self.assertEqual(code,201);did=result['id'];base='/api/'+route+'/'+did
            self.assertEqual(self.request('/api/'+route)[1]['tasks'][0]['id'],did)
            self.assertEqual(self.request(base)[1]['status'],'running')
            self.assertEqual(self.request(base+'/report',token=False)[0],403)
            code,report,headers=self.request(base+'/report')
            self.assertEqual(code,200);self.assertIn(b'not executable',report)
            self.assertIn('attachment;',headers['Content-Disposition'])
            self.assertEqual(self.request(base+'/stop',{})[0],200)
            self.assertTrue(self.request(base)[1]['stop_requested'])
            self.assertEqual(self.request('/api/'+route+'/../../foreman.toml')[0],404)

    def test_briefing_api_explicit_version_bound_confirmation_and_auth(self):
        from agentforeman.pool import Pool
        from agentforeman.briefing import Briefing
        from tests.test_briefing import reply
        self.assertEqual(self.request('/api/briefings',token=False)[0],403)
        self.assertEqual(self.request('/api/briefings')[1],{'briefings':[]})
        pool=Pool(self.project)
        for member in ('author','critic','chair'):pool.register(member,(member+'-')*8)
        body={'idea':'Analyze sales','execution':{'mode':'adversarial','members':['author','critic'],'chair':'chair'},
              'checks':[{'id':'total','kind':'json_value','artifact_id':'report','pointer':'/total','expected':60}]}
        self.assertEqual(self.request('/api/briefings',body,origin=False)[0],403)
        code,result,_=self.request('/api/briefings',body);self.assertEqual(code,201)
        bid=result['id'];base='/api/briefings/'+bid
        self.assertEqual(self.request('/api/debates')[1]['tasks'],[])
        self.assertEqual(self.request(base+'/confirm',{'version':0,'sha256':None})[0],400)
        task=pool.claim('chair','chair');pool.complete('chair',task['id'],task['lease'],'success',reply())
        Briefing(self.project).advance(bid)
        state=self.request(base)[1]
        confirm={'version':state['version'],'sha256':state['sha256']}
        self.assertEqual(self.request(base+'/confirm',confirm,token=False)[0],403)
        self.assertEqual(self.request(base+'/confirm',{**confirm,'sha256':'wrong'})[0],400)
        code,launched,_=self.request(base+'/confirm',confirm);self.assertEqual(code,200)
        duplicate=self.request(base+'/confirm',confirm)[1]
        self.assertEqual(launched['launch'],duplicate['launch'])
        child=self.request('/api/debates/'+launched['launch']['id'])[1]
        self.assertEqual(child['task_contract']['sha256'],state['sha256'])
        self.assertEqual(self.request(base+'/message',{'version':state['version'],'content':'Silently change the goal'})[0],400)

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
            resume.assert_called_once_with(self.project.resolve(), cid, retry_failed=True)


class StoreContext:
    def __init__(self, project):
        from agentforeman.store import Store
        self.store = Store(project)
    def __enter__(self): return self.store
    def __exit__(self, *args): self.store.close()
