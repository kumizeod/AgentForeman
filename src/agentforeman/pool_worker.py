"""Member-owned pull worker; no login credentials leave this machine."""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import tomllib
import urllib.parse
import urllib.request

from .codex import json_spec
from .files import atomic_json, read_json
from .job import wait_for_job
from .platforms import file_lock
from .pool import name, validate
from .providers import NoRedirect
from .quota import classify


def load_worker(path):
    path = Path(path).resolve()
    data = tomllib.loads(path.read_text(encoding='utf-8'))['worker']
    allowed = {'coordinator','token_env','state_dir','provider','model','command','timeout_seconds','active_hours','key_env','base_url','max_output_tokens'}
    if set(data)-allowed:
        raise ValueError('Unknown worker setting; secrets belong in environment variables')
    parsed = urllib.parse.urlsplit(data['coordinator'])
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
        raise ValueError('Coordinator must be an HTTP(S) origin')
    if parsed.scheme=='http' and parsed.hostname not in {'127.0.0.1','localhost','::1'}:
        raise ValueError('Remote coordinator requires HTTPS (use a TLS reverse proxy or localhost SSH forwarding)')
    data['coordinator'] = data['coordinator'].rstrip('/')
    data.setdefault('provider','codex');data.setdefault('timeout_seconds',600)
    data.setdefault('token_env','FOREMAN_WORKER_TOKEN')
    if data['provider'] not in {'codex','anthropic','openai-compatible'}:
        raise ValueError('Unknown worker provider')
    if type(data['timeout_seconds']) not in {int,float} or not math.isfinite(data['timeout_seconds']) or not 1<=data['timeout_seconds']<=7200:
        raise ValueError('Worker timeout must be between 1 and 7200 seconds')
    for k in ('token_env','key_env'):
        if k in data and (not isinstance(data[k],str) or not data[k].isidentifier()):
            raise ValueError('Invalid credential environment variable name')
    if 'command' in data and (not isinstance(data['command'],list) or not data['command'] or any(not isinstance(x,str) or not x for x in data['command'])):
        raise ValueError('Worker command must be an argument list')
    if data['provider']!='codex' and not data.get('model'):
        raise ValueError('Choose an explicit API model available to your account')
    if type(data.get('max_output_tokens',4096)) is not int or not 128<=data.get('max_output_tokens',4096)<=32000:
        raise ValueError('max_output_tokens must be 128..32000')
    if 'active_hours' in data:
        hours(data['active_hours'])
    data['state_dir'] = str((path.parent / data.get('state_dir','worker-state')).resolve())
    return data


def hours(value):
    try:
        start,end = value.split('-')
        times = [datetime.strptime(v,'%H:%M') for v in (start,end)]
        return tuple(v.hour*60+v.minute for v in times)
    except (ValueError,TypeError,AttributeError):
        raise ValueError('active_hours must be HH:MM-HH:MM') from None


def available(settings, now=None):
    if 'active_hours' not in settings:
        return True
    start,end = hours(settings['active_hours'])
    now = now or datetime.now()
    minute = now.hour*60+now.minute
    return True if start==end else (start<=minute<end if start<end else minute>=start or minute<end)


