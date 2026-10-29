import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agentforeman.accounting import normalized, receipt
from agentforeman.files import atomic_json, atomic_text
from agentforeman.pool import Pool
from agentforeman.pool_server import PoolServer
from agentforeman.pool_worker import Worker
from agentforeman.workspace import inbox, usage, preferences, save_preferences
from tests.test_pool import SCHEMA
from tests.fixture_connection_api import FixtureAPI


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)

    def profile(self, cid, member, provider='codex'):
        atomic_json(self.project/'.foreman/connections'/cid/'connection.json',
                    {'id': cid, 'member': member, 'provider': provider, 'label': 'Account '+member, 'model': 'test', 'key_env': 'DO_NOT_EXPOSE'})

    def task(self, pool, group='a'*12, member='a'):
        tid = pool.enqueue(group, 'explore', [member], {'prompt': 'answer', 'schema': SCHEMA}, time.time()+120)
        return pool.claim(member, 'session')

    def telemetry(self, n=10):
        return {'provider': 'codex', 'model': 'test', 'input_tokens': n, 'output_tokens': 5, 'complete': True, 'source': 'client_reported'}

    def test_read_only_empty_and_invalid_period(self):
        self.assertEqual(inbox(self.project)['tasks'], [])
        self.assertEqual(usage(self.project)['totals']['attempts'], 0)
        self.assertEqual(preferences(self.project)['connections'], [])
        self.assertFalse((self.project/'.foreman').exists())
        for days in (0, 2, True, 31):
            with self.assertRaises(ValueError): usage(self.project, days)

    def test_retry_replay_failure_and_unknown_do_not_inflate_or_disappear(self):
        pool = Pool(self.project)
        for m in ('a', 'b'): pool.register(m, m*40)
        self.profile('1'*12, 'a'); self.profile('2'*12, 'b')
        pool.enqueue('a'*12, 'explore', ['a', 'b'], {'prompt': 'answer', 'schema': SCHEMA}, time.time()+120)
        a = pool.claim('a', 'session'); reset = time.time()+3600
        for _ in range(2): pool.complete('a', a['id'], a['lease'], 'quota', retry_at=reset, usage=self.telemetry())
        b = pool.claim('b', 'session')
        pool.complete('b', b['id'], b['lease'], 'failed', usage=self.telemetry(20))
        for _ in range(2): pool.complete('b', b['id'], b['lease'], 'failed', usage=self.telemetry(20))
        # Another call is in flight: its consumption is unknown, never zero.
        self.task(pool, 'b'*12, 'b')
        result = usage(self.project)
        self.assertEqual(result['totals']['total_tokens'], 40)
        self.assertEqual(result['totals']['attempts'], 3)
        self.assertEqual(result['totals']['unknown_attempts'], 1)
        self.assertEqual(sum(r['share_percent'] for r in result['connections']), 100)
        self.assertNotIn('DO_NOT_EXPOSE', json.dumps(result))
        with pool.connect() as c: c.execute('UPDATE token_usage SET recorded_at=?', (time.time()-8*86400,))
        self.assertEqual(usage(self.project)['totals']['total_tokens'], 0)
        self.assertEqual(usage(self.project, 30)['totals']['total_tokens'], 40)

    def test_lease_auth_and_invalid_receipts_cannot_change_accounting(self):
        pool = Pool(self.project); pool.register('a', 'a'*40); t = self.task(pool)
        with self.assertRaises(ValueError): pool.complete('b', t['id'], t['lease'], 'failed', usage=self.telemetry())
        for bad in ({**self.telemetry(), 'password': 'secret'}, {**self.telemetry(), 'input_tokens': True}, {**self.telemetry(), 'output_tokens': -1}):
            with self.assertRaises(ValueError): pool.complete('a', t['id'], t['lease'], 'failed', usage=bad)
        self.assertEqual(usage(self.project)['totals']['total_tokens'], 0)
        pool.cancel_group('a'*12)
        pool.complete('a', t['id'], t['lease'], 'success', {'answer': 'late'}, usage=self.telemetry())
        self.assertEqual(usage(self.project)['totals']['total_tokens'], 15)
        self.assertIsNone(pool.tasks()[0]['result'])

    def test_briefing_and_confirmed_child_are_one_task_with_combined_usage(self):
        bid, did = 'a'*12, 'b'*12
        atomic_json(self.project/'.foreman/briefings'/bid/'state.json', {'id': bid, 'idea': 'Goal', 'created_at': 1, 'status': 'launched', 'launch': {'mode': 'adversarial', 'id': did}})
        atomic_json(self.project/'.foreman/debates'/did/'state.json', {'id': did, 'question': 'Goal', 'created_at': 2, 'status': 'running', 'stage': 'revise'})
        box = inbox(self.project)
        self.assertEqual(len(box['tasks']), 1); self.assertEqual(box['tasks'][0]['stage'], 'revise')
        pool = Pool(self.project); pool.register('a', 'a'*40)
        for gid in (bid, did):
            t = self.task(pool, gid); pool.complete('a', t['id'], t['lease'], 'failed', usage=self.telemetry())
        result = usage(self.project)
        self.assertEqual(len(result['tasks']), 1); self.assertEqual(result['tasks'][0]['total_tokens'], 30)
        self.assertEqual(result['tasks'][0]['key'], 'briefings:'+bid)

    def test_preferences_persist_reject_stale_and_changed_roster(self):
        self.profile('1'*12, 'a'); self.profile('2'*12, 'b')
        saved = save_preferences(self.project, version=0, weights={'1'*12: 30, '2'*12: 70})
        self.assertEqual(saved['version'], 1)
        self.assertEqual({r['id']: r['target_share'] for r in preferences(self.project)['connections']}, {'1'*12: 30, '2'*12: 70})
        for version, weights in ((0, {'1'*12: 50, '2'*12: 50}), (1, {'1'*12: 50, '2'*12: 40}), (1, {'1'*12: 100})):
            with self.assertRaises(ValueError): save_preferences(self.project, version=version, weights=weights)

    def test_real_connection_metadata_and_smoke_usage_are_attributed(self):
        cid='1'*12
        atomic_json(self.project/'.foreman/connections'/cid/'connection.json', {'id':cid,'label':'GPT account','provider':'codex'})
        root=self.project/'.foreman/smoke'/('f'*12)
        atomic_json(root/'report.json', {'started_at':time.time(),'provider':'codex','connection_id':cid})
        folder=root/'worker/tasks/test/1'
        atomic_json(folder/'job/spec.json', {})
        atomic_text(folder/'job/stdout.log', json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'output_tokens':10}})+'\n')
        result=usage(self.project)
        self.assertEqual(result['totals']['total_tokens'],30)
        self.assertEqual(result['connections'][0]['id'],cid)
        self.assertEqual(result['connections'][0]['label'],'GPT account')
        self.assertEqual(result['tasks'][0]['title'],'连接测试')
        self.assertFalse(result['tasks'][0]['selectable'])

    def test_native_format_normalization_counts_cache_once_and_missing_unknown(self):
        self.assertEqual(normalized('claude-code', {'input_tokens': 10, 'output_tokens': 4, 'cache_read_input_tokens': 20, 'cache_creation_input_tokens': 5}), (35, 4))
        self.assertEqual(normalized('opencode', {'input': 10, 'output': 4, 'reasoning': 3, 'cache': {'read': 20, 'write': 5}}), (35, 4))
        folder = self.project/'attempt'
        atomic_text(folder/'job/stdout.log', json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 50, 'cached_input_tokens': 20, 'output_tokens': 10}})+'\n')
        self.assertEqual(receipt(folder, 'codex')['input_tokens'], 50)
        event = {'type': 'step_finish', 'part': {'id': 'step-a', 'tokens': {'input': 10, 'output': 5, 'cache': {'read': 20}}}}
        atomic_text(folder/'job/stdout.log', (json.dumps(event)+'\n')*2)
        self.assertEqual(receipt(folder, 'opencode')['input_tokens'], 30)
        atomic_text(folder/'job/stdout.log', '{"result":"no usage"}')
        self.assertIsNone(receipt(folder, 'claude-code')['input_tokens'])

    def test_real_api_worker_uploads_usage_even_when_answer_is_invalid(self):
        api = FixtureAPI(); api.correct = False
        server = PoolServer(self.project, port=0)
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (api, server)]
        for t in threads: t.start()
        try:
            server.pool.register('a', 'a'*40)
            server.pool.enqueue('a'*12, 'explore', ['a'], {'prompt': 'answer', 'schema': {**SCHEMA, 'properties': {'answer': {'type': 'string', 'enum': ['42']}}}}, time.time()+60)
            with patch.dict(os.environ, {'TEST_WORKSPACE_TOKEN': 'a'*40, 'TEST_WORKSPACE_KEY': 'test-only'}):
                w = Worker({'coordinator': f'http://127.0.0.1:{server.server_port}', 'state_dir': str(self.project/'worker'), 'token_env': 'TEST_WORKSPACE_TOKEN', 'provider': 'openai-compatible', 'key_env': 'TEST_WORKSPACE_KEY', 'base_url': api.url, 'model': 'fixture', 'timeout_seconds': 10})
                self.assertTrue(w.run_once())
            result = usage(self.project)
            self.assertEqual(server.pool.tasks()[0]['status'], 'failed')
            self.assertEqual(result['totals']['total_tokens'], 150)
            self.assertEqual(result['totals']['unknown_attempts'], 0)
        finally:
            for s in (api, server): s.shutdown(); s.server_close()
            for t in threads: t.join(timeout=5)
