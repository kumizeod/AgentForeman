"""Coding CLI protocol adapters; member-owned authentication, normalized JSON output."""
from __future__ import annotations

import json
from pathlib import Path

from .codex import resolve_command
from .files import atomic_json, atomic_text
from .pool import validate

CLIS = {'codex':('@openai/codex','codex'),
        'claude-code':('@anthropic-ai/claude-code','claude'),
        'gemini-cli':('@google/gemini-cli','gemini')}


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
    else:
        raise ValueError('Use native codex.json_spec for Codex')
    if settings.get('model'):
        argv+=['--model',settings['model']]
    return {'argv':argv,'cwd':str(context),'stdin':str(folder/'prompt.txt'),
            'timeout_seconds':settings['timeout_seconds'],
            'env':{'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}}


def read_answer(job: Path, folder: Path, request: dict, provider: str):
    with (job/'stdout.log').open('rb') as stream:
        raw=stream.read(1000001)
    if len(raw)>1000000:
        raise ValueError('Agent output exceeded bound')
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
