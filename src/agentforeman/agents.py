"""Coding CLI protocol adapters; member-owned authentication, normalized JSON output."""
from __future__ import annotations

import json
from pathlib import Path

from .codex import resolve_command
from .files import atomic_json, atomic_text
from .pool import validate

CLIS = {'codex':('@openai/codex','codex'),
        'claude-code':('@anthropic-ai/claude-code','claude'),
        'gemini-cli':('@google/gemini-cli','gemini'),
        'opencode':('opencode-ai','opencode')}


def command(settings):
    package,binary=CLIS[settings['provider']]
    return resolve_command({**settings,'executable':binary},npm_package=package,bin_name=binary)


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
    elif provider=='opencode':
        argv=[*prefix,'run','--format','json','--agent','foreman']
    else:
        raise ValueError('Use native codex.json_spec for Codex')
    if settings.get('model'):
        argv+=['--model',settings['model']]
    env={'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
    if provider=='opencode':
        env['OPENCODE_CONFIG_CONTENT']=json.dumps({'permission':{'*':'deny'},
            'agent':{'foreman':{'mode':'primary','description':'Bounded structured response without tools',
                                'permission':{'*':'deny'}}}})
    return {'argv':argv,'cwd':str(context),'stdin':str(folder/'prompt.txt'),
            'timeout_seconds':settings['timeout_seconds'],
            'env':env}


def read_answer(job: Path, folder: Path, request: dict, provider: str):
    with (job/'stdout.log').open('rb') as stream:
        raw=stream.read(1000001)
    if len(raw)>1000000:
        raise ValueError('Agent output exceeded bound')
    if provider=='opencode':
        events=[json.loads(line) for line in raw.splitlines() if line.strip()]
        if any(not isinstance(e,dict) or not isinstance(e.get('part',{}),dict) for e in events):
            raise ValueError('Invalid OpenCode event')
        if any(e.get('type') in {'error','tool_use'} or e.get('part',{}).get('type')=='tool' for e in events):
            raise ValueError('OpenCode failed or attempted a tool call')
        if not any(e.get('type')=='step_finish' and e.get('part',{}).get('reason')=='stop' for e in events):
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
        return {'retry_at':max(time.time()+60,reset or time.time()+1800)}
    return None
