"""Coding CLI protocol adapters; member-owned authentication, normalized JSON output."""
from __future__ import annotations

import json
import functools
import re
import subprocess
from pathlib import Path

from .codex import resolve_command
from .files import atomic_json, atomic_text
from .pool import validate

CLIS = {'codex':('@openai/codex','codex'),
        'claude-code':('@anthropic-ai/claude-code','claude'),
        'gemini-cli':('@google/gemini-cli','gemini'),
        'antigravity':(None,'agy'),
        'opencode':('opencode-ai','opencode')}


def command(settings):
    package,binary=CLIS[settings['provider']]
    return resolve_command({**settings,'executable':binary},npm_package=package,bin_name=binary)


@functools.lru_cache(maxsize=32)
def _opencode_version(prefix, stamp):
    try:
        result = subprocess.run([*prefix, '--version'], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=5, check=False)
        match = re.fullmatch(rb'(?:opencode\s+)?v?(\d+)\.\d+\.\d+(?:[-+][^\s]+)?', result.stdout.strip())
        if result.returncode == 0 and match and int(match[1]) in {1, 2}:
            return int(match[1])
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise ValueError('Cannot identify OpenCode version. Run opencode --version and select the actual CLI executable.')


def opencode_version(prefix):
    stamps = tuple((str(p), p.stat().st_mtime_ns) for v in prefix for p in [Path(v)] if p.is_file())
    return _opencode_version(tuple(prefix), stamps)


def detect():
    rows=[]
    for provider in CLIS:
        try:
            command({'provider':provider})
            present=True
        except (ValueError,OSError):
            present=False
        rows.append({'provider':provider,'installed':present,'login_checked':False})
    return rows


