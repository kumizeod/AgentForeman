"""TEST ONLY Claude/Gemini envelopes; not a live model."""
import json
import sys

prompt=sys.stdin.read()
answer={'answer':'TEST ONLY multi CLI result'}
if '--json-schema' in sys.argv:
    print(json.dumps({'type':'result','subtype':'success','is_error':False,'structured_output':answer}))
else:
    print(json.dumps({'response':json.dumps(answer),'stats':{}}))
