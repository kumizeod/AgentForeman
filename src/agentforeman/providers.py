"""Bounded API subprocess adapter. Credentials stay in local environment variables."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

from .files import atomic_json, read_json
from .pool import validate


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an API credential through a redirect.


def retry_time(value):
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            return time.time()+max(60, seconds)
    except (ValueError, TypeError):
        pass
    try:
        return max(time.time()+60, parsedate_to_datetime(value).timestamp())
    except (ValueError, TypeError, OverflowError):
        return time.time()+1800


def request_json(settings, request):
    provider = settings['provider']
    key = os.environ.get(settings.get('key_env', 'ANTHROPIC_API_KEY' if provider=='anthropic' else 'OPENAI_API_KEY'))
    if not key:
        raise ValueError('Configured API key environment variable is empty')
    model = settings.get('model')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('An explicit API model is required')
    prompt = request['prompt']+'\nReturn only valid JSON conforming to this schema:\n'+json.dumps(request['schema'])
    headers = {'Content-Type':'application/json'}
    if provider == 'anthropic':
        url = 'https://api.anthropic.com/v1/messages'
        headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'})
        body = {'model':model,'max_tokens':settings.get('max_output_tokens',4096),
                'messages':[{'role':'user','content':prompt}]}
    elif provider == 'openai-compatible':
        url = settings.get('base_url', 'https://api.openai.com/v1').rstrip('/')+'/chat/completions'
        if not url.startswith('https://'):
            raise ValueError('API endpoint requires HTTPS')
        headers['Authorization'] = 'Bearer '+key
        body = {'model':model,'max_tokens':settings.get('max_output_tokens',4096),
                'messages':[{'role':'user','content':prompt}], 'response_format':{'type':'json_object'}}
    else:
        raise ValueError('Unknown API provider')
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method='POST')
    with urllib.request.build_opener(NoRedirect).open(req, timeout=settings['timeout_seconds']) as response:
        raw = response.read(2000001)
        if len(raw)>2000000:
            raise ValueError('API response too large')
        data = json.loads(raw)
    if provider == 'anthropic':
        if data.get('stop_reason') != 'end_turn':
            raise ValueError('API response was incomplete or refused')
        content = ''.join(x['text'] for x in data['content'] if x.get('type')=='text')
    else:
        choice = data['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('API response was incomplete or refused')
        content = choice['message']['content']
    answer = validate(json.loads(content), request['schema'])
    return {'answer':answer,'usage':data.get('usage',{})}


def main():
    settings, request = read_json(Path(sys.argv[1])), read_json(Path(sys.argv[2]))
    output = Path(sys.argv[3])
    try:
        value = request_json(settings, request)
        atomic_json(output, value['answer'])
        # Usage is diagnostic, not a money-budget estimate.
        usage = {k:v for k,v in value['usage'].items() if k in {'input_tokens','output_tokens','prompt_tokens','completion_tokens'} and type(v) is int and v>=0}
        atomic_json(output.with_name('api-status.json'), {'outcome':'success','usage':usage})
        return 0
    except urllib.error.HTTPError as exc:
        info = {'outcome':'quota','retry_at':retry_time(exc.headers.get('Retry-After'))} if exc.code==429 else {'outcome':'failed','code':exc.code}
        atomic_json(output.with_name('api-status.json'),info)
    except Exception:
        # Raw provider errors may contain credentials or request content.
        atomic_json(output.with_name('api-status.json'),{'outcome':'failed'})
    return 1


if __name__=='__main__':
    raise SystemExit(main())
