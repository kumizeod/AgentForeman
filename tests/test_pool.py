from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

from agentforeman.pool import Pool, validate
from agentforeman.pool_server import PoolServer
from agentforeman.pool_worker import Worker, available, load_worker
from agentforeman.providers import request_json

SCHEMA={'type':'object','properties':{'answer':{'type':'string','minLength':1}},'required':['answer'],'additionalProperties':False}


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.pool=Pool(self.root)
        for m in ('a','b','chair'):
            self.pool.register(m,m*40)
    def tearDown(self):
        self.tmp.cleanup()
    def enqueue(self,**kw):
        return self.pool.enqueue('group','explore',kw.get('members',['a','b']),{'prompt':'answer carefully','schema':SCHEMA},time.time()+120)
    def test_concurrent_claims_distinct_members_and_one_lease_each(self):
        for _ in range(3):self.enqueue()
        with ThreadPoolExecutor(max_workers=4) as ex:
            results=list(ex.map(lambda _:self.pool.claim('a','session-a'),range(4)))
        self.assertEqual(len({r['id'] for r in results}),1)
        b=self.pool.claim('b','session-b')
        self.assertNotEqual(b['id'],results[0]['id'])
        a=results[0];self.pool.complete('a',a['id'],a['lease'],'success',{'answer':'done'})
        self.assertIsNone(self.pool.claim('a','session-a'))
        self.assertIsNone(self.pool.claim('b','other-session'))
    def test_quota_cools_member_reassigns_to_other_and_ack_is_idempotent(self):
        tid=self.enqueue();a=self.pool.claim('a','a');reset=time.time()+18000
        for _ in range(2):self.assertTrue(self.pool.complete('a',tid,a['lease'],'quota',retry_at=reset))
        self.assertIsNone(self.pool.claim('a','a'))
        b=self.pool.claim('b','b');self.assertEqual(b['id'],tid)
        self.pool.complete('b',tid,b['lease'],'success',{'answer':'B'})
        self.pool.complete('b',tid,b['lease'],'success',{'answer':'B'})
        self.assertEqual(self.pool.tasks()[0]['attempts'],2)
    def test_expired_lease_is_uncertain_not_reassigned_and_original_can_finish(self):
        tid=self.enqueue();a=self.pool.claim('a','a',lease_seconds=-1)
        self.assertIsNone(self.pool.claim('b','b'))
        self.assertEqual(self.pool.claim('a','a')['status'],'uncertain')
        self.assertTrue(self.pool.heartbeat('a',tid,a['lease']))
        self.pool.complete('a',tid,a['lease'],'success',{'answer':'recovered'})
    def test_wrong_lease_invalid_result_and_expired_deadline_cannot_complete(self):
        tid=self.enqueue();a=self.pool.claim('a','a')
        with self.assertRaises(ValueError):self.pool.complete('b',tid,a['lease'],'success',{'answer':'wrong'})
        with self.assertRaises(ValueError):self.pool.complete('a',tid,a['lease'],'success',{'answer':'','extra':1})
        with self.pool.connect() as c:c.execute('UPDATE tasks SET deadline=? WHERE id=?',(time.time()-1,tid))
        self.pool.complete('a',tid,a['lease'],'success',{'answer':'late'})
        self.assertEqual(self.pool.tasks()[0]['status'],'expired')
    def test_strict_schema_time_window_and_config(self):
        with self.assertRaises(ValueError):validate({'answer':4},SCHEMA)
        self.assertTrue(available({'active_hours':'22:00-08:00'},datetime(2026,1,1,23)))
        self.assertFalse(available({'active_hours':'22:00-08:00'},datetime(2026,1,1,12)))
        path=self.root/'worker.toml';path.write_text('[worker]\ncoordinator="http://example.com"')
        with self.assertRaises(ValueError):load_worker(path)
        self.assertIsNone(self.pool.authenticate('wrong'))
        self.assertNotIn('token_hash',self.pool.members()[0])
        self.assertNotIn('a'*40,self.pool.db.read_bytes().decode(errors='ignore'))
    def test_real_http_worker_receipt_upload_and_auth(self):
        server=PoolServer(self.root,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        settings={'coordinator':f'http://127.0.0.1:{server.server_port}','state_dir':str(self.root/'worker'),
                  'token_env':'TEST_POOL_TOKEN','provider':'codex','timeout_seconds':10}
        try:
            tid=self.enqueue()
            with patch.dict(os.environ,{'TEST_POOL_TOKEN':'a'*40}):worker=Worker(settings)
            with patch.object(worker,'execute',return_value={'outcome':'success','result':{'answer':'real HTTP upload'},'retry_at':None}):
                self.assertTrue(worker.run_once())
            self.assertEqual(self.pool.tasks()[0]['status'],'completed')
            worker.token='bad'
            with self.assertRaises(urllib.error.HTTPError):worker.post('/claim',{'session':'a'})
        finally:server.shutdown();server.server_close();thread.join()
    def test_codex_worker_uses_real_native_supervisor_and_reuses_receipt(self):
        settings={'state_dir':str(self.root/'native-worker'),'token_env':'TEST_POOL_TOKEN',
                  'provider':'codex','timeout_seconds':10,
                  'command':[str(Path(__file__).with_name('fixture_pool.py').resolve())]}
        with patch.dict(os.environ,{'TEST_POOL_TOKEN':'a'*40}):worker=Worker(settings)
        tid=self.enqueue();task=self.pool.claim('a','native')
        result=worker.execute(task)
        self.assertEqual(result['outcome'],'success')
        self.assertIn('TEST ONLY',result['result']['answer'])
        receipt=worker.root/'tasks'/tid/'1'/'job'/'result.json'
        saved=receipt.read_bytes()
        self.assertEqual(worker.execute(task),result)
        self.assertEqual(receipt.read_bytes(),saved)

    def test_late_delivery_after_another_claim_expires_task_does_not_block_worker(self):
        tid=self.enqueue();a=self.pool.claim('a','a')
        with self.pool.connect() as c:c.execute('UPDATE tasks SET deadline=? WHERE id=?',(time.time()-1,tid))
        self.assertIsNone(self.pool.claim('b','b'))
        self.assertTrue(self.pool.complete('a',tid,a['lease'],'success',{'answer':'too late'}))
        self.assertEqual(self.pool.tasks()[0]['status'],'expired')
        self.assertIsNone(self.pool.tasks()[0]['result'])

    def test_quota_attempt_budget_is_finite(self):
        tid=self.enqueue(members=['a'])
        for _ in range(5):
            task=self.pool.claim('a','a')
            self.pool.complete('a',tid,task['lease'],'quota',retry_at=time.time()+60)
            with self.pool.connect() as c:c.execute('UPDATE members SET not_before=0 WHERE id=?',('a',))
        self.assertIsNone(self.pool.claim('a','a'))
        self.assertEqual(self.pool.tasks()[0]['status'],'failed')

    def test_lost_upload_ack_replays_outbox_without_reexecuting_model(self):
        server=PoolServer(self.root,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        settings={'coordinator':f'http://127.0.0.1:{server.server_port}','state_dir':str(self.root/'outbox-worker'),
                  'token_env':'TEST_POOL_TOKEN','provider':'codex','timeout_seconds':10}
        try:
            self.enqueue()
            with patch.dict(os.environ,{'TEST_POOL_TOKEN':'a'*40}):worker=Worker(settings)
            original=worker.post
            def lost_ack(route,value):
                response=original(route,value)
                if route=='/complete':raise OSError('TEST lost acknowledgment')
                return response
            with patch.object(worker,'execute',return_value={'outcome':'success','result':{'answer':'done'},'retry_at':None}) as execute:
                with patch.object(worker,'post',side_effect=lost_ack):
                    with self.assertRaises(OSError):worker.run_once()
                self.assertTrue((worker.root/'outbox.json').exists())
                worker.run_once()
                self.assertEqual(execute.call_count,1)
                self.assertFalse((worker.root/'outbox.json').exists())
        finally:server.shutdown();server.server_close();thread.join()

    def test_api_requests_use_local_secret_no_redirect_and_validate_output(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return json.dumps({'stop_reason':'end_turn','content':[{'type':'text','text':'{"answer":"Claude"}'}],'usage':{'input_tokens':10}}).encode()
        class Opener:
            def open(inner,req,timeout):
                self.assertEqual(req.full_url,'https://api.anthropic.com/v1/messages')
                self.assertEqual(req.get_header('X-api-key'),'secret-test-only')
                self.assertEqual(json.loads(req.data)['model'],'test-explicit-model')
                return Response()
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'secret-test-only'}),patch('urllib.request.build_opener',return_value=Opener()):
            result=request_json({'provider':'anthropic','model':'test-explicit-model','timeout_seconds':5},{'prompt':'test','schema':SCHEMA})
        self.assertEqual(result['answer'],{'answer':'Claude'})
