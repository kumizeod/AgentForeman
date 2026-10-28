"""Local connection onboarding. No subscription tokens are read or centralized."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from .agents import CLIS, detect
from .files import atomic_json, read_json
from .platforms import file_lock
from .pool import Pool
from .pool_worker import Worker, hours, available

PROVIDERS={*CLIS,'anthropic','openai-compatible'}
GUIDES={
    'codex':{'label':'Codex / ChatGPT','login':'codex login','install':'npm install -g @openai/codex'},
    'antigravity':{'label':'Antigravity / Google AI Pro','login':'agy','install':'https://antigravity.google/download'},
    'gemini-cli':{'label':'Gemini CLI（企业 / 旧 API）','login':'gemini','install':'npm install -g @google/gemini-cli'},
    'claude-code':{'label':'Claude Code','login':'claude auth login','install':'https://code.claude.com/docs/en/setup'},
    'opencode':{'label':'OpenCode / Go','login':'opencode auth login','install':'npm install -g opencode-ai'},
}


class Connections:
    def __init__(self,project):
        self.project=Path(project).resolve();self.root=self.project/'.foreman/connections'
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.stop=threading.Event();self.threads={};self.testing=set();self.tests={};self.logins={}
        self.pool=Pool(self.project);self.server=None;self.owner=None;self.error=None
        try:
            self.owner=file_lock(self.root/'service.lock');self.owner.__enter__()
        except BlockingIOError:
            self.owner=None;self.error='Another console owns local connections. Use that console.'
        if self.owner:
            from .pool_server import PoolServer
            try:
                self.server=PoolServer(self.project,port=0)
                threading.Thread(target=self.server.serve_forever,daemon=True).start()
                for c in self.records():
                    self.restore_key(c)
                    if c.get('enabled') and c.get('test',{}).get('passed'):self.start(c)
            except Exception:
                self.close();raise

    def records(self):return [read_json(p) for p in sorted(self.root.glob('*/connection.json'))]

    def path(self,cid):
        if not isinstance(cid,str) or len(cid)!=12 or any(x not in '0123456789abcdef' for x in cid):
            raise ValueError('Invalid connection ID')
        return self.root/cid

    def get(self,cid):return read_json(self.path(cid)/'connection.json')
    def save(self,c):atomic_json(self.path(c['id'])/'connection.json',c)

    def require_owner(self):
        if not self.owner:raise ValueError(self.error or 'Connection service unavailable')

    @staticmethod
    def keyring():
        try:
            import keyring
            backend=keyring.get_keyring()
            if type(backend).__module__ not in {'keyring.backends.Windows','keyring.backends.macOS','keyring.backends.SecretService'}:
                raise ValueError('Native OS credential store unavailable; choose session-only or an environment variable.')
            return keyring
        except ImportError:
            raise ValueError('Install agentforeman[desktop] for OS credential storage, or choose session-only.') from None

    def restore_key(self,c):
        if c.get('secret_storage')=='os':
            try:
                value=self.keyring().get_password('AgentForeman',str(self.project)+':'+c['id'])
                if value:os.environ[c['key_env']]=value
            except Exception:pass  # List as needing a key; never expose credential backend errors.

    def login(self,provider,account_id=None):
        self.require_owner()
        if provider not in CLIS:raise ValueError('Login is only available for local clients')
        import shutil
        import subprocess
        import sys
        from .agents import command
        settings={'provider':provider}
        if account_id:
            from .accounts import Accounts
            settings['account_dir']=str(Accounts(self.project).directory(account_id,provider))
        from .accounts import profile_environment
        profile_env,unset=profile_environment(settings)
        environment=os.environ.copy()
        for key in unset:environment.pop(key,None)
        environment.update(profile_env)
        login_key=provider+('_'+account_id if account_id else '')
        import shlex
        login_argv=None
        argv=command(settings)+{'codex':['login'],'gemini-cli':[],'antigravity':[],
                    'claude-code':['auth','login'],'opencode':['auth','login']}[provider]
        if time.time()-self.logins.get(login_key,0)<15:raise ValueError('Login was just opened; complete it in that terminal')
        login_argv=(['env',*[arg for key in unset for arg in ('-u',key)],*[k+'='+v for k,v in profile_env.items()],*argv] if profile_env else argv)
        if sys.platform=='win32':
            subprocess.Popen(argv,cwd=self.root,env=environment,creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif sys.platform=='darwin':
            from .files import atomic_text
            script=self.root/(login_key+'-login.command')
            atomic_text(script,'#!/bin/sh\nexec '+shlex.join(login_argv)+'\n');script.chmod(0o700)
            subprocess.Popen(['open','-a','Terminal',str(script)])
        else:
            terminal=next((shutil.which(x) for x in ('x-terminal-emulator','gnome-terminal','konsole','xterm') if shutil.which(x)),None)
            if not terminal:
                if profile_env:
                    from .files import atomic_text
                    script=self.root/(login_key+'-login.command')
                    atomic_text(script,'#!/bin/sh\nexec '+shlex.join(login_argv)+'\n');script.chmod(0o700)
                    raise ValueError('No desktop terminal found. Run this independent-profile login script: '+shlex.quote(str(script)))
                raise ValueError('No desktop terminal found. Run '+GUIDES[provider]['login']+' in your terminal.')
            separator='--' if Path(terminal).name=='gnome-terminal' else '-e'
            subprocess.Popen([terminal,separator,*login_argv],cwd=self.root,env=environment)
        self.logins[login_key]=time.time()
        return {'opened':True,'provider':provider,'account_id':account_id,'note':'Complete official login in the new terminal, then test the connection.'}

    def update_key(self,cid,api_key,remember=False):
        self.require_owner()
        if not isinstance(api_key,str) or not api_key.strip() or len(api_key)>8192 or any(ord(x)<32 for x in api_key):
            raise ValueError('Provide a valid API key')
        if type(remember) is not bool:raise ValueError('Invalid remember option')
        with self.lock:
            c=self.get(cid)
            if c['provider'] in CLIS:raise ValueError('Use official client login for subscription accounts')
            variable='FOREMAN_API_'+cid.upper()
            if remember:
                try:self.keyring().set_password('AgentForeman',str(self.project)+':'+cid,api_key)
                except ValueError:raise
                except Exception:raise ValueError('OS credential storage failed') from None
            elif c.get('secret_storage')=='os':
                try:self.keyring().delete_password('AgentForeman',str(self.project)+':'+cid)
                except Exception:raise ValueError('Could not remove the previous OS credential; key update cancelled') from None
            os.environ[variable]=api_key;c['key_env']=variable;c['secret_storage']='os' if remember else 'session'
            c['enabled']=False;c['test_generation']=c.get('test_generation',0)+1;c.pop('test',None)
            self.save(c)
            with self.pool.connect() as db:db.execute('UPDATE members SET enabled=0 WHERE id=?',('local_'+cid,))
            return self.public(c)

    def settings(self,c):
        settings={k:v for k,v in c.items() if k in {'provider','model','key_env','base_url','active_hours','timeout_seconds'}}
        if c.get('account_id'):
            from .accounts import Accounts
            settings['account_dir']=str(Accounts(self.project).directory(c['account_id'],c['provider']))
        return settings

    def group(self,c):
        # Same CLI login is one resource, regardless of model/connection count.
        return (c['provider']+('_'+c['account_id'] if c.get('account_id') else '')) if c['provider'] in CLIS else 'api-'+hashlib.sha256(c.get('base_url','anthropic').encode()).hexdigest()[:16]

    def create(self,**body):
        self.require_owner()
        if set(body)-{'label','provider','model','base_url','api_key','key_env','remember','active_hours','chair_preferred','account_id'}:
            raise ValueError('Unknown connection setting')
        provider=body.get('provider')
        if provider not in PROVIDERS:raise ValueError('Choose a supported connection type')
        label=body.get('label') or GUIDES.get(provider,{}).get('label',provider)
        if not isinstance(label,str) or not 1<=len(label.strip())<=100:raise ValueError('Name must contain 1..100 characters')
        model=body.get('model','')
        if not isinstance(model,str) or len(model)>200 or any(ord(x)<32 for x in model):raise ValueError('Invalid model')
        if provider in {'anthropic','openai-compatible'} and not model.strip():
            raise ValueError('Choose the explicit API model advertised by your service')
        for flag in ('remember','chair_preferred'):
            if flag in body and type(body[flag]) is not bool:raise ValueError('Invalid option')
        c={'id':secrets.token_hex(6),'label':label.strip(),'provider':provider,'model':model.strip(),
           'timeout_seconds':600,'enabled':False,'chair_preferred':body.get('chair_preferred',False),'created_at':time.time()}
        if body.get('account_id'):
            from .accounts import Accounts
            Accounts(self.project).directory(body['account_id'],provider)
            c['account_id']=body['account_id']
        if body.get('active_hours'):
            hours(body['active_hours']);c['active_hours']=body['active_hours']
        if provider in {'openai-compatible','anthropic'}:
            url=body.get('base_url','https://api.anthropic.com/v1' if provider=='anthropic' else '')
            if not isinstance(url,str):raise ValueError('API base URL must be a string')
            url=url.rstrip('/');p=urlsplit(url)
            if not p.hostname or p.username or p.password or p.query or p.fragment or (p.scheme!='https' and not (p.scheme=='http' and p.hostname in {'localhost','127.0.0.1','::1'})):
                raise ValueError('Use an HTTPS API base URL; HTTP is allowed only on localhost')
            c['base_url']=url
        key=body.get('api_key','');variable=body.get('key_env','')
        if provider not in CLIS:
            if not isinstance(key,str) or len(key)>8192 or any(ord(x)<32 for x in key):raise ValueError('Invalid API key')
            if not isinstance(variable,str) or (variable and not variable.isidentifier()):raise ValueError('Invalid key environment variable')
            if bool(key)==bool(variable):raise ValueError('Provide an API key or an environment variable name')
            c['key_env']=variable or ('FOREMAN_API_'+c['id'].upper())
            c['secret_storage']='environment' if variable else ('os' if body.get('remember') else 'session')
            if key:
                if body.get('remember'):
                    try:self.keyring().set_password('AgentForeman',str(self.project)+':'+c['id'],key)
                    except ValueError:raise
                    except Exception:raise ValueError('OS credential storage failed; choose session-only.') from None
                os.environ[c['key_env']]=key
        elif key or variable or body.get('remember'):
            raise ValueError('CLI accounts authenticate in the official client; do not paste login tokens')
        with self.lock:self.save(c)
        return self.public(c)

    def public(self,c):
        row={k:v for k,v in c.items() if k not in {'key_env'}}
        if c['provider']=='gemini-cli':row['notice']='旧 Gemini CLI：Google AI Pro / Ultra 请改用 Antigravity；此入口仅保留企业 / 旧 API 兼容。'
        row['member']='local_'+c['id'];row['resource_group']=self.group(c)
        row['key_available']=c['provider'] in CLIS or bool(os.environ.get(c.get('key_env','')))
        row['authentication']='independent_profile' if c.get('account_id') else ('local_client' if c['provider'] in CLIS else 'api_key')
        if c.get('account_id'):
            from .accounts import Accounts
            row['account_label']=Accounts(self.project).get(c['account_id'])['label']
        row['active_now']=available(self.settings(c))
        row['testing']=c['id'] in self.testing
        row['worker_running']=c['id'] in self.threads and self.threads[c['id']].is_alive()
        m=next((m for m in self.pool.members() if m['id']==row['member']),{})
        row['retry_at']=m.get('not_before',0);row['last_seen']=m.get('last_seen')
        return row

    def listing(self):
        with self.lock:return {'connections':[self.public(c) for c in self.records()],
            'clients':detect(),'guides':GUIDES,'service_running':self.server is not None,'error':self.error,
            'lifetime':'Close the browser freely; keep the console process running. Restart restores saved connections.'}

    def test(self,cid):
        self.require_owner();c=self.get(cid)
        with self.lock:
            if cid in self.testing:raise ValueError('Connection test already running')
            self.testing.add(cid)
        def run():
            from .smoke import run_smoke
            try:
                from contextlib import ExitStack
                with ExitStack() as stack:
                    until=time.monotonic()+5
                    while True:
                        try:
                            stack.enter_context(file_lock(self.root/(self.group(c)+'.lock')));break
                        except BlockingIOError:
                            if time.monotonic()>=until or self.stop.wait(.1):raise
                    result=run_smoke(self.project,None,30,settings_override=self.settings(c))
                receipt={k:result[k] for k in ('passed','status','diagnostic')}
            except BlockingIOError:receipt={'passed':False,'status':'busy','diagnostic':'Resource busy; retry after the current task.'}
            except Exception:receipt={'passed':False,'status':'failed','diagnostic':'Connection test failed; check local login and configuration.'}
            with self.lock:
                current=self.get(cid)
                current['last_test_attempt']={**receipt,'tested_at':time.time()}
                if receipt['status']!='busy':current['test']=current['last_test_attempt']
                # Disabling during a test must not be reversed by its completion.
                if current.get('test_generation',0)==generation and not self.stop.is_set():
                    current['enabled']=receipt['passed'] if receipt['status']!='busy' else current['enabled'];self.save(current)
                    with self.pool.connect() as db:db.execute('UPDATE members SET enabled=? WHERE id=?',(int(current['enabled']),'local_'+cid))
                self.testing.discard(cid)
                if current.get('enabled') and current.get('test_generation',0)==generation and not self.stop.is_set():self.start(current)
        with self.lock:
            generation=c.get('test_generation',0)+1;c['test_generation']=generation;self.save(c)
        t=threading.Thread(target=run,daemon=True);self.tests[cid]=t;t.start()
        return {'testing':True,'id':cid}

    def enable(self,cid,enabled):
        self.require_owner()
        if type(enabled) is not bool:raise ValueError('enabled must be boolean')
        with self.lock:
            c=self.get(cid)
            if enabled and not c.get('test',{}).get('passed'):raise ValueError('Test the connection successfully first')
            c['enabled']=enabled;c['test_generation']=c.get('test_generation',0)+1;self.save(c)
            with self.pool.connect() as db:db.execute('UPDATE members SET enabled=? WHERE id=?',(int(enabled),'local_'+cid))
            if enabled:self.start(c)
            return self.public(c)

    def sync_cooldown(self,current):
        shared=['local_'+r['id'] for r in self.records() if self.group(r)==self.group(current)]
        with self.pool.connect() as db:
            marks=','.join('?' for _ in shared)
            until=db.execute(f'SELECT MAX(not_before) FROM members WHERE id IN ({marks})',shared).fetchone()[0] or 0
            db.execute(f'UPDATE members SET not_before=MAX(not_before,?) WHERE id IN ({marks})',[until,*shared])

    def start(self,c):
        if self.stop.is_set():return
        if c['id'] in self.threads and self.threads[c['id']].is_alive():return
        variable='FOREMAN_LOCAL_'+c['id'].upper();os.environ.setdefault(variable,secrets.token_urlsafe(32))
        self.pool.register('local_'+c['id'],os.environ[variable],c['label'])
        with self.pool.connect() as db:db.execute('UPDATE members SET enabled=1 WHERE id=?',('local_'+c['id'],))
        settings={**self.settings(c),'coordinator':f'http://127.0.0.1:{self.server.server_port}',
                  'state_dir':str(self.path(c['id'])/'worker'),'token_env':variable}
        worker=Worker(settings)
        def run():
            # Existing native receipts and identity persist across console restarts.
            try:
                with file_lock(worker.root/'worker.lock'):
                    while not self.stop.is_set():
                        current=self.get(c['id'])
                        if not current['enabled'] or (current['provider'] not in CLIS and not os.environ.get(current.get('key_env',''))):
                            if self.stop.wait(1):return
                            continue
                        worker.settings.update(self.settings(current))
                        try:
                            with file_lock(self.root/(self.group(current)+'.lock')):
                                self.sync_cooldown(current)
                                worker.run_once()
                                self.sync_cooldown(current)
                        except (ValueError,OSError):pass
                        if self.stop.wait(1):return
            except (ValueError,OSError):pass
        t=threading.Thread(target=run,daemon=True);self.threads[c['id']]=t;t.start()

    def candidates(self):
        now=time.time()
        return [self.public(c) for c in self.records() if c.get('enabled') and c.get('test',{}).get('passed')
                and available(self.settings(c)) and self.public(c)['worker_running'] and self.public(c)['key_available'] and self.public(c)['retry_at']<=now]

    def plan(self,chair=None,mode='adversarial'):
        self.require_owner()
        if mode not in {'adversarial','cooperative'}:raise ValueError('Unknown mode')
        rows=self.candidates();minimum=4 if mode=='cooperative' else 3
        if len(rows)<minimum:raise ValueError(f'This mode requires {minimum} tested, enabled connections with distinct role identities. Add connections first.')
        if chair is None:chair=next((c['member'] for c in rows if c['chair_preferred']),rows[0]['member'])
        if chair not in {c['member'] for c in rows}:raise ValueError('Chair connection is unavailable')
        others=[c['member'] for c in rows if c['member']!=chair]
        return {'mode':mode,'chair':chair,'members':others[:2],
                **({'reviewers':others[2:3]} if mode=='cooperative' else {}),
                'max_rounds':3,'budget_seconds':28800,'stage_seconds':600}

    def close(self):
        self.stop.set()
        def finish():
            for t in list(self.tests.values()):t.join()
            for t in list(self.threads.values()):t.join()
            if self.server:self.server.shutdown();self.server.server_close();self.server=None
            if self.owner:self.owner.__exit__(None,None,None);self.owner=None
        # Let in-flight tasks settle while the process is alive. Native receipts
        # protect interrupted subprocesses after a forced process exit.
        if any(t.is_alive() for t in [*self.threads.values(),*self.tests.values()]):threading.Thread(target=finish,daemon=True).start()
        else:finish()
