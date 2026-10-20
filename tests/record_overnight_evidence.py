"""TEST ONLY accelerated quota windows; real training and independent verification."""
from contextlib import redirect_stdout
import argparse
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from agentforeman.campaign import create_campaign
from agentforeman.cli import init_demo
from agentforeman.files import atomic_json
from agentforeman.overnight import serve
from agentforeman.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evidence/overnight-summary.json"))
    args = parser.parse_args()
    fixture = Path(__file__).with_name("fixture_campaign.py").resolve()
    with tempfile.TemporaryDirectory(prefix="foreman-overnight-evidence-") as tmp:
        project = Path(tmp) / "project"
        marker = Path(tmp) / "quota-fixture"
        settings = {"FOREMAN_CAMPAIGN_SCRIPT": '["good", "stop"]', "FOREMAN_TEST_QUOTA_MARKER": str(marker),
                    "FOREMAN_TEST_QUOTA_SCOPE": "manager,worker", "FOREMAN_TEST_RESET_DELAY": "0.25"}
        with patch.dict(os.environ, settings), redirect_stdout(io.StringIO()):
            init_demo(project)
            cfg = project / "foreman.toml"
            cfg.write_text(cfg.read_text().replace('executable = "codex"', f"executable = {json.dumps(str(fixture))}"))
            cid = create_campaign(project, "TEST ONLY: overnight planner and worker quota recovery", 1, 180,
                                  quota_policy={"minimum_seconds": 0.05})
            state = serve(project, cid)
            store = Store(project)
            try:
                events = store.events("campaign:" + cid)
            finally:
                store.close()
        calls = [json.loads(line) for line in (marker / "calls.jsonl").read_text().splitlines()]
        summary = {"version": "0.4.0",
                   "disclosure": "TEST ONLY planner/editor and quota metadata; 0.25-second fixture resets, not real account exhaustion or a five-hour wait. Real CSV training, NPZ checkpoints and independent verification.",
                   "status": state["status"], "stop_reason": state["stop_reason"],
                   "initial_metric": state["initial_metric"], "best_metric": state["best"]["metric"],
                   "metric_criterion_met": state["metric_criterion_met"],
                   "quota_resumptions": state["quota_retries"], "quota_wait_seconds": state["quota_wait_seconds"],
                   "fixture_model_invocations": calls,
                   "quota_events": [e for e in events if e["kind"] in {"quota_waiting", "quota_woke"}],
                   "rounds": [{k: r[k] for k in ("number", "action", "status", "metric", "promoted")} for r in state["rounds"]]}
        atomic_json(args.output, summary)
        print(f"Wrote explicitly accelerated fixture evidence: {args.output}")


if __name__ == "__main__":
    main()
