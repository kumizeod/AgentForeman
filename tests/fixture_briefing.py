"""TEST ONLY chairman clarification replies, not live model evidence."""
import json
from pathlib import Path
import sys

schema=json.loads(Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())
context=json.loads(sys.stdin.read().splitlines()[-1])
ready=len(context['messages'])>1
check_ids=[c['id'] for c in context['checks']][:1]
contract={'goal':'Calculate the supplied sales total','inputs':['Supplied sales rows'],
          'deliverables':[{'id':'answer','description':'Explain the sales total'},
                          {'id':'report','description':'JSON with integer total'}],
          'acceptance':[{'criterion':'Sum agrees with every supplied row',
                         'verification':'automated' if check_ids else 'manual','check_ids':check_ids}],
          'constraints':['Do not acquire external data'],'priorities':['Correctness before speed'],
          'exclusions':['No external purchases or deployments'],
          'autonomy':{'allowed_decisions':['Choose task distribution and wording'],
                      'pause_conditions':['A required change of goal or acceptance'],
                      'defaults':['Report limitations of supplied input']}}
answer={'reply':'TEST ONLY chairman <img src=x onerror="window.briefInjected=true"> '+('Ready for human confirmation' if ready else 'Should JSON be included?'),
        'questions':[] if ready else ['Should the final answer include report.total JSON?'],
        'contract':contract,'open_questions':[] if ready else ['Output format'],'ready':ready}
Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps(answer),encoding='utf-8')
print(json.dumps({'type':'turn.completed'}))
