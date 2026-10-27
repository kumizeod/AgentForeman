"""General artifact debate with frozen independent checks and bounded revision."""
import json
from pathlib import Path

from .debate import Debate
from .files import atomic_text


def add_parser(sub):
    p=sub.add_parser('debate',help='Evidence-bound adversarial mode for general complex tasks')
    actions=p.add_subparsers(dest='debate_command',required=True)
    for action in ('start','advance','status','list','report','stop'):
        a=actions.add_parser(action);a.add_argument('--project',type=Path,default=Path.cwd())
        if action not in {'start','list'}:a.add_argument('debate_id')
        if action=='start':
            a.add_argument('--question',required=True);a.add_argument('--members',nargs='+',required=True)
            a.add_argument('--chair',required=True)
            a.add_argument('--evidence',type=Path,help='JSON list of supplied id/title/content records')
            a.add_argument('--checks',type=Path,help='Local JSON list of frozen scoped checks')
            a.add_argument('--max-rounds',type=int,default=3)
            a.add_argument('--budget-seconds',type=float,default=3600)
            a.add_argument('--stage-seconds',type=float,default=600)


def run(args):
    debate=Debate(args.project);action=args.debate_command
    if action=='start':
        kwargs={k:getattr(args,k) for k in ('max_rounds','budget_seconds','stage_seconds')}
        evidence=json.loads(args.evidence.read_text(encoding='utf-8')) if args.evidence else []
        checks=json.loads(args.checks.read_text(encoding='utf-8')) if args.checks else []
        did=debate.create(args.question,args.members,args.chair,evidence=evidence,checks=checks,**kwargs)
        print(json.dumps({'id':did,'mode':'adversarial','note':'Keep pool serve and member workers running; checks are scoped, chair opinions advisory.'}))
    elif action=='list':print(json.dumps(debate.list(),ensure_ascii=False,indent=2))
    elif action=='report':print((debate.path(args.debate_id)/'report.md').read_text(encoding='utf-8'))
    else:
        if action=='stop':atomic_text(debate.path(args.debate_id)/'stop.request','stop\n')
        state=debate.advance(args.debate_id) if action in {'advance','stop'} else debate.get(args.debate_id)
        print(json.dumps(state,ensure_ascii=False,indent=2))
    return 0
