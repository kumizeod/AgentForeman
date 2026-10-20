"""Generate explicitly fixture-led campaign evidence. Never calls a model."""
from contextlib import redirect_stdout
import argparse
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from agentforeman.cli import init_demo
from agentforeman.campaign import create_campaign, drive_campaign
from agentforeman.files import atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evidence/campaign-summary.json"))
    args = parser.parse_args()
    fixture = Path(__file__).with_name("fixture_campaign.py").resolve()
    script = '["bad", "repair", "retest", "worse", "stop"]'
    with tempfile.TemporaryDirectory(prefix="foreman-campaign-evidence-") as tmp, patch.dict(os.environ, {"FOREMAN_CAMPAIGN_SCRIPT": script}):
        project = Path(tmp)
        with redirect_stdout(io.StringIO()):
            init_demo(project)
            cfg = project / "foreman.toml"
            cfg.write_text(cfg.read_text().replace('executable = "codex"', f"executable = {json.dumps(str(fixture))}"))
            cid = create_campaign(project, "TEST ONLY: demonstrate failure, repair, retest and best retention", 5, 180)
            state = drive_campaign(project, cid)
        summary = {
            "disclosure": "TEST FIXTURE planner and editor, NO LLM calls; real CSV/NumPy training and independent NPZ verification. Not evidence of autonomous model discovery.",
            "version": "0.3.0", "stop_reason": state["stop_reason"],
            "initial_metric": state["initial_metric"], "best_metric": state["best"]["metric"],
            "rounds": [{k: r[k] for k in ("number", "action", "status", "phase", "promoted", "repairable", "decision")}
                       | {"metric": r.get("metric"), "error": r.get("error")} for r in state["rounds"]],
        }
        atomic_json(args.output, summary)
        print(f"Wrote fixture-led campaign evidence: {args.output}")


if __name__ == "__main__":
    main()
