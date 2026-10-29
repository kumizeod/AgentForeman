"""Fixed diagnostic codes without exposing provider error bodies or credentials."""
import json

from .files import read_json


def diagnose(root):
    """Return fixed diagnostic codes; never expose provider bodies or credentials."""
    texts=[];timed_out=False
    for path in root.glob('**/job/result.json'):
        try:timed_out |= bool(read_json(path).get('timed_out'))
        except (OSError,ValueError):pass
    for path in root.glob('**/job/*.log'):
        try:
            with path.open('rb') as stream:
                stream.seek(max(0,path.stat().st_size-65536))
                texts.append(stream.read(65536).decode('utf-8',errors='replace').lower())
        except OSError:pass
    text='\n'.join(texts)
    # Bare 401/403 substrings also occur in timestamps, IDs and model answers.
    # Only explicit errors/status fields may classify authentication failures.
    statuses=[]
    for path in root.glob('**/api-status.json'):
        try:statuses.append(read_json(path).get('code'))
        except (OSError,ValueError,AttributeError):pass
    for line in text.splitlines():
        try:event=json.loads(line)
        except ValueError:continue
        if not isinstance(event,dict):continue
        error=event.get('error')
        if isinstance(error,dict):statuses.append(error.get('status',error.get('code')))
    if 401 in statuses or any(s in text for s in ('unauthorized','authentication failed','invalid api key','token expired','provider.auth')):

        return 'authentication_failed'
    if 403 in statuses or any(s in text for s in ('permission denied','access denied','model_not_found','model not found','provider.no-route','model unavailable')):
        return 'permission_or_model_access'
    if timed_out:return 'provider_timeout'
    if any(s in text for s in ('connecterror','connection refused','network is unreachable','failed to connect','dns')):
        return 'network_unavailable'
    return 'provider_execution_or_output_failed'

