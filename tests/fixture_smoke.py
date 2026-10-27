"""TEST ONLY provider process; never evidence of live model success."""
import json
import os
from pathlib import Path
import sys

mode=os.environ.get('FOREMAN_SMOKE_FIXTURE_MODE','pass')
if mode=='auth':
    print('401 Unauthorized TEST ONLY secret-value-must-not-appear-in-report',file=sys.stderr)
    raise SystemExit(1)
if mode=='quota':
    print("You've hit your usage limit. Try again in 5 hours.",file=sys.stderr)
    raise SystemExit(1)
target=Path(sys.argv[sys.argv.index('--output-last-message')+1])
target.write_text(json.dumps({'answer':'42' if mode=='pass' else '99'}),encoding='utf-8')
print(json.dumps({'type':'turn.completed'}))
