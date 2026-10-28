"""TEST ONLY scripted replies for cooperation/debate; no live model quality claim."""
import json
from pathlib import Path
import sys

def answer_for(schema,context):
    keys=schema['properties']
    if 'assignments' in keys:
        answer={'summary':'TEST ONLY parallel decomposition','assignments':[
            {'member':m,'goal':'Calculate supplied sum' if i==0 else 'Explain data limitations',
             'deliverables':['answer','report'],'acceptance':'Use supplied sales data'}
            for i,m in enumerate(context['contributors'])],
            'integration_contract':'Combine all contributions, return answer and report.total integer'}
    elif 'artifacts' in keys:
        total=60 if context.get('assignment') or context.get('current') else 50
        answer={'summary':'TEST ONLY concrete data result','artifacts':[
            {'id':'answer','media_type':'text/markdown','content':'TEST ONLY total: '+str(total)+' <img src=x onerror="window.modeInjected=true">'},
            {'id':'report','media_type':'application/json','content':json.dumps({'total':total})}],
            'claims':[],'responses':[{'issue_id':i['id'],'action':'change','explanation':'Recomputed supplied sum'} for i in context.get('open_issues',[])]}
    elif 'verdict' in keys:
        receipts=context['current']['checks'];failed=next((c for c in receipts if not c['passed']),None)
        if failed:
            lines=json.dumps(failed,ensure_ascii=False,indent=2,sort_keys=True).splitlines()
            n=next(i+1 for i,line in enumerate(lines) if '"passed"' in line)
            answer={'verdict':'revise','summary':'Actual frozen check failed','issues':[{
                'severity':'major','problem':'Integrated total fails the independent sum check',
                'anchor':{'kind':'check','ref':failed['check_id'],'quote':'"passed": false','line_start':n,'line_end':n},
                'suggested_change':'Recompute supplied rows and retest'}]}
        else:answer={'verdict':'accept','summary':'Configured sum check passed','issues':[]}
    else:
        answer={'candidate_version':context['selected_candidate']['id'],'summary':'TEST ONLY advisory summary',
                'open_questions':[],'proposed_checks':[]}
    if 'scope_change' in keys:answer['scope_change']={'required':False,'reason':'TEST ONLY within confirmed task scope'}
    return answer


if __name__=='__main__':
    schema=json.loads(Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())
    output=Path(sys.argv[sys.argv.index('--output-last-message')+1])
    context=json.loads(sys.stdin.read().splitlines()[-1])
    answer=answer_for(schema,context)
    output.write_text(json.dumps(answer),encoding='utf-8')
    print(json.dumps({'type':'turn.completed'}))
