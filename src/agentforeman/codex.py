"""Production Codex CLI adapter. No simulated backend is exposed by the CLI."""
from __future__ import annotations

from pathlib import Path
import json
import os
import shutil
import sys

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


def resolve_command(config: dict, *, npm_package="@openai/codex", bin_name="codex") -> list[str]:
    """Resolve native binaries or npm's Windows shim without cmd.exe parsing."""
    prefix = config.get("command") or [config.get("executable", "codex")]
    first = Path(prefix[0]).expanduser()
    # Preserve executable symlinks: resolving a venv's Python to the base binary
    # would silently discard that environment's installed dependencies.
    found = os.path.abspath(first) if first.is_file() and (first.is_absolute() or first.parent != Path(".")) else shutil.which(prefix[0])
    if not found and bin_name=='agy' and not config.get('command'):
        candidate=(Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'agy/bin/agy.exe' if sys.platform=='win32' else Path.home()/'.local/bin/agy')
        if candidate.is_file():found=str(candidate)
    if not found:
        raise ValueError("Codex CLI not found. Install @openai/codex, run codex login, then resume this run." if bin_name == "codex" else f"{bin_name} CLI not found. Install and authenticate the configured CLI before resuming.")
    path = Path(found)
    if sys.platform == "win32" and path.suffix.lower() in {".cmd", ".bat"}:
        if npm_package is None:raise ValueError("Choose the native agy.exe binary, not a Windows batch shim")
        package = path.parent / "node_modules" / npm_package
        manifest = package / "package.json"
        if manifest.is_file():
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
            entry = metadata.get("bin", {})
            entry = entry.get(bin_name) if isinstance(entry, dict) else entry
            script = (package / entry).resolve() if isinstance(entry, str) else None
            if script and script.is_file() and script.is_relative_to(package.resolve()):
                node = shutil.which("node")
                if node:
                    return [node, str(script), *prefix[1:]]
        raise ValueError("Found a Windows batch shim but cannot resolve its npm Codex entry. "
                         'Set codex.command = ["C:/path/to/codex.exe"] or '
                         '["node", "C:/path/to/@openai/codex/bin/codex.js"] in foreman.toml.')
    if path.suffix.lower() == ".py":
        return [sys.executable, str(path), *prefix[1:]]
    if path.suffix.lower() in {".js", ".mjs"}:
        node = shutil.which("node")
        if not node:
            raise ValueError("Node.js is required for the configured Codex JavaScript entry")
        return [node, str(path), *prefix[1:]]
    return [str(path), *prefix[1:]]


def make_spec(run: Path, state: dict, config: dict) -> dict:
    work = run / "workspace"
    control = run / "worker"
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
    if state.get("quota_resumed_jobs"):
        prompt += ("\nThis task resumes after a usage-limit interruption. Your workspace retains the previous "
                   "attempt's partial edits. Inspect and continue those edits rather than restarting blindly. "
                   "The manager has preserved baseline training and previous attempt logs.\n")
    return json_spec(control, work, prompt, SCHEMA, config["codex"], sandbox="workspace-write")


def json_spec(control: Path, cwd: Path, prompt: str, schema: dict, settings: dict, *, sandbox: str) -> dict:
    prefix = resolve_command(settings)
    control.mkdir(parents=True, exist_ok=True)
    atomic_json(control / "schema.json", schema)
    atomic_text(control / "prompt.txt", prompt)
    argv = [*prefix, "--ask-for-approval", "never", "exec", "--sandbox", sandbox,
            "--skip-git-repo-check", "--json", "--output-schema", str(control / "schema.json"),
            "--output-last-message", str(control / "answer.json")]
    if settings.get("model"):
        argv.extend(["--model", settings["model"]])
    argv.append("-")
    from .accounts import apply_profile
    return apply_profile({"argv": argv, "cwd": str(cwd), "stdin": str(control / "prompt.txt"),
            "timeout_seconds": settings["timeout_seconds"],
            "env": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}},settings)


def check_answer(run: Path) -> dict:
    answer = read_json(run / "worker" / "answer.json")
    if not isinstance(answer, dict) or set(answer) != set(SCHEMA["required"]):
        raise ValueError("Codex output does not match the expected handoff schema")
    if not isinstance(answer["summary"], str) or not isinstance(answer["hypothesis"], str):
        raise ValueError("Codex summary/hypothesis must be strings")
    if not isinstance(answer["changes"], list) or not all(isinstance(x, str) for x in answer["changes"]):
        raise ValueError("Codex changes must be a string array")
    return answer
