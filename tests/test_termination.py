from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agentforeman.debate import Debate
from agentforeman.files import atomic_json,atomic_text,read_json
from agentforeman.job import wait_for_job
from agentforeman.platforms import alive
from agentforeman.pool import Pool
from agentforeman.pool_server import PoolServer
from agentforeman.pool_worker import Worker
from agentforeman.termination import request,status

SCHEMA={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer']}


class TerminationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.pool=Pool(self.root)
        for m in ('author','critic','chair'):self.pool.register(m,(m+'-')*10)
    def tearDown(self):self.tmp.cleanup()
    def until(self,predicate,seconds=15):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            value=predicate()
            if value:return value
            time.sleep(.05)
        self.fail('Timed out waiting for termination receipt')
    def enqueue(self,group='task_group',member='author'):
        return self.pool.enqueue(group,'test',[member],{'prompt':'TEST ONLY','schema':SCHEMA},time.time()+120)
    def test_cancel_is_durable_blocks_new_tasks_and_retains_offline_unknown(self):
        first=self.enqueue();leased=self.pool.claim('author','author');self.enqueue(member='critic')
        result=self.pool.cancel_group('task_group')
        self.assertFalse(result['all_workers_acknowledged'])
        self.assertIsNone(self.pool.claim('critic','critic'))
        future=self.enqueue(member='critic');rows={r['id']:r for r in self.pool.tasks()}
        self.assertEqual(rows[future]['status'],'cancelled')
        self.pool=Pool(self.root)
        self.assertTrue(self.pool.control('author',first,leased['lease'])['cancel_requested'])
        with self.assertRaises(ValueError):self.pool.control('critic',first,leased['lease'])
        self.pool.complete('author',first,leased['lease'],'uncertain')
        self.assertFalse(self.pool.cancellation('task_group')['all_workers_acknowledged'])
        self.assertIsNone(self.pool.claim('author','different-session'))
        self.pool.complete('author',first,leased['lease'],'cancelled')
        self.assertTrue(self.pool.cancellation('task_group')['all_workers_acknowledged'])
        self.pool.complete('author',first,leased['lease'],'cancelled')
    def test_late_success_after_cancellation_is_acknowledged_but_never_accepted(self):
        tid=self.enqueue();task=self.pool.claim('author','author');self.pool.cancel_group('task_group')
        self.pool.complete('author',tid,task['lease'],'success',{'answer':'late output'})
        row=self.pool.tasks()[0];self.assertEqual(row['status'],'cancelled');self.assertIsNone(row['result'])
        self.assertTrue(self.pool.cancellation('task_group')['all_workers_acknowledged'])
    def test_cancellation_before_native_launch_never_starts_the_command(self):
        root=self.root/'.foreman/debates/0123456789ab';root.mkdir(parents=True)
        atomic_json(root/'state.json',{'id':root.name})
        request(self.root,'debates',root.name)
        marker=root/'should-not-exist'
        result=wait_for_job(root/'jobs/first',{'argv':[sys.executable,'-c','from pathlib import Path;import sys;Path(sys.argv[1]).write_text("BAD")',str(marker)],
                   'cwd':str(root),'env':{},'timeout_seconds':10})
        self.assertTrue(result['cancelled']);self.assertIsNone(result['error']);self.assertFalse(marker.exists())
        self.assertTrue(status(self.root,'debates',root.name)['all_execution_acknowledged'])
    def test_termination_of_running_remote_worker_kills_cli_and_descendant_even_when_member_paused(self):
        did=Debate(self.root).create('TEST ONLY cancel running task',['author','critic'],'chair')
        server=PoolServer(self.root,port=0);server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
        marker=self.root/'started.json'
        settings={'provider':'claude-code','state_dir':str(self.root/'remote-worker'),'token_env':'FOREMAN_CANCEL_TEST',
           'coordinator':f'http://127.0.0.1:{server.server_port}','timeout_seconds':60,
           'command':[sys.executable,str(Path(__file__).with_name('fixture_long_cli.py').resolve()),str(marker)]}
        with patch.dict(os.environ,{'FOREMAN_CANCEL_TEST':('author-'*10)}):worker=Worker(settings)
        outcomes=[];errors=[]
        def run():
            try:outcomes.append(worker.run_once())
            except Exception as e:errors.append(e)
        runner=threading.Thread(target=run);runner.start()
        try:
            self.until(marker.exists);processes=read_json(marker);self.assertTrue(alive(processes['child']))
            # Pause blocks new claims but must not block cancellation or settlement.
            with self.pool.connect() as db:db.execute("UPDATE members SET enabled=0 WHERE id='author'")
            before=time.monotonic();result=request(self.root,'debates',did)
            self.assertFalse(result['all_execution_acknowledged'])
            runner.join(timeout=12);self.assertFalse(runner.is_alive());self.assertEqual(errors,[])
            self.assertLess(time.monotonic()-before,12)
            self.assertFalse(alive(processes['parent']));self.assertFalse(alive(processes['child']))
            final=status(self.root,'debates',did);self.assertTrue(final['all_execution_acknowledged'],final)
            self.assertEqual(final['provider_generation_stopped'],'unknown')
            self.assertTrue(all(t['result'] is None for t in self.pool.tasks(did)))
            self.assertEqual(len(list((worker.root/'tasks').glob('*/*/job/result.json'))),1)
        finally:
            request(self.root,'debates',did);runner.join(timeout=12)
            server.shutdown();server.server_close();server_thread.join()
    def test_terminating_briefing_cascades_to_confirmed_child_and_blocks_reconfirmation(self):
        from agentforeman.briefing import Briefing
        from tests.test_briefing import reply
        b=Briefing(self.root);bid=b.create('TEST ONLY stop confirmed task',{'mode':'adversarial','members':['author','critic'],'chair':'chair'})
        chair=self.pool.claim('chair','chair');self.pool.complete('chair',chair['id'],chair['lease'],'success',reply(manual=True))
        ready=b.advance(bid);launched=b.confirm(bid,ready['version'],ready['sha256'])
        child=launched['launch']['id'];lease=self.pool.claim('author','offline')
        self.assertEqual(lease['group_id'],child)
        result=request(self.root,'briefings',bid);self.assertFalse(result['all_execution_acknowledged'])
        self.assertTrue(result['children'][0]['requested'])
        self.pool.complete('author',lease['id'],lease['lease'],'cancelled')
        self.assertTrue(status(self.root,'briefings',bid)['all_execution_acknowledged'])
        with self.assertRaises(ValueError):b.confirm(bid,ready['version'],ready['sha256'])
        self.assertEqual(len(Debate(self.root).list()),1)

    def test_termination_during_crash_recovery_blocks_a_saved_launch_intent(self):
        from agentforeman.briefing import Briefing
        from tests.test_briefing import reply
        b=Briefing(self.root);bid=b.create('TEST ONLY recover launch',{'mode':'adversarial','members':['author','critic'],'chair':'chair'})
        task=self.pool.claim('chair','chair');self.pool.complete('chair',task['id'],task['lease'],'success',reply(manual=True))
        ready=b.advance(bid)
        with patch.object(Debate,'create',side_effect=RuntimeError('TEST crash before child create')):
            with self.assertRaises(RuntimeError):b.confirm(bid,ready['version'],ready['sha256'])
        intent=b.get(bid)['launch']['id'];request(self.root,'briefings',bid)
        recovered=b.advance(bid);self.assertEqual(recovered['status'],'stopped')
        self.assertFalse((self.root/'.foreman/debates'/intent/'state.json').exists())
        self.assertEqual(Debate(self.root).list(),[])

    def test_cancelled_uncertain_job_is_not_falsely_acknowledged_after_worker_restart(self):
        tid=self.enqueue();task=self.pool.claim('author','session');self.pool.cancel_group('task_group')
        settings={'provider':'codex','state_dir':str(self.root/'uncertain-worker'),'token_env':'FOREMAN_CANCEL_TEST','timeout_seconds':30}
        with patch.dict(os.environ,{'FOREMAN_CANCEL_TEST':'t'*40}):worker=Worker(settings)
        job=worker.root/'tasks'/tid/'1/job';job.mkdir(parents=True);atomic_json(job/'spec.json',{'uncertain':'TEST ONLY'})
        bodies=[]
        def post(route,body):
            if route=='/claim':return {'task':task}
            if route=='/control':return {'cancel_requested':True}
            if route=='/complete':bodies.append(body);return {'accepted':True}
            raise AssertionError(route)
        with patch.object(worker,'post',side_effect=post),patch.object(worker,'execute',return_value={'outcome':'uncertain','result':None,'retry_at':None}) as execute:
            worker.run_once();execute.assert_called_once()
        self.assertEqual(bodies[0]['outcome'],'uncertain')
    @unittest.skipUnless(sys.platform=='darwin','macOS zombie process-group semantics')
    def test_finished_root_is_not_force_signalled_as_a_zombie_group(self):
        import signal
        from agentforeman.quota import probe_limits
        import agentforeman.platforms as platforms
        original=platforms.os.killpg
        def reject_escalation(pid,sig):
            if sig==signal.SIGKILL:raise PermissionError('TEST macOS empty group')
            return original(pid,sig)
        fixture=Path(__file__).with_name('fixture_campaign.py').resolve()
        with patch('agentforeman.platforms.os.killpg',side_effect=reject_escalation):
            result=probe_limits({'command':[sys.executable,str(fixture)]},timeout=5)
        self.assertTrue(result['ok'])

    def test_api_termination_closes_live_http_request_without_claiming_provider_stopped(self):
        started=threading.Event();disconnected=threading.Event()
        class API(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(inner):
                inner.rfile.read(int(inner.headers['Content-Length']));started.set()
                inner.connection.settimeout(15)
                try:
                    if inner.connection.recv(1)==b'':disconnected.set()
                except (ConnectionResetError,ConnectionAbortedError):disconnected.set()
                except OSError:pass
        api=ThreadingHTTPServer(('127.0.0.1',0),API);at=threading.Thread(target=api.serve_forever,daemon=True);at.start()
        server=PoolServer(self.root,port=0);st=threading.Thread(target=server.serve_forever,daemon=True);st.start()
        did=Debate(self.root).create('TEST ONLY cancel API request',['author','critic'],'chair')
        settings={'provider':'openai-compatible','model':'test','key_env':'FOREMAN_CANCEL_KEY','token_env':'FOREMAN_CANCEL_TEST',
                  'base_url':f'http://127.0.0.1:{api.server_port}/v1','state_dir':str(self.root/'api-worker'),
                  'coordinator':f'http://127.0.0.1:{server.server_port}','timeout_seconds':60}
        with patch.dict(os.environ,{'FOREMAN_CANCEL_TEST':'author-'*10,'FOREMAN_CANCEL_KEY':'TEST ONLY'}):
            worker=Worker(settings);runner=threading.Thread(target=worker.run_once);runner.start()
            try:
                self.assertTrue(started.wait(15));request(self.root,'debates',did)
                runner.join(timeout=12);self.assertFalse(runner.is_alive());self.assertTrue(disconnected.wait(3))
                self.assertTrue(status(self.root,'debates',did)['all_execution_acknowledged'])
                self.assertEqual(status(self.root,'debates',did)['provider_generation_stopped'],'unknown')
            finally:
                request(self.root,'debates',did);runner.join(timeout=12)
                server.shutdown();server.server_close();st.join();api.shutdown();api.server_close();at.join()