def make_spec(folder: Path, context: Path, request: dict, settings: dict):
    prefix=command(settings)
    provider=settings['provider']
    folder.mkdir(parents=True,exist_ok=True);context.mkdir(parents=True,exist_ok=True)
    prompt=request['prompt']+'\nReturn ONLY a JSON object conforming to this schema:\n'+json.dumps(request['schema'])
    atomic_text(folder/'prompt.txt',prompt)
    atomic_json(folder/'schema.json',request['schema'])
    if provider=='claude-code':
        argv=[*prefix,'--print','--output-format','json','--json-schema',json.dumps(request['schema']),
              '--tools','','--disallowedTools','mcp__*','--strict-mcp-config',
              '--mcp-config','{"mcpServers":{}}','--no-session-persistence']
    elif provider=='gemini-cli':
        argv=[*prefix,'--prompt','Return only the requested JSON object from the piped task.',
              '--output-format','json','--approval-mode','plan']
    elif provider=='antigravity':
        # Official stream-json input avoids Windows command-line prompt limits.
        atomic_text(folder/'prompt.txt',json.dumps({'event':'user','message':{'content':prompt}})+'\n')
        argv=[*prefix,'--input-format','stream-json','--output-format','stream-json',
              '--json-schema',str(folder/'schema.json'),'--mode','plan',
              '--disable-slash-commands','--sandbox',
              '--print-timeout',str(int(settings['timeout_seconds']))+'s']
    elif provider=='opencode':
        major=opencode_version(prefix)
        argv=[*prefix,'run',*(['--standalone','--title','AgentForeman task'] if major==2 else []),'--format','json','--agent','foreman']
        atomic_json(folder/'opencode-protocol.json',{'major':major})
    else:
        raise ValueError('Use native codex.json_spec for Codex')
    if settings.get('model'):
        argv+=['--model',settings['model']]
    env={'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
    if provider=='opencode':
        # OpenCode 2 selects its project from PWD before process.cwd(). Keep the
        # child-only logical directory aligned with the supervised cwd.
        env['PWD']=str(context)
        if major == 2:
            rules=[{'action':'*','resource':'*','effect':'deny'}]
            config={'permissions':rules,'policies':[{'action':'tool.use','resource':'*','effect':'deny'}],
                    'warming':False,'snapshots':False,
                    'agents':{'foreman':{'mode':'primary','description':'Bounded structured response without tools',
                                          'permissions':rules,'steps':1}}}
        else:
            config={'permission':{'*':'deny'},'agent':{'foreman':{'mode':'primary',
                    'description':'Bounded structured response without tools','permission':{'*':'deny'}}}}
        env['OPENCODE_CONFIG_CONTENT']=json.dumps(config)
        if major == 2:
            # V2 documents project configuration rather than the legacy inline
            # environment override. Own context only; never overwrite user config.
            atomic_json(context/'.opencode/opencode.json',config)
    from .accounts import apply_profile
    return apply_profile({'argv':argv,'cwd':str(context),'stdin':str(folder/'prompt.txt'),
            'timeout_seconds':settings['timeout_seconds'],
            'env':env},settings)


def read_answer(job: Path, folder: Path, request: dict, provider: str):
    with (job/'stdout.log').open('rb') as stream:
        raw=stream.read(1000001)
    if len(raw)>1000000:
        raise ValueError('Agent output exceeded bound')
    if provider=='antigravity':
        events=[json.loads(line) for line in raw.splitlines() if line.strip()]
        if any(not isinstance(e,dict) for e in events):raise ValueError('Invalid Antigravity stream')
        if any(e.get('event')=='step_update' and not isinstance(e.get('step_update',{}),dict) for e in events):
            raise ValueError('Invalid Antigravity step')
        # General council workers must return supplied-context artifacts only.
        if any(e.get('event')=='step_update' and (e.get('step_update',{}).get('tool_info') or e.get('step_update',{}).get('subagent_info')) for e in events):
            raise ValueError('Antigravity attempted tools or background agents')
        results=[e['result'] for e in events if e.get('event')=='result']
        if len(results)!=1 or not isinstance(results[0],dict) or results[0].get('status')!='SUCCESS' or results[0].get('error'):
            raise ValueError('Antigravity did not finish one successful turn')
        result=results[0];answer=result.get('structured_output')
        if answer is None:answer=json.loads(result['response'])
        answer=validate(answer,request['schema']);atomic_json(folder/'answer.json',answer);return answer
    if provider=='opencode':
        events=[json.loads(line) for line in raw.splitlines() if line.strip()]
        if any(not isinstance(e,dict) or not isinstance(e.get('part',{}),dict) for e in events):
            raise ValueError('Invalid OpenCode event')
        if any(e.get('type') in {'error','tool_use'} or e.get('part',{}).get('type')=='tool' for e in events):
            raise ValueError('OpenCode failed or attempted a tool call')
        from .files import read_json
        major=read_json(folder/'opencode-protocol.json').get('major') if (folder/'opencode-protocol.json').exists() else 1
        if major == 2:
            # OpenCode 2.2.1 emits text/tool/error records but no step_finish.
            # Only the native supervisor's successful receipt can close the turn.
            receipt=read_json(job/'result.json')
            if receipt.get('exit_code') != 0 or any(receipt.get(k) for k in ('error','cancelled','timed_out')):
                raise ValueError('OpenCode 2 has no successful process receipt')
        elif not any(e.get('type')=='step_finish' and e.get('part',{}).get('reason')=='stop' for e in events):
            raise ValueError('OpenCode did not finish its response')
        content=''.join(e.get('part',{}).get('text','') for e in events if e.get('type')=='text')
        answer=validate(json.loads(content),request['schema'])
        atomic_json(folder/'answer.json',answer)
        return answer
    envelope=json.loads(raw)
    if not isinstance(envelope,dict) or envelope.get('is_error') or envelope.get('error'):
        raise ValueError('Agent reported an error')
    if provider=='claude-code':
        if envelope.get('subtype','success')!='success':
            raise ValueError('Claude Code did not finish successfully')
        answer=envelope.get('structured_output')
        if answer is None:
            answer=json.loads(envelope['result'])
    elif provider=='gemini-cli':
        answer=json.loads(envelope['response'])
    else:
        raise ValueError('Unknown CLI response protocol')
    answer=validate(answer,request['schema'])
    atomic_json(folder/'answer.json',answer)
    return answer


def structured_quota(job,receipt):
    """Only explicit provider error fields; never classify ordinary answer text."""
    if any(receipt.get(k) for k in ('timed_out','cancelled','error')):return None
    from .quota import tail,timestamp
    import time
    raw=tail(job/'stdout.log',1000000)
    try:events=[json.loads(raw)]
    except ValueError:
        try:events=[json.loads(line) for line in raw.splitlines() if line.strip()]
        except ValueError:return None
    for event in events:
        if not isinstance(event,dict):continue
        error=event.get('error')
        if not isinstance(error,dict):continue
        data=error.get('data',{})
        if not isinstance(data,dict):data={}
        code=error.get('code',error.get('type',error.get('name')))
        if (not isinstance(code,(str,int)) or code not in {429,'429','rate_limit_error','quota_exceeded','RESOURCE_EXHAUSTED'}) and error.get('status')!=429 and data.get('statusCode')!=429:
            continue
        reset=timestamp(error.get('resets_at',error.get('reset_at')))
        milliseconds=error.get('retryAfterMs')
        if type(milliseconds) in {int,float} and 0<=milliseconds<=7*86400*1000:
            reset=time.time()+milliseconds/1000
        return {'retry_at':max(time.time()+60,reset or time.time()+1800)}
    return None
