"""TEST ONLY network + native supervision integration used by browser tests."""
import os
from pathlib import Path
import sys
import threading
import time

from agentforeman.council import Council
from agentforeman.pool_server import PoolServer
from agentforeman.pool_worker import Worker


def run(project,cid):
    server=PoolServer(project,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    workers=[]
    try:
        for member in ('member-a','member-b','claude-chair'):
            variable='FOREMAN_FIXTURE_'+member.replace('-','_')
            os.environ[variable]=(member+'-')*8
            workers.append(Worker({'coordinator':f'http://127.0.0.1:{server.server_port}',
                'state_dir':str(project/'.foreman'/'fixture-workers'/member),'token_env':variable,
                'provider':'codex','timeout_seconds':15,'command':[str(Path(__file__).with_name('fixture_pool.py').resolve())]}))
        deadline=time.time()+40
        council=Council(project)
        while time.time()<deadline:
            for worker in workers:worker.run_once()
            state=council.get(cid)
            if state['status']!='running':return state
            time.sleep(.1)
        raise RuntimeError('Fixture council did not settle')
    finally:
        server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    result=run(Path(sys.argv[1]),sys.argv[2])
    print(result['status'])
