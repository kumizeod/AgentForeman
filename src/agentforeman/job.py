"""Detached local job supervisor. One durable spec, one lock, one result receipt.

Native platform birth identities prevent interpreting a recycled PID as our child.
The manager may die without killing the supervisor or its command. Ambiguous
supervisor loss is surfaced, never silently retried.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .files import atomic_json, read_json
from .platforms import alive, identity, detached_options, file_lock, stop_process


def exec_child(job_dir: Path):
    """Self-report birth identity before exec; identity survives exec unchanged."""
    atomic_json(job_dir / "child.json", identity(os.getpid()))
    spec = read_json(job_dir / "spec.json")
    os.chdir(spec["cwd"])
    os.execvpe(spec["argv"][0], spec["argv"], os.environ.copy())


def supervise(job_dir: Path) -> int:
    try:
        with file_lock(job_dir / "supervisor.lock"):
            return _supervise_locked(job_dir)
    except BlockingIOError:
        return 0


def _supervise_locked(job_dir: Path) -> int:
    # A previous supervisor was here. Do not replay an uncertain side effect.
    if (job_dir / "result.json").exists() or (job_dir / "process.json").exists():
        return 0
    spec = read_json(job_dir / "spec.json")
    started = time.time()
    meta = {"supervisor": identity(os.getpid()), "child": None, "started_at": started}
    atomic_json(job_dir / "process.json", meta)
    child = None
    timed_out = False
    cancelled = False
    error = None
    exit_code = None
    try:
        env = os.environ.copy()
        env.update(spec.get("env", {}))
        with (job_dir / "stdout.log").open("wb") as out, (job_dir / "stderr.log").open("wb") as err:
            stdin_path = spec.get("stdin")
            source = open(stdin_path, "rb") if stdin_path else open(os.devnull, "rb")
            try:
                if time.time() >= spec.get("deadline_at", float("inf")):
                    timed_out = True
                    raise RuntimeError("Absolute deadline expired before child launch")
                argv = spec["argv"] if sys.platform == "win32" else [sys.executable, "-m", "agentforeman.job", "--child", str(job_dir)]
                child = subprocess.Popen(argv, cwd=spec["cwd"], env=env,
                                         stdin=source, stdout=out, stderr=err,
                                         **detached_options(supervisor=False))
                if sys.platform == "win32":
                    meta["child"] = identity(child.pid)
                    atomic_json(job_dir / "child.json", meta["child"])
                    atomic_json(job_dir / "process.json", meta)
            finally:
                source.close()
            deadline = time.monotonic() + spec["timeout_seconds"]
            if "deadline_at" in spec:
                deadline = min(deadline, time.monotonic() + max(0, spec["deadline_at"] - time.time()))
            while child.poll() is None:
                if meta["child"] is None and (job_dir / "child.json").exists():
                    meta["child"] = read_json(job_dir / "child.json")
                    atomic_json(job_dir / "process.json", meta)
                if (job_dir / "cancel.request").exists():
                    cancelled = True
                    stop_process(child)
                    break
                if time.monotonic() >= deadline or time.time() >= spec.get("deadline_at", float("inf")):
                    timed_out = True
                    stop_process(child)
                    break
                time.sleep(0.1)
            exit_code = child.wait()
            if meta["child"] is None and (job_dir / "child.json").exists():
                meta["child"] = read_json(job_dir / "child.json")
                atomic_json(job_dir / "process.json", meta)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if child is not None and child.poll() is None:
            try:
                stop_process(child)
            except Exception as cleanup:
                error += f"; cleanup failed: {type(cleanup).__name__}: {cleanup}"
    atomic_json(job_dir / "result.json", {
        "exit_code": exit_code, "timed_out": timed_out, "cancelled": cancelled,
        "error": error, "started_at": started, "finished_at": time.time(),
        "duration_seconds": time.time() - started,
    })
    return 0


def launch(job_dir: Path):
    """Only spec metadata crosses the manager/supervisor process boundary."""
    log = (job_dir / "supervisor.log").open("ab")
    try:
        return subprocess.Popen([sys.executable, "-m", "agentforeman.job", str(job_dir)],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                **detached_options(supervisor=True))
    finally:
        log.close()


def wait_for_job(job_dir: Path, spec: dict) -> dict:
    job_dir.mkdir(parents=True, exist_ok=True)
    spec_path = job_dir / "spec.json"
    if not spec_path.exists():
        atomic_json(spec_path, spec)
    elif read_json(spec_path) != spec:
        raise ValueError("Job specification changed; create a new attempt instead of overwriting it.")
    wrapper = None
    if not (job_dir / "result.json").exists() and not (job_dir / "process.json").exists():
        wrapper = launch(job_dir)
    deadline = time.monotonic() + 10
    try:
        while not (job_dir / "result.json").exists():
            meta_path = job_dir / "process.json"
            if meta_path.exists():
                meta = read_json(meta_path)
                if not alive(meta["supervisor"]):
                    # One final check: result may have been atomically written as it exited.
                    if (job_dir / "result.json").exists():
                        break
                    raise RuntimeError(
                        f"Supervisor lost for {job_dir.name}; child_alive={alive(meta.get('child'))}. "
                        "No automatic replay. Inspect process.json and logs before --retry-failed.")
            elif time.monotonic() > deadline:
                raise RuntimeError(f"Supervisor did not start. Inspect {job_dir / 'supervisor.log'}")
            time.sleep(0.1)
        result = read_json(job_dir / "result.json")
        # Windows cannot remove/rename an open lock or log file. Let a completed
        # supervisor release its handles before archiving or caller cleanup.
        if wrapper is not None:
            wrapper.wait(timeout=5)
        else:
            meta_path = job_dir / "process.json"
            release_deadline = time.monotonic() + 5
            while meta_path.exists() and alive(read_json(meta_path).get("supervisor")):
                if time.monotonic() >= release_deadline:
                    raise RuntimeError("Supervisor wrote a receipt but did not release its handles")
                time.sleep(0.02)
        return result
    finally:
        if wrapper is not None and wrapper.poll() is not None:
            wrapper.wait()


if __name__ == "__main__":
    if sys.argv[1] == "--child":
        exec_child(Path(sys.argv[2]).resolve())
    else:
        raise SystemExit(supervise(Path(sys.argv[1]).resolve()))
