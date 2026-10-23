"""Local-only monitoring, bounded token accounting and shareable diagnostics."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import time

from . import __version__
from .codex import resolve_command
from .engine import load_config
from .files import read_json
from .platforms import SUPPORTED, alive
from .usage import usage_snapshot


def documents(project: Path, table: str) -> list[dict]:
    if table not in {"runs", "campaigns"}:
        raise ValueError("Unknown state table")
    path = project / ".foreman" / "state.sqlite3"
    if not path.exists():
        return []
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        return [json.loads(row[0]) for row in db.execute(f"SELECT document FROM {table} ORDER BY updated DESC")]
    finally:
        db.close()


def optional_json(path: Path) -> dict:
    try:
        value = read_json(path)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def monitor_snapshot(project: Path, campaign_id: str | None = None) -> dict:
    now = time.time()
    campaigns = documents(project, "campaigns")
    if campaign_id is not None:
        campaigns = [s for s in campaigns if s["id"] == campaign_id]
        if not campaigns:
            raise ValueError("Unknown campaign")
    rows = []
    for state in campaigns:
        root = project / ".foreman" / "campaigns" / state["id"]
        runner = optional_json(root / "runner.json")
        live = alive(runner.get("process"))
        waiting = state.get("quota_wait") or {}
        child = None
        pending = state.get("pending")
        if pending:
            child = next((s for s in documents(root / "project", "runs") if s["id"] == pending["run_id"]), None)
        rows.append({"id": state["id"], "status": state["status"], "phase": state["phase"],
                     "child_phase": child.get("phase") if child else None,
                     "controller_alive": live,
                     "needs_resume": bool(state.get("quota_policy")) and not live and state["status"] not in {"completed", "failed"},
                     "rounds": len(state["rounds"]), "max_rounds": state["max_rounds"],
                     "best_metric": (state.get("best") or {}).get("metric"),
                     "deadline_at": state["deadline_at"], "seconds_left": max(0, state["deadline_at"] - now),
                     "quota_retries": state.get("quota_retries", 0),
                     "wake_at": waiting.get("wake_at"),
                     "wake_in_seconds": max(0, waiting["wake_at"] - now) if waiting else None,
                     "stop_requested": (root / "stop.request").exists()})
    return {"generated_at": now, "campaigns": rows,
            "runs": [{k: s.get(k) for k in ("id", "status", "phase", "baseline_metric", "candidate_metric")}
                     for s in documents(project, "runs")],
            "quota": [usage_snapshot(project, role) for role in ("worker", "manager", "reviewer")],
            "note": "Local snapshot only. No Codex process, metadata refresh or model call."}


def stamp(value):
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds") if value is not None else "unknown"


def render_monitor(snapshot: dict) -> str:
    lines = [f"AgentForeman {__version__} | {stamp(snapshot['generated_at'])}"]
    for quota in snapshot["quota"]:
        status = "stale/unknown" if quota["stale"] else "cached"
        lines.append(f"{quota['role']} quota: {status}; refresh explicitly with foreman usage --refresh --role {quota['role']}")
        for window in quota.get("windows") or []:
            lines.append(f"  {window['name']} ({window['duration_minutes']} min): {window['remaining_percent']}% remaining; reset {stamp(window['resets_at'])}")
    for row in snapshot["campaigns"]:
        lines.append(f"{row['id']} | {row['status']}/{row['phase']} | rounds {row['rounds']}/{row['max_rounds']} | best {row['best_metric']} | controller {'alive' if row['controller_alive'] else 'absent'}")
        lines.append(f"  deadline {stamp(row['deadline_at'])}; child {row['child_phase'] or '-'}; wake {stamp(row['wake_at'])}")
        if row["needs_resume"]:
            lines.append(f"  ACTION: foreman overnight resume {row['id']} --project <project>")
        if row["status"] == "failed":
            lines.append("  ACTION: inspect overnight status/report before explicit --retry-failed")
    for row in snapshot["runs"]:
        lines.append(f"run {row['id']} | {row['status']}/{row['phase']}")
    if not snapshot["campaigns"] and not snapshot["runs"]:
        lines.append("No experiments submitted yet.")
    return "\n".join(lines) + "\n"


def model_jobs(project: Path):
    root = project / ".foreman"
    # Known live-log locations only: archives and quota snapshots are excluded.
    for pattern in ("runs/*/jobs/codex-*", "campaigns/*/decisions/*/job", "campaigns/*/discussions/*/review-*/job", "campaigns/*/discussions/*/revise-*/job", "campaigns/*/project/.foreman/runs/*/jobs/codex-*"):
        yield from root.glob(pattern)


def job_usage(path: Path, limit: int = 32 * 1024 * 1024) -> dict:
    totals = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    turns, complete, truncated = 0, True, False
    try:
        with path.open("rb") as stream:
            size = path.stat().st_size
            if size > limit:
                stream.seek(size - limit)
                stream.readline(1048577)  # Discard a potentially partial first line.
                truncated = True
            while line := stream.readline(1048577):
                if len(line) > 1048576:
                    complete = False
                    # Drain oversized records without interpreting fragments as events.
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(1048577)
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    complete = False
                    continue
                if not isinstance(event, dict) or event.get("type") != "turn.completed":
                    continue
                usage = event.get("usage")
                if not isinstance(usage, dict):
                    complete = False
                    continue
                values = {k: usage.get(k, 0 if k == "cached_input_tokens" else None) for k in totals}
                if any(type(v) is not int or v < 0 for v in values.values()) or values["cached_input_tokens"] > values["input_tokens"]:
                    complete = False
                    continue
                turns += 1
                for key, value in values.items():
                    totals[key] += value
    except OSError:
        complete = False
    return {**totals, "total_tokens": totals["input_tokens"] + totals["output_tokens"],
            "reported_turns": turns, "complete": complete and not truncated and turns > 0,
            "truncated": truncated}


def statistics(project: Path) -> dict:
    totals = Counter()
    jobs = 0
    by_role = {role: Counter() for role in ("manager", "worker", "reviewer")}
    for job in model_jobs(project):
        if job.is_symlink():
            continue
        jobs += 1
        role = "worker" if job.name.startswith("codex-") else "reviewer" if job.parent.name.startswith("review-") else "manager"
        usage = job_usage(job / "stdout.log")
        values = {k: usage[k] for k in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", "reported_turns")}
        values.update(jobs=1, jobs_with_usage=int(usage["reported_turns"] > 0), jobs_with_incomplete_usage=int(not usage["complete"]))
        totals.update(values)
        by_role[role].update(values)
    campaigns = documents(project, "campaigns")
    waits = sum(s.get("quota_wait_seconds", 0) for s in campaigns)
    for state in campaigns:
        if state["status"] == "waiting_quota" and state.get("quota_wait"):
            waits += max(0, time.time() - state["quota_wait"]["started_at"])
    return {"model_jobs": jobs, "reported_usage": dict(totals), "by_role": {k: dict(v) for k, v in by_role.items()},
            "campaigns": dict(Counter(s["status"] for s in campaigns)), "quota_wait_seconds": waits,
            "billing_estimate": None,
            "note": "Observed AgentForeman CLI turn.completed records only; missing/partial usage is unknown. Cached input is a subset of input. Tokens are not subscription quota percentages or billed API cost. Summed job/wait times are not utilization."}


def diagnostics(project: Path) -> dict:
    checks = []
    cfg = None
    try:
        cfg = load_config(project)
        checks.append({"check": "config", "ok": True})
    except (OSError, ValueError, KeyError):
        checks.append({"check": "config", "ok": False, "hint": "Check foreman.toml, source/trusted paths and command arrays."})
    checks.append({"check": "platform", "ok": sys.platform in SUPPORTED})
    if cfg:
        for role in ("worker", "manager", "reviewer"):
            settings = {**cfg["codex"], **(cfg.get(role, {}) if role != "worker" else {})}
            try:
                resolve_command(settings)
                ok = True
            except (OSError, ValueError):
                ok = False
            checks.append({"check": role + "_command", "ok": ok,
                           "hint": "CLI presence does not verify login, model access or sandbox compatibility." if ok else "Install Codex/Node and check this terminal's PATH or executable setting."})
    return {"version": __version__, "python_version": sys.version.split()[0], "platform": sys.platform,
            "ok": all(c["ok"] for c in checks), "checks": checks,
            "note": "Allowlisted environment checks only; no credentials, prompts, command arguments, account details or absolute paths. No model calls or login checks."}
