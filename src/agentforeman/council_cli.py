"""Council submission and inspectable durable reports."""
import json
from pathlib import Path

from .council import Council
from .files import atomic_text


def add_parser(sub):
    p=sub.add_parser('council',help='Independent exploration, anonymous peer review, Claude chair and audit')
    actions=p.add_subparsers(dest='council_command',required=True)
    for action in ('start','status','list','advance','report','stop'):
        a=actions.add_parser(action)
        a.add_argument('--project',type=Path,default=Path.cwd())
        if action not in {'start','list'}:a.add_argument('council_id')
        if action=='start':
            a.add_argument('--question',required=True)
            a.add_argument('--members',nargs='+',required=True)
            a.add_argument('--chair',required=True)
            a.add_argument('--sources',type=Path,help='JSON list of id/title/url/excerpt source records')
            a.add_argument('--budget-seconds',type=float,default=28800)
            a.add_argument('--stage-seconds',type=float,default=1800)
            a.add_argument('--min-answers',type=int,default=2)


def run(args):
    council=Council(args.project)
    action=args.council_command
    if action=='start':
        sources=json.loads(args.sources.read_text(encoding='utf-8')) if args.sources else []
        cid=council.create(args.question,args.members,args.chair,sources=sources,budget_seconds=args.budget_seconds,
                           stage_seconds=args.stage_seconds,min_answers=args.min_answers)
        print(json.dumps({'id':cid,'status':'running','report':str(council.path(cid)/'report.md'),
                          'note':'Run pool serve plus member and chair workers; sources are supplied evidence, not automatically verified literature.'},ensure_ascii=False,indent=2))
    elif action=='list':
        print(json.dumps(council.list(),ensure_ascii=False,indent=2))
    elif action=='report':
        print((council.path(args.council_id)/'report.md').read_text(encoding='utf-8'))
    elif action=='stop':
        atomic_text(council.path(args.council_id)/'stop.request','stop\n')
        print(json.dumps(council.advance(args.council_id),ensure_ascii=False,indent=2))
    else:
        state=council.advance(args.council_id) if action=='advance' else council.get(args.council_id)
        print(json.dumps(state,ensure_ascii=False,indent=2))
    return 0
