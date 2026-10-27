"""Explicit human clarification and version-bound confirmation CLI."""
import json
from pathlib import Path

from .briefing import Briefing


def add_parser(sub):
    p=sub.add_parser('briefing',help='Clarify requirements with a chairman before execution')
    actions=p.add_subparsers(dest='briefing_action',required=True)
    for action in ('start','list','status','advance','message','edit','confirm','stop'):
        a=actions.add_parser(action);a.add_argument('--project',type=Path,default=Path.cwd())
        if action not in {'start','list'}:a.add_argument('briefing_id')
        if action=='start':
            a.add_argument('--idea',required=True);a.add_argument('--execution',type=Path,required=True)
            a.add_argument('--evidence',type=Path);a.add_argument('--checks',type=Path)
            a.add_argument('--turn-seconds',type=float,default=600);a.add_argument('--max-turns',type=int,default=8)
        if action in {'message','edit','confirm'}:a.add_argument('--version',type=int,required=True)
        if action=='message':a.add_argument('--text',required=True)
        if action=='edit':a.add_argument('--contract',type=Path,required=True)
        if action=='confirm':a.add_argument('--sha256',required=True)


def run(args):
    c=Briefing(args.project);action=args.briefing_action
    if action=='start':
        result={'id':c.create(args.idea,json.loads(args.execution.read_text(encoding='utf-8')),
                            evidence=json.loads(args.evidence.read_text(encoding='utf-8')) if args.evidence else [],
                            checks=json.loads(args.checks.read_text(encoding='utf-8')) if args.checks else [],
                            turn_seconds=args.turn_seconds,max_turns=args.max_turns)}
    elif action=='list':result=c.list()
    elif action=='status':result=c.get(args.briefing_id)
    elif action=='advance':result=c.advance(args.briefing_id)
    elif action=='stop':result=c.stop(args.briefing_id)
    elif action=='message':result=c.message(args.briefing_id,args.version,args.text)
    elif action=='edit':result=c.edit(args.briefing_id,args.version,json.loads(args.contract.read_text(encoding='utf-8')))
    else:result=c.confirm(args.briefing_id,args.version,args.sha256)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0