class Worker:
    def __init__(self, settings):
        self.settings = settings
        self.root = Path(settings['state_dir'])
        self.root.mkdir(parents=True,exist_ok=True)
        self.token = os.environ.get(settings['token_env'])
        if not self.token or len(self.token)<32:
            raise ValueError('Configured worker token environment variable is empty/too short')
        identity = self.root/'identity.json'
        with file_lock(self.root/'identity.lock'):
            if not identity.exists():
                atomic_json(identity,{'session':secrets.token_hex(16)})
            self.session = name(read_json(identity)['session'])

    def post(self, route, value):
        request = urllib.request.Request(self.settings['coordinator']+route, data=json.dumps(value,allow_nan=False).encode(),
                   headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},method='POST')
        with urllib.request.build_opener(NoRedirect).open(request,timeout=15) as response:
            raw = response.read(1000001)
        if len(raw)>1000000:
            raise ValueError('Coordinator response too large')
        return json.loads(raw)

    def run_once(self):
        # Persist-before-send makes settlement acknowledgment loss safe, including quota.
        outbox = self.root/'outbox.json'
        if outbox.exists():
            self.post('/complete',read_json(outbox))
            outbox.unlink()
        if not available(self.settings):
            return False
        task = self.post('/claim',{'session':self.session})['task']
        if task is None:
            return False
        name(task['id'])
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(15):
                try:
                    if not self.post('/heartbeat',{'task':task['id'],'lease':task['lease']})['accepted']:
                        return
                except Exception:
                    pass  # Lease becomes uncertain; it is never handed to a different member.
        thread = threading.Thread(target=heartbeat,daemon=True)
        thread.start()
        try:
            value = self.execute(task)
            body = {'task':task['id'],'lease':task['lease'],**value}
            atomic_json(outbox,body)
            self.post('/complete',body)
            outbox.unlink()
        finally:
            stop.set();thread.join(timeout=16)
        return value['outcome']!='uncertain'

    def execute(self, task):
        folder = self.root/'tasks'/task['id']/str(task['attempts'])
        folder.mkdir(parents=True,exist_ok=True)
        request_path = folder/'request.json'
        if request_path.exists() and read_json(request_path)!=task['request']:
            raise ValueError('Coordinator changed an immutable task request')
        atomic_json(request_path,task['request'])
        job = folder/'job'
        if time.time()>=task['deadline']:
            return {'outcome':'failed','result':None,'retry_at':None}
        try:
            if (job/'spec.json').exists():
                spec = read_json(job/'spec.json')
            else:
                timeout = min(self.settings['timeout_seconds'],task['deadline']-time.time())
                if self.settings['provider']=='codex':
                    context = folder/'context';context.mkdir(exist_ok=True)
                    spec = json_spec(folder,context,task['request']['prompt'],task['request']['schema'],self.settings,sandbox='read-only')
                else:
                    # Only environment variable names, never their secret values.
                    settings_path = folder/'provider.json'
                    atomic_json(settings_path,{k:v for k,v in self.settings.items() if k in {'provider','model','key_env','base_url','max_output_tokens','timeout_seconds'}})
                    spec = {'argv':[sys.executable,'-m','agentforeman.providers',str(settings_path),str(request_path),str(folder/'answer.json')],
                            'cwd':str(folder),'env':{},'timeout_seconds':timeout}
                spec.update(timeout_seconds=timeout,deadline_at=task['deadline'])
            receipt = wait_for_job(job,spec)
            if receipt['exit_code']==0 and not any(receipt[k] for k in ('error','cancelled','timed_out')):
                answer = validate(read_json(folder/'answer.json'),task['request']['schema'])
                return {'outcome':'success','result':answer,'retry_at':None}
            quota = classify(job,receipt) if self.settings['provider']=='codex' else None
            if quota:
                reset = quota.get('reported_reset_at')
                return {'outcome':'quota','result':None,'retry_at':max(time.time()+60,reset or time.time()+18000)}
            status = read_json(folder/'api-status.json') if (folder/'api-status.json').exists() else {}
            if status.get('outcome')=='quota':
                return {'outcome':'quota','result':None,'retry_at':max(time.time()+60,status['retry_at'])}
            return {'outcome':'failed','result':None,'retry_at':None}
        except RuntimeError:
            # An uncertain native supervisor is never interpreted as safe to replay.
            return {'outcome':'uncertain','result':None,'retry_at':None}
        except (ValueError,OSError,KeyError):
            return {'outcome':'failed','result':None,'retry_at':None}

    def run(self, once=False, poll_seconds=10):
        with file_lock(self.root/'worker.lock'):
            while True:
                try:
                    worked = self.run_once()
                except (OSError,ValueError):
                    if once:
                        raise RuntimeError('Worker request failed; credentials and provider details are not printed') from None
                    worked = False
                if once:
                    return
                if not worked:
                    time.sleep(poll_seconds)
