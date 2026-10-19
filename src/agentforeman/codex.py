"""Production Codex CLI adapter. No simulated backend is exposed by the CLI."""
from __future__ import annotations

from pathlib import Path
import shutil

from .files import atomic_json, atomic_text, read_json


SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "hypothesis": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "hypothesis", "changes"],
}


def make_spec(run: Path, state: dict, config: dict) -> dict:
    executable = shutil.which(config["codex"]["executable"])
    if not executable:
        raise ValueError("Codex CLI not found. Install @openai/codex, run codex login, then resume this run.")
    work = run / "workspace"
    control = run / "worker"
    control.mkdir(exist_ok=True)
    atomic_json(control / "schema.json", SCHEMA)
    prompt = f"""You are the coding worker for one bounded ML experiment.
Goal: {state['goal']}
Baseline independently measured metric: {state.get('baseline_metric')}
Metric: {config['metric']['name']}; direction: {config['metric']['direction']}
Editable files (exact relative paths): {config['allowed_edit']}
Work only inside this directory. Do not modify other files or symlinks.
Implement one coherent candidate now. Explain its hypothesis.
Do not run the full training job, install dependencies, edit datasets, manufacture
metrics, or create model outputs. The manager runs training and an external verifier.
Keep the trainer interface and checkpoint contract described in CONTRACT.md.
You may run short syntax checks. Return the requested JSON summary when finished.
This is a single candidate, not an open-ended research loop.
"""
    atomic_text(control / "prompt.txt", prompt)
    argv = [executable, "--ask-for-approval", "never", "exec", "--sandbox", "workspace-write",
            "--skip-git-repo-check", "--json", "--output-schema", str(control / "schema.json"),
            "--output-last-message", str(control / "answer.json")]
    if config["codex"].get("model"):
        argv.extend(["--model", config["codex"]["model"]])
    argv.append("-")
    return {"argv": argv, "cwd": str(work), "stdin": str(control / "prompt.txt"),
            "timeout_seconds": config["codex"]["timeout_seconds"],
            "env": {"PYTHONDONTWRITEBYTECODE": "1"}}


def check_answer(run: Path) -> dict:
    answer = read_json(run / "worker" / "answer.json")
    if not isinstance(answer, dict) or set(answer) != set(SCHEMA["required"]):
        raise ValueError("Codex output does not match the expected handoff schema")
    if not isinstance(answer["summary"], str) or not isinstance(answer["hypothesis"], str):
        raise ValueError("Codex summary/hypothesis must be strings")
    if not isinstance(answer["changes"], list) or not all(isinstance(x, str) for x in answer["changes"]):
        raise ValueError("Codex changes must be a string array")
    return answer
