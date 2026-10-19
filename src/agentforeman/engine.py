"""One candidate experiment, durable stage boundaries, independent verification."""
from __future__ import annotations

import difflib
import json
import math
from pathlib import Path
import shutil
import sys
import time
import tomllib
import uuid

from . import codex
from .files import atomic_json, atomic_text, inventory, read_json, render_argv, replace_snapshot, snapshot
from .job import alive, wait_for_job
from .store import Store, run_lock


PHASES = ["baseline_train", "baseline_verify", "codex", "check_edits", "train", "verify", "archive", "done"]


def load_config(project: Path) -> dict:
    with (project / "foreman.toml").open("rb") as stream:
        cfg = tomllib.load(stream)
    if cfg.get("version") != 1:
        raise ValueError("foreman.toml requires version = 1")
    for key in ("source", "trusted"):
        path = (project / cfg[key]).resolve()
        if not path.is_relative_to(project) or not path.is_dir() or ".foreman" in path.relative_to(project).parts:
            raise ValueError(f"{key} must be a directory inside the project, outside .foreman")
        cfg[key] = str(path.relative_to(project))
    a, b = (project / cfg["source"]).resolve(), (project / cfg["trusted"]).resolve()
    if a.is_relative_to(b) or b.is_relative_to(a):
        raise ValueError("source and trusted must be separate, non-nested directories")
    editable = cfg["allowed_edit"]
    if not isinstance(editable, list) or not editable:
        raise ValueError("allowed_edit must list exact relative file paths")
    for rel in editable:
        if not isinstance(rel, str):
            raise ValueError("Editable paths must be strings")
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts or not p.parts or p.as_posix() != rel:
            raise ValueError(f"Invalid editable path: {rel}")
        if not (a / p).is_file() or (a / p).is_symlink():
            raise ValueError(f"Editable file must already exist: {rel}")
    for key in ("train", "verify"):
        section = cfg[key]
        cmd = section["command"]
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
            raise ValueError(f"{key}.command must be a nonempty argv array")
    for key in ("train", "verify", "codex"):
        value = cfg[key]["timeout_seconds"]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key}.timeout_seconds must be a positive finite number")
    if not isinstance(cfg["codex"]["executable"], str) or not cfg["codex"]["executable"]:
        raise ValueError("codex.executable must be a nonempty string")
    metric = cfg["metric"]
    if metric["direction"] not in {"maximize", "minimize"}:
        raise ValueError("metric.direction must be maximize or minimize")
    for key in ("min_delta", "minimum", "maximum"):
        val = metric[key]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise ValueError(f"metric.{key} must be finite")
    if metric["min_delta"] < 0 or metric["minimum"] > metric["maximum"]:
        raise ValueError("Invalid metric bounds/min_delta")
    return cfg


def create_run(project: Path, goal: str) -> str:
    if sys.platform != "linux":
        raise ValueError("This release supports Linux / WSL2 only")
    if not goal.strip():
        raise ValueError("Goal cannot be empty")
    cfg = load_config(project)
    store = Store(project)
    run_id = uuid.uuid4().hex[:12]
    run = store.root / "runs" / run_id
    run.mkdir(parents=True)
    try:
        snapshot(project / cfg["source"], run / "seed")
        snapshot(run / "seed", run / "workspace")
        snapshot(project / cfg["trusted"], run / "trusted")
        snapshot(run / "seed", run / "baseline" / "code")
        (run / "baseline" / "artifacts").mkdir()
        atomic_json(run / "config.json", cfg)
        state = {"id": run_id, "goal": goal, "phase": "baseline_train", "status": "ready",
                 "created_at": time.time(), "retries": {}, "active_job": None,
                 "seed_hashes": inventory(run / "seed"), "trusted_hashes": inventory(run / "trusted"),
                 "config": cfg}
        store.save(state, "run_created", goal=goal)
    finally:
        store.close()
    return run_id


