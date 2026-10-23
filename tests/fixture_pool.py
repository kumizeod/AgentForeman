"""TEST ONLY council JSON CLI. No live model calls or measured quality claims."""
import json
from pathlib import Path
import sys

schema=json.loads(Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())
output=Path(sys.argv[sys.argv.index('--output-last-message')+1])
prompt=sys.stdin.read()
keys=schema['properties']
if set(keys)=={'answer'}:
    answer={'answer':'TEST ONLY supervised pool fixture'}
else:
    context=json.loads(prompt.splitlines()[-1])
    sources=[s['id'] for s in context.get('sources',[])][:1]
    if 'finding_responses' in keys:
        answer={'answer':'TEST ONLY chair recommends a matched-budget experiment; no measured improvement is claimed',
                'claims':[{'statement':'TEST hypothesis','source_ids':sources,'supporting_answers':[next(iter(context['answers']))]}],
                'finding_responses':[{'finding_id':f['id'],'resolution':'addressed','explanation':'TEST plan adds matched-budget controls'} for f in context['required_findings']],
                'unresolved':[],'experiments':['Run baseline and candidate with equal training steps'],'confidence':'medium'}
    elif 'claims' in keys:
        answer={'answer':'TEST ONLY independent exploration','claims':[{'statement':'TEST hypothesis','source_ids':sources,'verification':'Not run'}],
                'risks':['Training-budget confound'],'experiments':['Run matched-budget baseline']}
    elif 'evaluations' in keys:
        labels=list(context['answers'])
        answer={'evaluations':[{'label':label,'correctness':3,'novelty':2,'impact':3,'feasibility':4,
                'findings':[{'severity':'major','evidence':'No controlled measurement','suggested_test':'Run matched training budgets'}]} for label in labels],
                'ranking':labels}
    else:
        answer={'verdict':'accept','reasons':['TEST ONLY plan review; real evidence remains pending'],'unsupported_claims':[],'missing_tests':[]}
output.write_text(json.dumps(answer))
print(json.dumps({'type':'turn.completed'}))
