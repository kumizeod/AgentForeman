from __future__ import annotations

import argparse
import importlib.resources
import json
import math
import time
from pathlib import Path
import shutil
import sys

from .engine import PHASES, create_run, drive, load_config
from .files import atomic_text
from .report import verify_archive
from .store import Store


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
    ui = sub.add_parser("ui", help="Open the local experiment control panel")
    ui.add_argument("--project", type=Path, default=Path.cwd())
    ui.add_argument("--port", type=int, default=8765, help="Local port; 0 selects a free port")
    ui.add_argument("--no-open", action="store_true", help="Print URL without opening a browser")
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
    doctor.add_argument("--json", action="store_true", help="Machine-readable diagnostics (also the default)")
    usage = sub.add_parser("usage", help="Read quota windows without model calls; cached unless explicitly refreshed")
    usage.add_argument("--project", type=Path, default=Path.cwd())
    usage.add_argument("--role", choices=["worker", "manager", "reviewer"], default="worker")
    freshness = usage.add_mutually_exclusive_group()
    freshness.add_argument("--refresh", action="store_true", help="Read metadata once via official Codex app-server")
    freshness.add_argument("--cached", action="store_true", help="Read the local snapshot only (default)")
    usage.add_argument("--json", action="store_true")
    monitor = sub.add_parser("monitor", help="Local experiment and cached quota panel; never calls Codex")
    monitor.add_argument("--project", type=Path, default=Path.cwd())
    monitor.add_argument("--campaign", dest="campaign_id")
    monitor.add_argument("--watch", action="store_true")
    monitor.add_argument("--interval", type=float, default=5)
    monitor.add_argument("--samples", type=int, default=0, help="Stop watching after N snapshots; 0 means until Ctrl-C")
    monitor.add_argument("--json", action="store_true", help="JSON snapshot; JSONL when watching")
    for name in ("stats", "diagnostics"):
        observe = sub.add_parser(name, help="Local token accounting" if name == "stats" else "Sanitized support report")
        observe.add_argument("--project", type=Path, default=Path.cwd())
        observe.add_argument("--json", action="store_true")
    check = sub.add_parser("verify-archive")
    check.add_argument("path", type=Path)
    for command in ("campaign", "overnight"):
        campaign = sub.add_parser(command, help="Manage experiments" if command == "campaign" else "Submit overnight work with model-free quota waiting")
        actions = campaign.add_subparsers(dest="campaign_command", required=True)
        for name in ("start", "resume", "status", "list", "events", "report", "stop"):
            p = actions.add_parser(name)
            p.add_argument("--project", type=Path, default=Path.cwd())
            if name not in {"start", "list"}:
                p.add_argument("campaign_id")
            if name in {"start", "resume"}:
                if command == "campaign":
                    p.add_argument("--until", choices=["baseline", "decide", "review", "revise", "execute", "record"])
                else:
                    p.add_argument("--foreground", action="store_true", help="Keep controller in this terminal; default is detached")
            if name == "start":
                p.add_argument("--goal", required=True)
                p.add_argument("--review", action="store_true", help="Require bounded independent proposal review before execution")
                p.add_argument("--max-reviews", type=int, default=2, help="Maximum reviews per proposal (1–5); no human approval stage")
                p.add_argument("--max-rounds", type=int, default=5 if command == "overnight" else 3)
                p.add_argument("--target", type=float)
                if command == "campaign":
                    p.add_argument("--budget-seconds", type=float, default=1800)
                else:
                    p.add_argument("--deadline", default="08:00", help="Next local HH:MM or full ISO time with offset; default 08:00")
                    p.add_argument("--quota-retry-seconds", type=float, default=18000, help="Conservative fallback if reset time is unavailable")
                    p.add_argument("--max-quota-retries", type=int, default=8)
                    p.add_argument("--quota-preflight", action="store_true", help="Check fresh quota metadata before each NEW planner/worker job")
                    p.add_argument("--no-quota-probe", action="store_true", help="Disable model-free app-server quota metadata lookup")
            if name == "resume":
                p.add_argument("--retry-failed", action="store_true")
    from .pool_cli import add_parser as add_pool_parser
    add_pool_parser(sub)
    from .council_cli import add_parser as add_council_parser
    add_council_parser(sub)
    from .coding_cli import add_parser as add_coding_parser
    add_coding_parser(sub)
    from .debate_cli import add_parser as add_debate_parser
    add_debate_parser(sub)
    add_debate_parser(sub,'cooperate')
    from .briefing_cli import add_parser as add_briefing_parser
    add_briefing_parser(sub)
    args = parser.parse_args(argv)
    try:
        if args.command=='briefing':
            from .briefing_cli import run
            return run(args)
        if args.command in {"debate", "cooperate"}:
            from .debate_cli import run
            return run(args)
        if args.command == "coding":
            from .coding_cli import run
            return run(args)
        if args.command == "council":
            from .council_cli import run
            return run(args)
        if args.command == "pool":
            from .pool_cli import run
            return run(args)
        if args.command == "init":
            init_demo(args.path.expanduser().resolve())
            return 0
        if args.command == "verify-archive":
            print(json.dumps(verify_archive(args.path), indent=2))
            return 0
        project = args.project.expanduser().resolve()
        if args.command == "ui":
            from .webserver import run_ui
            run_ui(project, args.port, open_browser=not args.no_open)
            return 0
        if args.command in {"usage", "monitor", "stats", "diagnostics", "doctor"}:
            from .observe import diagnostics, monitor_snapshot, render_monitor, statistics
            if args.command == "usage":
                from .usage import usage_snapshot
                result = usage_snapshot(project, args.role, refresh=args.refresh)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["available"] and not (args.refresh and result["error"]) else 2
            if args.command == "monitor":
                if not math.isfinite(args.interval) or args.interval < 1 or args.samples < 0:
                    raise ValueError("Monitor interval must be finite and >=1 second; samples must be >=0")
                count = 0
                while True:
                    result = monitor_snapshot(project, args.campaign_id)
                    if args.json:
                        print(json.dumps(result, ensure_ascii=False), flush=True)
                    else:
                        if args.watch and sys.stdout.isatty():
                            print("\033[2J\033[H", end="")
                        print(render_monitor(result), end="", flush=True)
                    count += 1
                    if not args.watch or (args.samples and count >= args.samples):
                        return 0
                    time.sleep(args.interval)
            if args.command == "stats":
                result = statistics(project)
            else:
                result = diagnostics(project)
                if args.command == "diagnostics":
                    result["statistics"] = statistics(project)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if result.get("ok") is False else 0
        if args.command in {"campaign", "overnight"}:
            from .campaign import create_campaign, drive_campaign
            if args.campaign_command in {"start", "resume"}:
                if args.campaign_command == "start":
                    if args.command == "overnight":
                        from .overnight import parse_deadline
                        cid = create_campaign(project, args.goal, args.max_rounds, 86400, args.target,
                                              deadline_at=parse_deadline(args.deadline),
                                              review_policy={"enabled": args.review, "max_reviews": args.max_reviews},
                                              quota_policy={"fallback_seconds": args.quota_retry_seconds,
                                                            "max_retries": args.max_quota_retries,
                                                            "probe": not args.no_quota_probe, "preflight": args.quota_preflight})
                    else:
                        cid = create_campaign(project, args.goal, args.max_rounds, args.budget_seconds, args.target,
                                              review_policy={"enabled": args.review, "max_reviews": args.max_reviews})
                else:
                    cid = args.campaign_id
                print(f"Campaign ID: {cid}", flush=True)
                if args.command == "overnight":
                    from .overnight import launch, serve
                    retry = getattr(args, "retry_failed", False)
                    if args.foreground:
                        result = serve(project, cid, retry_failed=retry)
                    else:
                        receipt = launch(project, cid, retry_failed=retry)
                        print(json.dumps({"controller": receipt, "note": "Detached controller; safe to close this terminal. Keep the computer awake."}, indent=2))
                        print(f"Report: {project / '.foreman' / 'campaigns' / cid / 'report.md'}")
                        print(f"Inspect: python -m agentforeman overnight status {cid} --project \"{project}\"")
                        return 0
                else:
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
                    elif args.campaign_command == "stop":
                        store.get_campaign(args.campaign_id)
                        atomic_text(store.root / "campaigns" / args.campaign_id / "stop.request", "stop\n")
                        print("Stop requested. The controller will finish/reconcile the current round and write its report; no further rounds will start.")
                        return 0
                    else:
                        result = store.get_campaign(args.campaign_id)
                        from .files import read_json
                        from .platforms import alive
                        path = store.root / "campaigns" / args.campaign_id / "runner.json"
                        receipt = read_json(path) if path.exists() else {}
                        result["controller_alive"] = alive(receipt.get("process"))
                        result["controller"] = receipt
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                finally:
                    store.close()
            return 0
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
        if args.command == "monitor":
            return 130
        print("Manager paused. Active jobs keep running; use foreman resume or foreman campaign resume with the printed ID.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"foreman: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
