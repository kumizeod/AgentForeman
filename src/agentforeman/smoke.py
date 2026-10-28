"""One bounded live provider call through a private HTTP pool and native worker."""
from __future__ import annotations

import math
import os
from pathlib import Path
import secrets
import threading
import time

from .codex import resolve_command
from .files import atomic_json
from .pool import Pool
from .pool_server import PoolServer
from .pool_worker import Worker, load_worker
from .worker_diagnostics import diagnose



def run_smoke(project,config,timeout_seconds=90,*,settings_override=None):
    if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or not 5<=timeout_seconds<=300:
        raise ValueError('Smoke timeout must be 5..300 seconds')
    settings=dict(settings_override) if settings_override is not None else load_worker(config)
    project=Path(project).resolve();sid=secrets.token_hex(6)
    root=project/'.foreman'/'smoke'/sid
    report={'id':sid,'passed':False,'status':'blocked','provider':settings['provider'],
            'model':settings.get('model'),'started_at':time.time(),
            'scope':'One bounded provider task, HTTP lease/upload and independent exact-value check; not a multi-model quality or overnight test.',
            'report_path':str(root/'report.json')}
    def save():
        report['ended_at']=time.time();atomic_json(root/'report.json',report)
        return report
    try:
        if settings['provider'] in {'codex','claude-code','gemini-cli','opencode'}:
            provider=settings['provider']
            package,bin_name={'codex':('@openai/codex','codex'),
                              'claude-code':('@anthropic-ai/claude-code','claude'),
                              'gemini-cli':('@google/gemini-cli','gemini'),
                              'opencode':('opencode-ai','opencode')}[provider]
            resolve_command(settings,npm_package=package,bin_name=bin_name)
        else:
            key_env=settings.get('key_env','ANTHROPIC_API_KEY' if settings['provider']=='anthropic' else 'OPENAI_API_KEY')
            if not os.environ.get(key_env):
                report['diagnostic']='missing_api_key_environment';return save()
    except (ValueError,OSError):
        report['diagnostic']='provider_command_unavailable';return save()
    # Private pool/token/state: no registration or tasks are added to the real
    # coordinator, no login files are copied, and no account token is persisted.
    pool=Pool(root);variable='FOREMAN_SMOKE_'+secrets.token_hex(8).upper()
    os.environ[variable]=secrets.token_urlsafe(32)
    server=None;thread=None
    try:
        pool.register('smoke',os.environ[variable])
        server=PoolServer(root,port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        task=pool.enqueue(sid,'smoke',['smoke'],{
            'prompt':'Compute 17 + 25. Return JSON with answer equal to the decimal result as a string. Do not call tools.',
            'schema':{'type':'object','additionalProperties':False,'properties':{'answer':{'type':'string','minLength':1,'maxLength':20}},'required':['answer']}},
            time.time()+timeout_seconds+10)
        worker=Worker({**settings,'coordinator':f'http://127.0.0.1:{server.server_port}',
                       'state_dir':str(root/'worker'),'token_env':variable,
                       'timeout_seconds':timeout_seconds})
        # Self-test deliberately ignores the production night window.
        worker.settings.pop('active_hours',None)
        worker.run_once()
        row=pool.tasks(sid)[0]
        report.update(task_id=task,task_status=row['status'],status='failed')
        if row['status']=='completed':
            report['passed']=row['result']['answer'].strip()==str(17+25)
            report['status']='passed' if report['passed'] else 'failed'
            report['diagnostic']='independent_check_passed' if report['passed'] else 'independent_check_failed'
        elif row['status']=='queued' and pool.members()[0]['not_before']>time.time():
            report.update(status='waiting_quota',diagnostic='quota_wait',retry_at=pool.members()[0]['not_before'])
        else:report['diagnostic']=diagnose(root/'worker')
    except (ValueError,OSError,RuntimeError):
        report.update(status='failed',diagnostic='worker_transport_or_runtime_failed')
    finally:
        if server is not None:server.shutdown();server.server_close()
        if thread is not None:thread.join(timeout=5)
        os.environ.pop(variable,None)
    return save()
