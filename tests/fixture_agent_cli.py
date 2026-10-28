"""TEST ONLY Claude/Gemini envelopes; not a live model."""
import json
import sys

prompt=sys.stdin.read()
answer={'answer':'TEST ONLY multi CLI result'}
for line in prompt.splitlines():
    try:context=json.loads(line)
    except ValueError:continue
    if isinstance(context,dict) and 'allowed_edit' in context and 'base_sha256' in context:
        config=json.loads(context['files']['config.json']);config.update(hidden=32,epochs=500,learning_rate=.3)
        answer={'summary':'TEST ONLY Claude code candidate','hypothesis':'TEST ONLY model config',
                'base_sha256':context['base_sha256'],'files':[{'path':'config.json','content':json.dumps(config)}]}
        break
if '--input-format' in sys.argv:
    message=json.loads(prompt);assert message['event']=='user'
    print(json.dumps({'event':'result','result':{'status':'SUCCESS','structured_output':answer,'response':json.dumps(answer)}}))
elif '--format' in sys.argv:
    print(json.dumps({'type':'text','part':{'type':'text','text':json.dumps(answer)}}))
    print(json.dumps({'type':'step_finish','part':{'type':'step-finish','reason':'stop'}}))
elif '--json-schema' in sys.argv:
    print(json.dumps({'type':'result','subtype':'success','is_error':False,'structured_output':answer}))
else:
    print(json.dumps({'response':json.dumps(answer),'stats':{}}))
