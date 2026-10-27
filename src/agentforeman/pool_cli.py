"""Local administration and member-owned workers for the shared pool."""
import json
import os
from pathlib import Path
import secrets
import time

from .pool import Pool


def add_parser(sub):
    parser = sub.add_parser('pool',help='Member-owned Codex/API worker pool')
    actions = parser.add_subparsers(dest='pool_command',required=True)
    for action in ('register','serve','worker','status','submit','agents','smoke'):
        p = actions.add_parser(action)
        p.add_argument('--project',type=Path,default=Path.cwd())
        if action=='register':
            p.add_argument('--member',required=True)
            p.add_argument('--token-env',required=True,help='Environment variable containing a unique 32+ character worker token')
            p.add_argument('--description',default='')
        elif action=='serve':
            p.add_argument('--host',default='127.0.0.1')
            p.add_argument('--port',type=int,default=8770)
        elif action=='worker':
            p.add_argument('--config',type=Path,required=True)
            p.add_argument('--once',action='store_true')
        elif action=='smoke':
            p.add_argument('--config',type=Path,required=True)
            p.add_argument('--timeout-seconds',type=float,default=90)
        elif action=='submit':
            p.add_argument('--members',nargs='+',required=True)
            p.add_argument('--prompt-file',type=Path,required=True)
            p.add_argument('--schema-file',type=Path,required=True)
            p.add_argument('--budget-seconds',type=float,default=1800)


def run(args):
    if args.pool_command=='smoke':
        from .smoke import run_smoke
        result=run_smoke(args.project,args.config,args.timeout_seconds)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['passed'] else 2
    if args.pool_command=='worker':
        from .pool_worker import Worker,load_worker
        result=Worker(load_worker(args.config)).run(once=args.once)
        if args.once:
            print(json.dumps(result,ensure_ascii=False,indent=2))
            if result['status'] in {'failed','uncertain'}:return 2
        return 0
    if args.pool_command=='agents':
        from .agents import detect
        print(json.dumps({'agents':detect(),'note':'Executable detection only; no login reads or model calls'},indent=2))
        return 0
    pool = Pool(args.project)
    if args.pool_command=='register':
        token = os.environ.get(args.token_env,'')
        pool.register(args.member,token,args.description)
        print(json.dumps({'member':args.member,'registered':True,'credential_storage':'SHA-256 of worker token only'}))
    elif args.pool_command=='submit':
        request = {'prompt':args.prompt_file.read_text(encoding='utf-8'),'schema':json.loads(args.schema_file.read_text(encoding='utf-8'))}
        tid = pool.enqueue(secrets.token_hex(12),'answer',args.members,request,time.time()+args.budget_seconds)
        print(json.dumps({'task':tid}))
    elif args.pool_command=='status':
        print(json.dumps({'members':pool.members(),'tasks':[{k:v for k,v in t.items() if k not in {'request','result'}} for t in pool.tasks()]},ensure_ascii=False,indent=2))
    else:
        from .pool_server import PoolServer
        server = PoolServer(args.project,args.host,args.port)
        print(f'Worker coordinator: http://{args.host}:{server.server_port}',flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    return 0
