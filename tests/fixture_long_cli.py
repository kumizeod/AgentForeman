"""TEST ONLY long CLI plus a SIGTERM-ignoring descendant; no model call."""
import os
from pathlib import Path
import subprocess
import sys
import time
from agentforeman.files import atomic_json,read_json
from agentforeman.platforms import identity

marker=Path(sys.argv[1]);child_marker=marker.with_suffix('.child.json')
code=('import os,signal,sys,time; from pathlib import Path; '
      'from agentforeman.files import atomic_json; from agentforeman.platforms import identity; '
      'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
      'atomic_json(Path(sys.argv[1]),identity(os.getpid())); time.sleep(90)')
child=subprocess.Popen([sys.executable,'-c',code,str(child_marker)])
end=time.monotonic()+10
while not child_marker.exists() and time.monotonic()<end:time.sleep(.02)
atomic_json(marker,{'parent':identity(os.getpid()),'child':read_json(child_marker)})
time.sleep(90)
