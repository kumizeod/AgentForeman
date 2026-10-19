"""Portable evidence archives with per-file checksums and replay commands."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import sys
import zipfile

from .files import atomic_json, atomic_text, digest


REPRODUCE = '''#!/usr/bin/env python3
"""Re-run the archived code and trusted verification; no Codex required."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--baseline", action="store_true")
args = parser.parse_args()
cfg = json.loads((root / "config.json").read_text())
kind = "baseline" if args.baseline else "candidate"
code = root / kind / "code"
output = root / "reproduced" / kind
output.mkdir(parents=True, exist_ok=True)
values = dict(python=sys.executable, workspace=str(code), artifacts=str(output), trusted=str(root / "trusted"))
env = os.environ.copy()
env.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
for stage in ("train", "verify"):
    argv = []
    for item in cfg[stage]["command"]:
        for key, value in values.items():
            item = item.replace("{" + key + "}", value)
        argv.append(item)
    print("Running:", argv, flush=True)
    subprocess.run(argv, cwd=code if stage == "train" else root / "trusted", env=env,
                   timeout=cfg[stage]["timeout_seconds"], check=True)
print((output / "verification.json").read_text())
'''


def archive_run(run: Path, state: dict, events: list):
    baseline, candidate = state["baseline_metric"], state["candidate_metric"]
    metric = state["config"]["metric"]
    atomic_json(run / "report.json", {
        "run_id": state["id"], "goal": state["goal"],
        "worker_backend": "codex-cli-adapter",
        "worker_executable": state["config"]["codex"]["executable"],
        "baseline": state["baseline_verdict"], "candidate": state["candidate_verdict"],
        "improvement": state["improvement"], "objective_met": state["objective_met"],
        "changed_files": state["changed_files"], "worker": state["worker_answer"],
        "code_hashes": state["candidate_hashes"], "trusted_hashes": state["trusted_hashes"],
        "retries": state["retries"],
    })
    atomic_json(run / "events.json", events)
    atomic_json(run / "environment.json", {
        "python": sys.version, "platform": platform.platform(), "foreman_version": "0.1.0",
        "note": "Dependency versions used by the bundled trainer are recorded in training.json. No credentials collected.",
    })
    outcome = "met" if state["objective_met"] else "not met"
    text = f"""# Experiment {state['id']}

Goal: {state['goal']}

| Independent measurement | {metric['name']} |
| --- | ---: |
| Baseline | {baseline:.6f} |
| Candidate | {candidate:.6f} |
| Direction-adjusted improvement | {state['improvement']:.6f} |

Objective: **{outcome}** (required delta: {metric['min_delta']}).
Successful execution/verification does not imply the candidate improved.
This is one candidate and one seed; it is not statistical evidence of a research advance.

Worker hypothesis: {state['worker_answer']['hypothesis']}

Worker summary: {state['worker_answer']['summary']}

Changed files: {', '.join(state['changed_files'])}

## Evidence

- `changes.patch`: exact candidate changes.
- `baseline/code`, `candidate/code`: source and input snapshots.
- `*/artifacts`: actual checkpoints, training logs and external verification reports.
- `jobs/*`: argv specifications, process identities, stdout/stderr, exit receipts.
- `worker`: goal prompt, response schema, structured answer.
- `manifest.json`: SHA-256 checksums for the archive contents (not a signature).

## Reproduce

Install the project's training dependencies. For the bundled example: `python -m pip install numpy`.
Run `python reproduce.py --baseline` and `python reproduce.py` after extracting the archive.
Only execute archives from sources you trust. Training code runs with your account permissions.
"""
    atomic_text(run / "report.md", text)
    atomic_text(run / "reproduce.py", REPRODUCE)
    # Keep the archive portable: job receipts retain historical absolute paths;
    # reproduce.py resolves the code/data paths relative to the extracted archive.
    selected = []
    for path in sorted(run.rglob("*")):
        rel = path.relative_to(run)
        if path.is_symlink():
            raise ValueError(f"Cannot archive symlink: {rel}")
        if not path.is_file() or path.name.endswith(".lock") or path.name in {"archive.zip", "archive.tmp", "manifest.json"}:
            continue
        if "__pycache__" in rel.parts or rel.parts[0] in {"workspace", "seed"}:
            continue
        selected.append(path)
    atomic_json(run / "manifest.json", {
        "algorithm": "sha256", "files": {p.relative_to(run).as_posix(): digest(p) for p in selected}
    })
    temporary = run / "archive.tmp"
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as out:
        for path in selected + [run / "manifest.json"]:
            out.write(path, path.relative_to(run).as_posix())
    temporary.replace(run / "archive.zip")


def verify_archive(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive entries")
        for name in names:
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or "\\" in name:
                raise ValueError(f"Unsafe archive entry: {name}")
        manifest = json.loads(archive.read("manifest.json"))
        expected = manifest["files"]
        if set(names) != set(expected) | {"manifest.json"}:
            raise ValueError("Archive entries differ from manifest")
        for name, wanted in expected.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != wanted:
                raise ValueError(f"Checksum mismatch: {name}")
        return {"valid": True, "files": len(expected)}
