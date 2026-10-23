"""TEST ONLY JSON CLI worker. No live model invocation."""
import json
from pathlib import Path
import sys

schema=json.loads(Path(sys.argv[sys.argv.index('--output-schema')+1]).read_text())
output=Path(sys.argv[sys.argv.index('--output-last-message')+1])
output.write_text(json.dumps({'answer':'TEST ONLY supervised pool fixture'}))
print(json.dumps({'type':'turn.completed'}))
