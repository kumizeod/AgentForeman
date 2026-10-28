"""TEST ONLY official-client protocol with isolated environment assertions."""
import json
import os
from pathlib import Path
import sys

provider='codex' if '--output-last-message' in sys.argv else 'claude-code'
variable='CODEX_HOME' if provider=='codex' else 'CLAUDE_CONFIG_DIR'
root=Path(os.environ[variable])
for key in (('OPENAI_API_KEY','CODEX_API_KEY') if provider=='codex' else ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','CLAUDE_CODE_OAUTH_TOKEN')):
    assert key not in os.environ,key
(root/'fixture-used-profile').write_text('TEST ONLY')
answer={'answer':root.parent.name}
if provider=='codex':
    Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps(answer))
    print(json.dumps({'type':'turn.completed'}))
else:print(json.dumps({'subtype':'success','structured_output':answer}))
