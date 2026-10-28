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
from .files import atomic_json, atomic_text, read_json
from .job import wait_for_job
from .platforms import file_lock
from .pool import name, validate
from .providers import NoRedirect
from .quota import classify


def load_worker(path):
    path = Path(path).resolve()
    data = tomllib.loads(path.read_text(encoding='utf-8'))['worker']
    allowed = {'coordinator','token_env','state_dir','provider','model','command','timeout_seconds','active_hours','key_env','base_url','max_output_tokens','account_dir'}
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
    if data['provider'] not in {'codex','claude-code','gemini-cli','antigravity','opencode','anthropic','openai-compatible'}:
        raise ValueError('Unknown worker provider')
    if type(data['timeout_seconds']) not in {int,float} or not math.isfinite(data['timeout_seconds']) or not 1<=data['timeout_seconds']<=7200:
        raise ValueError('Worker timeout must be between 1 and 7200 seconds')
    for k in ('token_env','key_env'):
        if k in data and (not isinstance(data[k],str) or not data[k].isidentifier()):
            raise ValueError('Invalid credential environment variable name')
    if 'command' in data and (not isinstance(data['command'],list) or not data['command'] or any(not isinstance(x,str) or not x for x in data['command'])):
        raise ValueError('Worker command must be an argument list')
    if data['provider'] in {'anthropic','openai-compatible'} and not data.get('model'):
        raise ValueError('Choose an explicit API model available to your account')
    if type(data.get('max_output_tokens',4096)) is not int or not 128<=data.get('max_output_tokens',4096)<=32000:
        raise ValueError('max_output_tokens must be 128..32000')
    if 'active_hours' in data:
        hours(data['active_hours'])
    if 'account_dir' in data:
        if data['provider'] not in {'codex','claude-code'} or not isinstance(data['account_dir'],str):raise ValueError('Invalid independent account directory')
        data['account_dir']=str((path.parent/data['account_dir']).resolve())
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
        self.last_attempt={'status':'no_task','model_called':False}
        # Persist-before-send makes settlement acknowledgment loss safe, including quota.
        outbox = self.root/'outbox.json'
        if outbox.exists():
            pending=read_json(outbox)
            self.post('/complete',pending)
            self.last_attempt={'status':pending['outcome'],'task_id':pending['task'],
                               'model_called':False,'completion_acknowledged':True,
                               'retry_at':pending.get('retry_at'),'replayed_upload':True}
            atomic_json(self.root/'last-attempt.json',self.last_attempt)
            outbox.unlink()
        if not available(self.settings):
            self.last_attempt['status']='inactive_window'
            return False
        task = self.post('/claim',{'session':self.session})['task']
        if task is None:
            return False
        name(task['id']);name(task['lease'])
        if type(task['attempts']) is not int or not 1<=task['attempts']<=5 or type(task['deadline']) not in (int,float) or not math.isfinite(task['deadline']):
            raise ValueError('Invalid bounded task lease')
        stop = threading.Event()
        cancellation=threading.Event()
        job=self.root/'tasks'/task['id']/str(task['attempts'])/'job'
        control={'task':task['id'],'lease':task['lease']}
        def check_cancel():
            if self.post('/control',control)['cancel_requested']:
                cancellation.set();atomic_text(job/'cancel.request','cancel\n');return True
            return False
        # Check before launching as well as during the call; durable markers close
        # cancellation races while the native supervisor is starting.
        try:check_cancel()
        except (OSError,ValueError,KeyError):pass
        def heartbeat():
            next_beat=0
            while not stop.wait(1):
                try:
                    if check_cancel():return
                    if time.monotonic()>=next_beat:
                        self.post('/heartbeat',control);next_beat=time.monotonic()+15
                except Exception:
                    pass  # No network response is not a cancellation confirmation.
        thread = threading.Thread(target=heartbeat,daemon=True)
        thread.start()
        try:
            value = ({'outcome':'cancelled','result':None,'retry_at':None} if cancellation.is_set() and not (job/'spec.json').exists() and not (job/'process.json').exists() else self.execute(task))
            if cancellation.is_set() and value['outcome']!='uncertain':
                # Do not acknowledge an old/uncertain supervisor as stopped just
                # because a cancellation intent was received after restart.
                uncertain=False
                if (job/'spec.json').exists():
                    try:
                        from .platforms import alive
                        receipt=read_json(job/'result.json')
                        meta=read_json(job/'process.json') if (job/'process.json').exists() else {}
                        child=meta.get('child') or (read_json(job/'child.json') if (job/'child.json').exists() else None)
                        uncertain=bool(receipt.get('error') or alive(child))
                    except (OSError,ValueError,KeyError):uncertain=True
                value={'outcome':'uncertain' if uncertain else 'cancelled','result':None,'retry_at':None}
            self.last_attempt={'status':value['outcome'],'task_id':task['id'],
                               'model_called':None,'retry_at':value.get('retry_at'),
                               'completion_acknowledged':False}
            if value['outcome'] in {'failed','uncertain'}:
                from .worker_diagnostics import diagnose
                self.last_attempt['diagnostic']=diagnose(self.root/'tasks'/task['id']/str(task['attempts']))
            body = {'task':task['id'],'lease':task['lease'],**value}
            atomic_json(outbox,body)
            atomic_json(self.root/'last-attempt.json',self.last_attempt)
            self.post('/complete',body)
            self.last_attempt['completion_acknowledged']=True
            atomic_json(self.root/'last-attempt.json',self.last_attempt)
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
                elif self.settings['provider'] in {'claude-code','gemini-cli','antigravity','opencode'}:
                    from .agents import make_spec
                    spec=make_spec(folder,folder/'context',task['request'],self.settings)
                else:
                    # Only environment variable names, never their secret values.
                    settings_path = folder/'provider.json'
                    atomic_json(settings_path,{k:v for k,v in self.settings.items() if k in {'provider','model','key_env','base_url','max_output_tokens','timeout_seconds'}})
                    spec = {'argv':[sys.executable,'-m','agentforeman.providers',str(settings_path),str(request_path),str(folder/'answer.json')],
                            'cwd':str(folder),'env':{},'timeout_seconds':timeout}
                spec.update(timeout_seconds=timeout,deadline_at=task['deadline'])
            receipt = wait_for_job(job,spec)
            if receipt['cancelled']:
                return {'outcome':'uncertain' if receipt.get('error') else 'cancelled','result':None,'retry_at':None}
            if self.settings['provider'] in {'claude-code','gemini-cli','antigravity','opencode'}:
                from .agents import structured_quota
                pause=structured_quota(job,receipt)
                if pause:return {'outcome':'quota','result':None,'retry_at':pause['retry_at']}
            if receipt['exit_code']==0 and not any(receipt[k] for k in ('error','cancelled','timed_out')):
                if self.settings['provider'] in {'claude-code','gemini-cli','antigravity','opencode'}:
                    from .agents import read_answer
                    answer=read_answer(job,folder,task['request'],self.settings['provider'])
                else:
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
        except (ValueError,OSError,KeyError,TypeError):
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
                    return self.last_attempt
                if not worked:
                    time.sleep(poll_seconds)
