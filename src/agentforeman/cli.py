from __future__ import annotations

import argparse
import importlib.resources
import json
from pathlib import Path
import shutil
import sys

from .engine import PHASES, create_run, drive, load_config
from .files import atomic_text
from .report import verify_archive
from .store import Store
from .platforms import SUPPORTED
from .codex import resolve_command


CONFIG = '''version = 1
source = "workspace"
trusted = "trusted"
allowed_edit = ["model.py", "config.json"]

[codex]
executable = "codex"
# Set model to a model available to your Codex account, or leave unset.
timeout_seconds = 600

[train]
command = ["{python}", "{workspace}/train.py", "--output", "{artifacts}"]
timeout_seconds = 120

[verify]
command = ["{python}", "{trusted}/verify.py", "--checkpoint", "{artifacts}/model.npz", "--data", "{trusted}/validation.csv", "--output", "{artifacts}/verification.json"]
timeout_seconds = 30

[metric]
name = "accuracy"
direction = "maximize"
min_delta = 0.02
minimum = 0.0
maximum = 1.0
'''


def init_demo(project: Path):
    if project.exists() and any(project.iterdir()):
        raise ValueError("Init requires a new or empty directory; existing files will not be overwritten")
    try:
        import numpy as np
    except ImportError:
        raise ValueError('Demo requires NumPy: python -m pip install "agentforeman[demo]"') from None
    project.mkdir(parents=True, exist_ok=True)
    workspace = project / "workspace"
    trusted = project / "trusted"
    workspace.mkdir()
    trusted.mkdir()
    templates = importlib.resources.files("agentforeman") / "templates" / "moons"
    for name in ("train.py", "model.py", "config.json", "CONTRACT.md"):
        atomic_text(workspace / name, (templates / name).read_text(encoding="utf-8"))
    atomic_text(trusted / "verify.py", (templates / "verify.py").read_text(encoding="utf-8"))
    rng = np.random.default_rng(1407)
    # Synthetic CSV classification dataset; no downloads or hidden in-memory jobs.
    for path, count in ((workspace / "train.csv", 1200), (trusted / "validation.csv", 600)):
        n = count // 2
        angle = rng.uniform(0, np.pi, n)
        x = np.concatenate((np.column_stack((np.cos(angle), np.sin(angle))),
                            np.column_stack((1 - np.cos(angle), 0.5 - np.sin(angle)))))
        x += rng.normal(0, 0.16, x.shape)
        y = np.concatenate((np.zeros(n), np.ones(n)))
        data = np.column_stack((x, y))[rng.permutation(count)]
        np.savetxt(path, data, delimiter=",", header="x1,x2,label", comments="", fmt="%.10f")
    atomic_text(project / "foreman.toml", CONFIG)
    atomic_text(project / ".gitignore", ".foreman/\n__pycache__/\n.venv/\n")
    print(f"Created demo project: {project}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="foreman")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create the offline CSV/MLP experiment example")
    init.add_argument("path", type=Path)
    start = sub.add_parser("start", help="Submit a goal and run one candidate")
    start.add_argument("--project", type=Path, default=Path.cwd())
    start.add_argument("--goal", required=True)
    start.add_argument("--until", choices=PHASES[:-1], help="Pause BEFORE this phase; useful for inspecting the baseline")
    resume = sub.add_parser("resume", help="Reattach to existing work without repeating finished stages")
    resume.add_argument("run_id")
    resume.add_argument("--project", type=Path, default=Path.cwd())
    resume.add_argument("--retry-failed", action="store_true", help="Explicitly repeat the failed phase after checking logs")
    resume.add_argument("--until", choices=PHASES[:-1])
    for name in ("status", "events", "report", "cancel"):
        p = sub.add_parser(name)
        p.add_argument("run_id")
        p.add_argument("--project", type=Path, default=Path.cwd())
    runs = sub.add_parser("list")
    runs.add_argument("--project", type=Path, default=Path.cwd())
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--project", type=Path, default=Path.cwd())
    check = sub.add_parser("verify-archive")
    check.add_argument("path", type=Path)
    campaign = sub.add_parser("campaign", help="Manage bounded multi-round experiments")
    actions = campaign.add_subparsers(dest="campaign_command", required=True)
    for name in ("start", "resume", "status", "list", "events", "report"):
        p = actions.add_parser(name)
        p.add_argument("--project", type=Path, default=Path.cwd())
        if name not in {"start", "list"}:
            p.add_argument("campaign_id")
        if name in {"start", "resume"}:
            p.add_argument("--until", choices=["baseline", "decide", "execute", "record"])
        if name == "start":
            p.add_argument("--goal", required=True)
            p.add_argument("--max-rounds", type=int, default=3)
            p.add_argument("--budget-seconds", type=float, default=1800)
            p.add_argument("--target", type=float)
        if name == "resume":
            p.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            init_demo(args.path.expanduser().resolve())
            return 0
        if args.command == "verify-archive":
            print(json.dumps(verify_archive(args.path), indent=2))
            return 0
        project = args.project.expanduser().resolve()
        if args.command == "campaign":
            from .campaign import create_campaign, drive_campaign
            if args.campaign_command in {"start", "resume"}:
                cid = (create_campaign(project, args.goal, args.max_rounds, args.budget_seconds, args.target)
                       if args.campaign_command == "start" else args.campaign_id)
                print(f"Campaign ID: {cid}", flush=True)
                result = drive_campaign(project, cid, until=args.until,
                                        retry_failed=getattr(args, "retry_failed", False))
                print(json.dumps({k: result.get(k) for k in ("id", "status", "phase", "stop_reason", "best")}, indent=2))
                print(f"Report: {project / '.foreman' / 'campaigns' / cid / 'report.md'}")
            else:
                store = Store(project)
                try:
                    if args.campaign_command == "list":
                        result = [{k: s.get(k) for k in ("id", "goal", "phase", "status")} for s in store.campaigns()]
                    elif args.campaign_command == "events":
                        result = store.events("campaign:" + args.campaign_id)
                    elif args.campaign_command == "report":
                        path = store.root / "campaigns" / args.campaign_id / "report.md"
                        print(path.read_text(encoding="utf-8"))
                        return 0
                    else:
                        result = store.get_campaign(args.campaign_id)
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                finally:
                    store.close()
            return 0
        if args.command == "doctor":
            cfg = load_config(project)
            try:
                found = resolve_command(cfg["codex"])
                resolution_error = None
            except ValueError as exc:
                found, resolution_error = None, str(exc)
            print(json.dumps({"platform": sys.platform, "platform_supported": sys.platform in SUPPORTED, "codex_command": found,
                              "python": sys.executable, "config_valid": True,
                              "resolution_error": resolution_error,
                              "note": "CLI presence does not verify login, model access, or sandbox compatibility."}, indent=2))
            return 0 if found and sys.platform in SUPPORTED else 2
        if args.command in {"start", "resume"}:
            run_id = create_run(project, args.goal) if args.command == "start" else args.run_id
            print(f"Run ID: {run_id}", flush=True)
            result = drive(project, run_id, retry=getattr(args, "retry_failed", False), until=args.until)
            print(json.dumps({k: result.get(k) for k in ("id", "status", "phase", "baseline_metric", "candidate_metric", "objective_met")}, indent=2))
            if result["status"] == "completed":
                print(f"Archive: {project / '.foreman' / 'runs' / run_id / 'archive.zip'}")
            return 0
        store = Store(project)
        try:
            if args.command == "list":
                print(json.dumps([{k: row.get(k) for k in ("id", "goal", "status", "phase")} for row in store.runs()], ensure_ascii=False, indent=2))
                return 0
            state = store.get(args.run_id)
            run = store.root / "runs" / args.run_id
            if args.command == "status":
                print(json.dumps(state, ensure_ascii=False, indent=2))
            elif args.command == "events":
                print(json.dumps(store.events(args.run_id), ensure_ascii=False, indent=2))
            elif args.command == "report":
                report = run / "report.md"
                if not report.exists():
                    raise ValueError(f"Report not ready: status={state['status']}, phase={state['phase']}, error={state.get('error')}")
                print(report.read_text(encoding="utf-8"))
            elif args.command == "cancel":
                job = state.get("active_job")
                if not job or not (run / "jobs" / job / "process.json").exists():
                    raise ValueError("No active supervised job. Stop the manager with Ctrl-C to pause between stages.")
                atomic_text(run / "jobs" / job / "cancel.request", "cancel\n")
                print("Cancellation requested. The supervisor will stop the command and its known descendants.")
        finally:
            store.close()
        return 0
    except KeyboardInterrupt:
        print("Manager paused. Active jobs keep running; use foreman resume or foreman campaign resume with the printed ID.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"foreman: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
