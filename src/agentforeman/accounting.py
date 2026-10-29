"""Allowlisted usage receipts. No credential/session directories or billing guesses."""
from __future__ import annotations

import json
from pathlib import Path

PROVIDERS = {'codex', 'claude-code', 'opencode', 'gemini-cli', 'antigravity', 'anthropic', 'openai-compatible'}


def count(value):
    if type(value) is not int or not 0 <= value <= 10**15:
        raise ValueError('Invalid token counter')
    return value


def normalized(provider, value):
    if provider not in PROVIDERS or not isinstance(value, dict):
        raise ValueError('Invalid usage provider')
    if provider == 'openai-compatible':
        return count(value['prompt_tokens']), count(value['completion_tokens'])
    if provider in {'anthropic', 'claude-code'}:
        # Anthropic's cache read/write fields are disjoint from input_tokens.
        return (count(value['input_tokens']) + count(value.get('cache_read_input_tokens', 0))
                + count(value.get('cache_creation_input_tokens', 0)), count(value['output_tokens']))
    if provider == 'opencode':
        cache = value.get('cache', {})
        # OpenCode input excludes cache; output already includes reasoning.
        return (count(value['input']) + count(cache.get('read', 0)) + count(cache.get('write', 0)),
                count(value['output']))
    return count(value['input_tokens']), count(value['output_tokens'])


def receipt(folder: Path, provider: str, model: str = '') -> dict:
    """Known completed usage survives invalid answers and failed/cancelled jobs."""
    result = {'provider': provider, 'model': model or '', 'input_tokens': None,
              'output_tokens': None, 'complete': False, 'source': 'client_reported'}
    try:
        if provider == 'codex':
            from .observe import job_usage
            usage = job_usage(folder / 'job/stdout.log')
            if usage['reported_turns']:
                result.update(input_tokens=usage['input_tokens'], output_tokens=usage['output_tokens'], complete=usage['complete'])
            return result
        source = folder / ('api-usage.json' if provider in {'anthropic', 'openai-compatible'} else 'job/stdout.log')
        with source.open('rb') as stream:
            raw = stream.read(1000001)
        if len(raw) > 1000000:
            return result
        if provider in {'claude-code', 'anthropic', 'openai-compatible'}:
            data = json.loads(raw)
            values = [data.get('usage', {})]
        elif provider == 'opencode':
            events = [json.loads(line) for line in raw.splitlines() if line.strip()]
            parts = [e['part'] for e in events if e.get('type') == 'step_finish']
            # Duplicate step IDs cannot inflate a retry/replayed stream.
            seen = set(); values = []
            for part in parts:
                key = part.get('id')
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                values.append(part['tokens'])
        else:
            # Unknown formats stay unknown rather than infer from response size.
            return result
        if not values:
            return result
        pairs = [normalized(provider, value) for value in values]
        result.update(input_tokens=sum(v[0] for v in pairs), output_tokens=sum(v[1] for v in pairs), complete=True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return result


def validate_usage(value):
    if not isinstance(value, dict) or set(value) != {'provider', 'model', 'input_tokens', 'output_tokens', 'complete', 'source'}:
        raise ValueError('Invalid usage receipt')
    if value['provider'] not in PROVIDERS or value['source'] != 'client_reported':
        raise ValueError('Unknown usage source')
    if not isinstance(value['model'], str) or len(value['model']) > 200 or type(value['complete']) is not bool:
        raise ValueError('Invalid usage metadata')
    if value['input_tokens'] is None or value['output_tokens'] is None:
        if value['input_tokens'] is not None or value['output_tokens'] is not None or value['complete']:
            raise ValueError('Incomplete usage must remain unknown')
    else:
        count(value['input_tokens']); count(value['output_tokens'])
    return value