def assert_integrity(run: Path, state: dict):
    if inventory(run / "seed") != state["seed_hashes"]:
        raise ValueError("Frozen baseline was changed")
    if inventory(run / "trusted") != state["trusted_hashes"]:
        raise ValueError("Trusted verifier/data changed")
    if read_json(run / "config.json") != state["config"]:
        raise ValueError("Frozen run configuration changed")


def command_spec(run: Path, state: dict, section: str, baseline: bool) -> dict:
    cfg = state["config"]
    root = run / ("baseline" if baseline else "candidate")
    out = root / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    argv = render_argv(cfg[section]["command"], python=sys.executable,
                       workspace=str(root / "code"), artifacts=str(out),
                       trusted=str(run / "trusted"))
    return {"argv": argv, "cwd": str(root / "code" if section == "train" else run / "trusted"),
            "timeout_seconds": cfg[section]["timeout_seconds"],
            "env": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
                    "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}}


def read_verdict(run: Path, state: dict, baseline: bool) -> dict:
    root = run / ("baseline" if baseline else "candidate")
    verdict = read_json(root / "artifacts" / "verification.json")
    if not isinstance(verdict, dict):
        raise ValueError("Verifier output must be a JSON object")
    value = verdict.get("metric")
    bounds = state["config"]["metric"]
    if verdict.get("valid") is not True or verdict.get("metric_name") != bounds["name"]:
        raise ValueError(f"Independent verifier rejected output: {verdict}")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Verifier metric must be a finite number")
    if not bounds["minimum"] <= value <= bounds["maximum"]:
        raise ValueError("Verifier metric out of configured bounds")
    expected = state["seed_hashes"] if baseline else state["candidate_hashes"]
    if inventory(root / "code") != expected:
        raise ValueError("Code/data changed during execution; result cannot be attributed to the snapshot")
    return verdict


def check_edits(run: Path, state: dict):
    before, after = state["seed_hashes"], inventory(run / "workspace")
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    if not changed:
        raise ValueError("Worker made no source changes")
    forbidden = set(changed) - set(state["config"]["allowed_edit"])
    if forbidden:
        raise ValueError(f"Worker edited protected files: {sorted(forbidden)}")
    deleted = set(before) - set(after)
    if deleted:
        raise ValueError(f"Deleting source files is unsupported: {sorted(deleted)}")
    answer = codex.check_answer(run)
    state["worker_answer"] = answer
    state["changed_files"] = changed
    replace_snapshot(run / "workspace", run / "candidate" / "code")
    state["candidate_hashes"] = inventory(run / "candidate" / "code")
    chunks = []
    for rel in changed:
        old = (run / "seed" / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        new = (run / "workspace" / rel).read_text(encoding="utf-8").splitlines(keepends=True)
        chunks.extend(difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    atomic_text(run / "changes.patch", "".join(chunks))


def retry_failed(run: Path, state: dict, store: Store):
    job_name = state.get("active_job")
    if job_name:
        meta = run / "jobs" / job_name / "process.json"
        if meta.exists():
            obj = read_json(meta)
            child_receipt = meta.parent / "child.json"
            child = obj.get("child") or (read_json(child_receipt) if child_receipt.exists() else None)
            if alive(obj.get("supervisor")) or alive(child):
                raise ValueError("An old supervisor/child is still alive; stop or inspect it before retrying")
        elif (run / "jobs" / job_name / "spec.json").exists():
            # A launch could be between Popen and process receipt. Never replay that uncertainty.
            if not (run / "jobs" / job_name / "result.json").exists():
                raise ValueError("Missing process/result receipt; inspect supervisor.log before manual recovery")
    phase = state["phase"]
    if phase == "check_edits":
        phase = "codex"
    if phase == "codex":
        replace_snapshot(run / "seed", run / "workspace")
        shutil.rmtree(run / "worker", ignore_errors=True)
    if phase in {"baseline_train", "train"}:
        target = run / ("baseline" if phase == "baseline_train" else "candidate") / "artifacts"
        # Keep failed outputs as evidence; do not allow stale checkpoint/verdict reuse.
        if target.exists():
            archive = run / "failed_outputs"
            archive.mkdir(exist_ok=True)
            shutil.move(target, archive / f"{phase}-{uuid.uuid4().hex[:8]}")
    if phase in {"baseline_verify", "verify"}:
        folder = run / ("baseline" if phase == "baseline_verify" else "candidate") / "artifacts"
        (folder / "verification.json").unlink(missing_ok=True)
    state["phase"] = phase
    state["retries"][phase] = state["retries"].get(phase, 0) + 1
    state["status"] = "ready"
    state["active_job"] = None
    state.pop("error", None)
    store.save(state, "explicit_retry", phase=phase)


def drive(project: Path, run_id: str, retry: bool = False, until: str | None = None) -> dict:
    store = Store(project)
    run = store.root / "runs" / run_id
    state = store.get(run_id)
    try:
        with run_lock(run):
            state = store.get(run_id)
            if state["status"] == "failed":
                if not retry:
                    raise ValueError(f"Run failed: {state.get('error')}. Inspect logs; use resume --retry-failed to retry.")
                retry_failed(run, state, store)
            if state["status"] == "completed":
                return state
            try:
                while state["phase"] != "done":
                    phase = state["phase"]
                    if until == phase:
                        state["status"] = "paused"
                        store.save(state, "paused_before_phase", phase=phase)
                        return state
                    assert_integrity(run, state)
                    state["status"] = "running"
                    store.save(state, "phase_started", phase=phase)
                    print(f"[{run_id}] {phase}", flush=True)
                    if phase in {"baseline_train", "baseline_verify", "codex", "train", "verify"}:
                        name = f"{phase}-{state['retries'].get(phase, 0):02d}"
                        job_dir = run / "jobs" / name
                        state["active_job"] = name
                        store.save(state, "job_attached", job=name)
                        # Reuse the original durable spec on resume; never re-render a live job.
                        if (job_dir / "spec.json").exists():
                            spec = read_json(job_dir / "spec.json")
                        elif phase == "codex":
                            spec = codex.make_spec(run, state, state["config"])
                        else:
                            spec = command_spec(run, state, "verify" if "verify" in phase else "train",
                                                phase.startswith("baseline"))
                        if "verify" in phase and not (job_dir / "process.json").exists():
                            folder = run / ("baseline" if phase.startswith("baseline") else "candidate") / "artifacts"
                            (folder / "verification.json").unlink(missing_ok=True)
                        result = wait_for_job(job_dir, spec)
                        if result["exit_code"] != 0 or result["timed_out"] or result["cancelled"] or result["error"]:
                            raise RuntimeError(f"Job {name} failed: {result}; inspect jobs/{name}/stderr.log")
                        assert_integrity(run, state)
                        if "verify" in phase:
                            verdict = read_verdict(run, state, phase.startswith("baseline"))
                            state["baseline_metric" if phase.startswith("baseline") else "candidate_metric"] = verdict["metric"]
                            state["baseline_verdict" if phase.startswith("baseline") else "candidate_verdict"] = verdict
                        state["active_job"] = None
                    elif phase == "check_edits":
                        check_edits(run, state)
                    elif phase == "archive":
                        from .report import archive_run
                        cfg = state["config"]["metric"]
                        delta = state["candidate_metric"] - state["baseline_metric"]
                        state["improvement"] = delta if cfg["direction"] == "maximize" else -delta
                        state["objective_met"] = state["improvement"] >= cfg["min_delta"]
                        archive_run(run, state, store.events(run_id))
                    state["phase"] = PHASES[PHASES.index(phase) + 1]
                    store.save(state, "phase_completed", phase=phase)
                state["status"] = "completed"
                store.save(state, "run_completed", objective_met=state["objective_met"])
                return state
            except KeyboardInterrupt:
                state["status"] = "paused"
                store.save(state, "manager_interrupted", active_job=state.get("active_job"))
                raise
            except Exception as exc:
                state["status"] = "failed"
                state["error"] = f"{type(exc).__name__}: {exc}"
                store.save(state, "run_failed", error=state["error"])
                raise
    finally:
        store.close()
