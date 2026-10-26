"""TEST ONLY Codex + Claude CLI protocol fixtures over actual HTTP and training."""
import os
from pathlib import Path
import sys
import threading
import time

from agentforeman.coding import CodingRound
from agentforeman.pool_server import PoolServer
from agentforeman.pool_worker import Worker


def run(project,cid):
    server=PoolServer(project,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    workers=[]
    try:
        for member,provider,fixture in [('member-a','codex','fixture_pool.py'),('member-b','claude-code','fixture_agent_cli.py')]:
            variable='FOREMAN_FIXTURE_'+member.replace('-','_');os.environ[variable]=(member+'-')*8
            workers.append(Worker({'coordinator':f'http://127.0.0.1:{server.server_port}',
                'state_dir':str(project/'.foreman'/'coding-fixture-workers'/member),'token_env':variable,
                'provider':provider,'timeout_seconds':15,'command':[str(Path(__file__).with_name(fixture).resolve())]}))
        deadline=time.time()+60;coding=CodingRound(project)
        while time.time()<deadline:
            for worker in workers:worker.run_once()
            state=coding.get(cid)
            if state['status']!='running':return state
            time.sleep(.1)
        raise RuntimeError('Coding fixture did not settle')
    finally:
        server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    result=run(Path(sys.argv[1]),sys.argv[2])
    assert result['status']=='completed',result.get('stop_reason')
    assert len(result['candidates'])==2
    assert all(x['status']=='verified' for x in result['candidates'])
    assert result['best']['metric']>result['baseline']['metric']
    print('TEST ONLY Codex + Claude code proposals independently trained and verified')
