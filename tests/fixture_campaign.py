"""TEST ONLY: scripted planner/editor; no model call. Training/verifier stay real."""
import json
import os
from pathlib import Path
import sys
import time

def reset_at():
    return float(os.environ.get("FOREMAN_TEST_RESET_AT", str(time.time() + float(os.environ.get("FOREMAN_TEST_RESET_DELAY", "600")))))

if "app-server" in sys.argv:
    # TEST ONLY metadata protocol, never starts a model thread/turn.
    for line in sys.stdin:
        request = json.loads(line)
        if request["method"] == "initialize":
            print(json.dumps({"id": request["id"], "result": {"userAgent": "TEST ONLY"}}), flush=True)
        elif request["method"] == "account/rateLimits/read":
            reset = reset_at()
            print(json.dumps({"id": request["id"], "result": {"rateLimits": {
                "primary": {"usedPercent": 100, "resetsAt": reset}, "secondary": None}}}), flush=True)
        elif request["method"] != "initialized":
            raise SystemExit("TEST protocol forbids model methods")
    raise SystemExit(0)

root = Path.cwd()
prompt = sys.stdin.read()
output = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
schema = json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
role = "manager" if "action" in schema["properties"] else "worker"
marker = os.environ.get("FOREMAN_TEST_QUOTA_MARKER")
if marker:
    folder = Path(marker)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "calls.jsonl").open("a") as stream:
        stream.write(json.dumps({"role": role, "time": time.time()}) + "\n")
    if role in os.environ.get("FOREMAN_TEST_QUOTA_SCOPE", "").split(","):
        used = folder / role
        if not used.exists() or os.environ.get("FOREMAN_TEST_QUOTA_ALWAYS") == "1":
            used.touch()
            if role == "worker":
                partial = json.loads((root / "config.json").read_text())
                partial["epochs"] = 77
                (root / "config.json").write_text(json.dumps(partial))
            print(json.dumps({"type": "turn.failed", "error": {"code": "usage_limit_reached",
                  "message": "You've hit your usage limit. TEST ONLY",
                  "resets_at": reset_at()}}))
            raise SystemExit(75)
    if role == "worker" and os.environ.get("FOREMAN_TEST_EXPECT_PARTIAL") == "1":
        assert json.loads((root / "config.json").read_text())["epochs"] == 77, "Lost partial code on quota resume"
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
