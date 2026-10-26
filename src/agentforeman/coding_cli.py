import json
from pathlib import Path

from .coding import CodingRound
from .files import atomic_text


def add_parser(sub):
    parser=sub.add_parser('coding',help='Multiple agents independently implement and verify code candidates')
    actions=parser.add_subparsers(dest='coding_command',required=True)
    for action in ('start','resume','status','list','report','stop'):
        p=actions.add_parser(action);p.add_argument('--project',type=Path,default=Path.cwd())
        if action not in {'start','list'}:p.add_argument('coding_id')
        if action=='start':
            group=p.add_mutually_exclusive_group(required=True)
            group.add_argument('--goal');group.add_argument('--council')
            p.add_argument('--members',nargs='+',required=True)
            p.add_argument('--budget-seconds',type=float,default=3600)
            p.add_argument('--proposal-seconds',type=float,default=600)


def run(args):
    coding=CodingRound(args.project);action=args.coding_command
    if action=='start':
        cid=coding.create(args.goal,args.members,council_id=args.council,budget_seconds=args.budget_seconds,proposal_seconds=args.proposal_seconds)
        print(json.dumps({'id':cid,'note':'Keep pool serve and member workers running; baseline, code proposals and independent evaluation advance automatically.'}))
    elif action=='list':print(json.dumps(coding.list(),ensure_ascii=False,indent=2))
    elif action=='report':print((coding.path(args.coding_id)/'report.md').read_text(encoding='utf-8'))
    else:
        if action=='stop':atomic_text(coding.path(args.coding_id)/'stop.request','stop\n')
        state=coding.advance(args.coding_id) if action in {'resume','stop'} else coding.get(args.coding_id)
        print(json.dumps(state,ensure_ascii=False,indent=2))
    return 0
