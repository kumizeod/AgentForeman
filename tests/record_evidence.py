"""Record a real local training run using the TEST-ONLY deterministic editor.

This script does NOT validate authenticated Codex inference. It is not installed
as a production entry point. Usage: python -m tests.record_evidence --output DIR
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

from agentforeman.cli import init_demo
from agentforeman.engine import create_run, drive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="foreman-evidence-") as temp:
        root = Path(temp)
        project = root / "project"
        worker = root / "TEST-ONLY-fixture-codex.py"
        shutil.copy2(Path(__file__).with_name("fixture_codex.py"), worker)
        worker.chmod(0o755)
        init_demo(project)
        cfg = project / "foreman.toml"
        cfg.write_text(cfg.read_text().replace('executable = "codex"', f"executable = {json.dumps(str(worker))}"))
        run_id = create_run(project, "TEST FIXTURE editing; actual training, independent verification and archive replay")
        with patch.dict(os.environ, {"FOREMAN_TEST_MODE": "improve"}):
            state = drive(project, run_id)
        run = project / ".foreman" / "runs" / run_id
        for source, target in (("archive.zip", "sample_archive.zip"),
                               ("report.json", "sample_report.json"), ("report.md", "sample_report.md")):
            shutil.copy2(run / source, output / target)
        summary = {"source_editing": "deterministic test fixture, NOT an LLM",
                   "training": "real NumPy gradient descent on CSV", "verification": "external checkpoint recomputation",
                   "baseline_metric": state["baseline_metric"], "candidate_metric": state["candidate_metric"],
                   "live_codex_validated": False}
        (output / "sample_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
