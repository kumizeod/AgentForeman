"""TEST ONLY live HTTP worker transport and native checker execution."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import threading
import time

from agentforeman.cooperate import Cooperation
from agentforeman.debate import Debate
from agentforeman.pool_server import PoolServer
from agentforeman.pool_worker import Worker


def run(project,mode,did):
    controller=(Cooperation if mode=='cooperations' else Debate)(project)
    state=controller.get(did)
    members=[*state['members'],*state.get('reviewers',[]),state['chair']]
    server=PoolServer(project,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        workers=[]
        for member in members:
            variable='FOREMAN_MODE_FIXTURE_'+member.replace('-','_');os.environ[variable]=(member+'-')*8
            workers.append(Worker({'coordinator':f'http://127.0.0.1:{server.server_port}',
                'state_dir':str(project/'.foreman'/'mode-fixtures'/member),'token_env':variable,
                'provider':'codex','timeout_seconds':15,
                'command':[str(Path(__file__).with_name('fixture_modes.py').resolve())]}))
        deadline=time.monotonic()+70
        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            while time.monotonic()<deadline:
                for result in executor.map(lambda worker:worker.run_once(),workers):pass
                state=controller.get(did)
                if state['status']!='running':return state
                time.sleep(.1)
        raise RuntimeError('Mode fixture did not settle')
    finally:
        server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    state=run(Path(sys.argv[1]),sys.argv[2],sys.argv[3])
    assert state['status']=='completed_scoped',state
    assert len(state['versions'])==2,state
    assert state['versions'][0]['checks'][0]['passed'] is False
    assert state['versions'][1]['checks'][0]['passed'] is True
    print('TEST ONLY HTTP/native pipeline: '+state['mode']+' passes configured checks after revision')
