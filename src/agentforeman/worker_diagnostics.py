"""Fixed diagnostic codes without exposing provider error bodies or credentials."""
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
    if any(s in text for s in ('401','unauthorized','authentication failed','invalid api key','token expired')):
        return 'authentication_failed'
    if any(s in text for s in ('403','permission denied','access denied','model_not_found','model not found')):
        return 'permission_or_model_access'
    if timed_out:return 'provider_timeout'
    if any(s in text for s in ('connecterror','connection refused','network is unreachable','failed to connect','dns')):
        return 'network_unavailable'
    return 'provider_execution_or_output_failed'

