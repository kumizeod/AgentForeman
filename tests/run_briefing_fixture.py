"""TEST ONLY actual HTTP/native chairman worker for browser clarification tests."""
import os
from pathlib import Path
import sys
import threading
import time

from agentforeman.briefing import Briefing
from agentforeman.pool_server import PoolServer
from agentforeman.pool_worker import Worker


def run(project,bid):
    c=Briefing(project);member=c.get(bid)['execution']['chair']
    variable='FOREMAN_BRIEF_FIXTURE';os.environ[variable]=(member+'-')*8
    server=PoolServer(project,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        worker=Worker({'coordinator':f'http://127.0.0.1:{server.server_port}',
                       'state_dir':str(project/'.foreman/briefing-fixture-worker'),
                       'token_env':variable,'provider':'codex','timeout_seconds':15,
                       'command':[str(Path(__file__).with_name('fixture_briefing.py').resolve())]})
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            worker.run_once();state=c.get(bid)
            if state['status']!='running':return state
            time.sleep(.1)
        raise RuntimeError('Briefing fixture did not settle')
    finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    state=run(Path(sys.argv[1]),sys.argv[2]);print(state['status'])
