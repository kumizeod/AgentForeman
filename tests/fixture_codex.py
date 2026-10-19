#!/usr/bin/env python3
"""TEST FIXTURE ONLY. Emulates CLI handoff, never calls a model.

It edits actual source/config files. Training and external evaluation are real.
This is deliberately unavailable as a production CLI backend.
"""
import json
import os
from pathlib import Path
import sys
import time

if "--version" in sys.argv:
    print("TEST FIXTURE, not Codex")
    raise SystemExit(0)
prompt = sys.stdin.read()
root = Path.cwd()
mode = os.environ.get("FOREMAN_TEST_MODE", "improve")
if mode == "worker_timeout":
    time.sleep(5)
if mode == "worker_failure":
    print("Intentional fixture failure", file=sys.stderr)
    raise SystemExit(17)
if mode == "forbidden":
    (root / "train.csv").write_text("tampered\n")
elif mode != "nochange":
    config = json.loads((root / "config.json").read_text())
    config.update(hidden=32, epochs=500, learning_rate=0.3)
    (root / "config.json").write_text(json.dumps(config, indent=2) + "\n")
if mode == "bad_training":
    (root / "model.py").write_text("raise RuntimeError('intentional training crash')\n")
if mode == "symlink":
    (root / "config.json").unlink()
    (root / "config.json").symlink_to("/etc/hostname")
if mode == "fake_metrics":
    # Candidate tries to claim 100%; the verifier will recompute from bad weights.
    (root / "model.py").write_text('''import numpy as np
def train(x, y, config):
    print("claimed_accuracy=1.0")
    return dict(w1=np.zeros((2,1)), b1=np.zeros(1), w2=np.zeros((1,1)), b2=np.zeros(1))
''')
answer = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
answer.write_text(json.dumps({
    "summary": "TEST FIXTURE ONLY: deterministic edit, no LLM call",
    "hypothesis": "Fixture increases optimization budget/width to exercise the real pipeline",
    "changes": ["config.json"],
}))
print(json.dumps({"type": "thread.started", "thread_id": "test-fixture-only"}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}))
