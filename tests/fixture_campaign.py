"""TEST ONLY: scripted planner/editor; no model call. Training/verifier stay real."""
import json
import os
from pathlib import Path
import sys
import time

root = Path.cwd()
prompt = sys.stdin.read()
output = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
schema = json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
if "action" in schema["properties"]:
    time.sleep(float(os.environ.get("FOREMAN_TEST_PLANNER_DELAY", "0")))
    evidence = json.loads((root / "evidence.json").read_text())
    script = json.loads(os.environ.get("FOREMAN_CAMPAIGN_SCRIPT", '["good", "worse", "stop"]'))
    number = len(evidence["rounds"])
    step = script[min(number, len(script) - 1)]
    action = {"good": "experiment", "worse": "experiment", "bad": "experiment"}.get(step, step)
    answer = {"action": action, "instruction": "fixture:" + step,
              "rationale": "TEST ONLY scripted decision based on completed round count",
              "based_on": [number] if number else []}
else:
    config = json.loads((root / "config.json").read_text())
    config.update(hidden=32, epochs=500, learning_rate=0.3)
    if "fixture:bad" in prompt:
        config["epochs"] = "intentional training failure"
    if "fixture:worse" in prompt:
        config.update(hidden=8, epochs=1, learning_rate=0.0)
    (root / "config.json").write_text(json.dumps(config))
    answer = {"summary": "TEST ONLY edit", "hypothesis": "Scripted fixture", "changes": ["config.json"]}
output.write_text(json.dumps(answer))
