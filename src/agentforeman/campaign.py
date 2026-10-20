"""Durable, bounded orchestration over the single-experiment executor."""
from __future__ import annotations

import math
from pathlib import Path
import time
import uuid

from . import engine, manager
from .files import atomic_json, atomic_text, digest, inventory, read_json, snapshot
from .job import alive, wait_for_job
from .quota import DEFAULT_POLICY, QuotaPause, classify, wait_plan
from .store import Store, run_lock

PHASES = ["baseline", "decide", "execute", "record", "done"]


def create_campaign(project: Path, goal: str, max_rounds: int, budget_seconds: float,
                    target: float | None = None, *, deadline_at: float | None = None,
                    quota_policy: dict | None = None) -> str:
    created_at = time.time()
    if not goal.strip() or type(max_rounds) is not int or not 1 <= max_rounds <= 100:
        raise ValueError("Provide a goal and max_rounds between 1 and 100")
    if not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("budget_seconds must be positive and finite")
    deadline_at = deadline_at if deadline_at is not None else created_at + budget_seconds
    if not math.isfinite(deadline_at) or deadline_at <= created_at:
        raise ValueError("Deadline must be a future finite timestamp")
    if quota_policy is not None:
        quota_policy = {**DEFAULT_POLICY, **quota_policy}
        for key in ("fallback_seconds", "minimum_seconds"):
            value = quota_policy[key]
            if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                raise ValueError(f"quota {key} must be positive and finite")
        if type(quota_policy["max_retries"]) is not int or not 0 <= quota_policy["max_retries"] <= 100:
            raise ValueError("quota max_retries must be between 0 and 100")
    cfg = engine.load_config(project)
    if target is not None and (not math.isfinite(target) or not cfg["metric"]["minimum"] <= target <= cfg["metric"]["maximum"]):
        raise ValueError("Target must be finite and inside metric bounds")
    store = Store(project)
    try:
        cid = uuid.uuid4().hex[:12]
        root = store.root / "campaigns" / cid
        frozen = root / "project"
        snapshot(project / cfg["source"], frozen / cfg["source"])
        snapshot(project / cfg["trusted"], frozen / cfg["trusted"])
        atomic_text(frozen / "foreman.toml", (project / "foreman.toml").read_text(encoding="utf-8"))
        state = {"id": cid, "goal": goal, "phase": "baseline", "status": "ready",
                 "created_at": created_at, "deadline_at": deadline_at,
                 "max_rounds": max_rounds, "target": target, "config": cfg,
                 "frozen_hashes": inventory(frozen), "baseline_id": uuid.uuid4().hex[:12],
                 "rounds": [], "best": None, "decision_attempt": 0, "pending": None,
                 "quota_policy": quota_policy, "quota_retries": 0, "quota_wait_seconds": 0}
        store.save_campaign(state, "campaign_created")
        write_report(root, state, store)
        return cid
    finally:
        store.close()


def child_state(project: Path, run_id: str) -> dict:
    store = Store(project)
    try:
        return store.get(run_id)
    finally:
        store.close()


def assert_settled(run: Path, state: dict):
    """An uncertain receipt blocks the campaign instead of starting replacement work."""
    name = state.get("active_job")
    if not name:
        return
    job = run / "jobs" / name
    meta = read_json(job / "process.json") if (job / "process.json").exists() else {}
    child = meta.get("child") or (read_json(job / "child.json") if (job / "child.json").exists() else None)
    if alive(meta.get("supervisor")) or alive(child):
        raise RuntimeError("Previous job is still alive; inspect it before continuing campaign")
    if (job / "spec.json").exists() and not (job / "result.json").exists():
        raise RuntimeError("Previous job has no result receipt; campaign blocked for manual inspection")


def execute_child(frozen: Path, state: dict, run_id: str, goal: str, source: Path, mode: str,
                  baseline_source: Path | None = None) -> dict:
    engine.create_run(frozen, goal, run_id=run_id, source=source, mode=mode, baseline_source=baseline_source)
    current = child_state(frozen, run_id)
    if current["status"] not in {"completed", "failed"}:
        try:
            return engine.drive(frozen, run_id, deadline_at=state["deadline_at"])
        except (ValueError, RuntimeError, OSError):
            current = child_state(frozen, run_id)
            if current["status"] != "failed":
                raise
    assert_settled(frozen / ".foreman" / "runs" / run_id, current)
    if state.get("quota_policy") and current["status"] == "failed" and current["phase"] == "codex":
        job = frozen / ".foreman" / "runs" / run_id / "jobs" / current["active_job"]
        if (job / "result.json").exists():
            info = classify(job, read_json(job / "result.json"))
            if info:
                raise QuotaPause(job, info, scope="worker", settings=current["config"]["codex"], run_id=run_id)
    return current


def resume_quota_worker(frozen: Path, wait: dict):
    """Idempotent across a crash between child and campaign state commits."""
    store = Store(frozen)
    run = store.root / "runs" / wait["run_id"]
    try:
        with run_lock(run):
            child = store.get(wait["run_id"])
            key = wait["job_name"]
            if key in child.get("quota_resumed_jobs", []):
                return
            if child["status"] != "failed" or child["phase"] != "codex" or child["active_job"] != key:
                raise ValueError("Quota worker state changed; inspect before recovery")
            assert_settled(run, child)
            engine.assert_integrity(run, child)
            before, after = child["seed_hashes"], inventory(run / "workspace")
            changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
            if changed - set(child["config"]["allowed_edit"]) or set(before) - set(after):
                raise ValueError("Partial worker edits violate the allowlist; automatic quota recovery refused")
            # Preserve the failed handoff and partial code. Existing job logs never move.
            evidence = run / "quota_attempts" / key
            snapshot(run / "workspace", evidence / "workspace")
            if (run / "worker").exists():
                snapshot(run / "worker", evidence / "worker")
            (run / "worker" / "answer.json").unlink(missing_ok=True)
            child.setdefault("quota_resumed_jobs", []).append(key)
            child["retries"]["codex"] = child["retries"].get("codex", 0) + 1
            child.update(status="ready", active_job=None)
            child.pop("error", None)
            store.save(child, "quota_worker_resumed", previous_job=key, partial_hashes=after)
    finally:
        store.close()


def candidate(root: Path, run: Path, child: dict, metric: float, baseline: bool = False) -> dict:
    source = run / ("baseline" if baseline else "candidate") / "code"
    return {"run_id": child["id"], "metric": metric,
            "source": source.relative_to(root).as_posix(), "hashes": inventory(source),
            "archive": (run / "archive.zip").relative_to(root).as_posix(),
            "archive_sha256": digest(run / "archive.zip")}


def observe(root: Path, state: dict, child: dict) -> dict:
    pending = state["pending"]
    run = root / "project" / ".foreman" / "runs" / child["id"]
    row = {"number": len(state["rounds"]) + 1, "run_id": child["id"],
           "action": pending["decision"]["action"], "decision": pending["decision"],
           "status": child["status"], "phase": child["phase"], "repairable": False,
           "source": pending["source"], "source_hashes": pending["source_hashes"],
           "promoted": False}
    if child["status"] == "completed":
        retest = row["action"] == "retest"
        metric = child["baseline_metric" if retest else "candidate_metric"]
        row["metric"] = metric
        row["archive"] = (run / "archive.zip").relative_to(root).as_posix()
        delta = metric - state["best"]["metric"]
        improvement = delta if state["config"]["metric"]["direction"] == "maximize" else -delta
        # A retest measures variability; it never changes the incumbent's score.
        if not retest and improvement > 0 and improvement >= state["config"]["metric"]["min_delta"]:
            state["best"] = candidate(root, run, child, metric)
            row["promoted"] = True
    else:
        row["error"] = child.get("error", "Unknown failure")[:6000]
        active = child.get("active_job")
        stderr = run / "jobs" / str(active) / "stderr.log"
        if stderr.exists():
            with stderr.open("rb") as stream:
                stream.seek(max(0, stderr.stat().st_size - 6000))
                row["stderr_tail"] = stream.read().decode("utf-8", errors="replace")
        # Only validated candidate code can seed a repair. Policy failures are excluded.
        code = run / "candidate" / "code"
        if child["phase"] in {"train", "verify"} and code.exists():
            try:
                row["repairable"] = inventory(code) == child["candidate_hashes"]
                if row["repairable"]:
                    row["repair_source"] = code.relative_to(root).as_posix()
                    row["repair_hashes"] = child["candidate_hashes"]
            except ValueError:
                pass
    return row


def finish(root: Path, state: dict, store: Store, reason: str):
    state["ended_from_phase"] = state["phase"]
    state.update(phase="done", status="completed", stop_reason=reason)
    best, metric = state.get("best"), state["config"]["metric"]
    state["metric_criterion_met"] = None
    if best is not None:
        sign = 1 if metric["direction"] == "maximize" else -1
        delta = sign * (best["metric"] - state["initial_metric"])
        state["metric_criterion_met"] = (sign * (best["metric"] - state["target"]) >= 0 if state["target"] is not None
                                         else delta > 0 and delta >= metric["min_delta"])
    store.save_campaign(state, "campaign_completed", reason=reason)
    write_report(root, state, store)


def write_report(root: Path, state: dict, store: Store):
    atomic_json(root / "report.json", state)
    atomic_json(root / "events.json", store.events("campaign:" + state["id"]))
    best = state.get("best")
    lines = ["# AgentForeman campaign", "", f"Goal: {state['goal']}", "",
             f"Status: {state['status']} / {state['phase']}",
             f"Stop reason: {state.get('stop_reason', '')}",
             f"Best independently verified metric: {best['metric'] if best else 'unavailable'}", "",
             "| Round | Action | Status | Metric | Promoted |", "|---|---|---|---|---|"]
    for row in state["rounds"]:
        lines.append(f"| {row['number']} | {row['action']} | {row['status']} | {row.get('metric', '')} | {row['promoted']} |")
    lines += ["", "Planning/coding CLI logs and decisions are retained under decisions/ and project/.foreman/runs/.",
              "Each completed run has its own hashed replay archive. This summary is not a standalone archive.",
              "Budget is elapsed wall-clock time including pauses, not money, tokens or GPU utilization."]
    from datetime import datetime
    lines += ["", "## Overnight handoff", "",
              f"Deadline: {datetime.fromtimestamp(state['deadline_at']).astimezone().isoformat()}",
              f"Quota resumptions: {state.get('quota_retries', 0)}",
              f"Configured metric criterion met: {state.get('metric_criterion_met', 'not yet assessed')}",
              f"Recorded quota waiting seconds: {state.get('quota_wait_seconds', 0):.1f}",
              f"Completed/failed rounds recorded: {len(state['rounds'])}"]
    if best:
        lines += [f"Best source: {best['source']}", f"Best replay archive: {best['archive']}"]
    if state.get("quota_wait"):
        wait = state["quota_wait"]
        lines += [f"Unfinished work: {wait['scope']} waiting for quota",
                  f"Estimated wake: {datetime.fromtimestamp(wait['wake_at']).astimezone().isoformat()}",
                  f"Reset source: {wait['reset_source']}", f"Quota reason: {wait['reason']}"]
    elif state.get("pending"):
        lines.append(f"Unfinished round: {state['pending']['run_id']} / {state.get('ended_from_phase', state['phase'])}")
    if state.get("error"):
        lines.append(f"Failure requiring inspection: {state['error']}")
    lines.append("Controller completion does not by itself prove the research goal was achieved.")
    atomic_text(root / "report.md", "\n".join(lines) + "\n")


def drive_campaign(project: Path, campaign_id: str, *, until: str | None = None,
                   retry_failed: bool = False) -> dict:
    store = Store(project)
    root = store.root / "campaigns" / campaign_id
    frozen = root / "project"
    try:
        with run_lock(root):
            state = store.get_campaign(campaign_id)
            if state["status"] == "completed":
                write_report(root, state, store)
                return state
            if state.get("quota_wait"):
                if state["status"] == "failed" and not retry_failed:
                    raise ValueError("Quota recovery blocked; inspect error before --retry-failed")
                wait = state["quota_wait"]
                now = time.time()
                if now >= state["deadline_at"] or (root / "stop.request").exists():
                    state["quota_wait_seconds"] += max(0, now - wait["started_at"])
                    finish(root, state, store, "stop_requested" if (root / "stop.request").exists() else "wall_clock_budget")
                    return state
                if now < wait["wake_at"]:
                    write_report(root, state, store)
                    return state
                if wait["scope"] == "worker":
                    try:
                        resume_quota_worker(frozen, wait)
                    except (ValueError, RuntimeError, OSError) as exc:
                        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                        store.save_campaign(state, "quota_recovery_blocked", error=state["error"])
                        write_report(root, state, store)
                        raise
                else:
                    state["decision_attempt"] += 1
                state["quota_retries"] += 1
                state["quota_wait_seconds"] += max(0, now - wait["started_at"])
                state.pop("quota_wait")
                state["status"] = "ready"
                store.save_campaign(state, "quota_woke", previous_wait=wait)
            if state["status"] == "failed":
                if not retry_failed:
                    raise ValueError("Campaign blocked; inspect its error and resume --retry-failed")
                # Retry only a settled planner job; an uncertain child remains blocked.
                if state["phase"] == "decide":
                    job = root / "decisions" / f"{len(state['rounds']):03d}-{state['decision_attempt']:02d}" / "job"
                    if (job / "spec.json").exists():
                        result = job / "result.json"
                        if not result.exists():
                            raise ValueError("Planner has no result receipt; inspect before retrying")
                        meta = read_json(job / "process.json") if (job / "process.json").exists() else {}
                        child = meta.get("child") or (read_json(job / "child.json") if (job / "child.json").exists() else None)
                        if alive(meta.get("supervisor")) or alive(child):
                            raise ValueError("Planner is still alive")
                    state["decision_attempt"] += 1
                state.pop("error", None)
                store.save_campaign(state, "explicit_campaign_retry")
            try:
                while state["phase"] != "done":
                    if (root / "stop.request").exists() and state["phase"] != "record":
                        finish(root, state, store, "stop_requested")
                        break
                    if inventory(frozen) != state["frozen_hashes"]:
                        raise ValueError("Frozen campaign inputs changed")
                    best = state.get("best")
                    if best and (inventory(root / best["source"]) != best["hashes"] or
                                 digest(root / best["archive"]) != best["archive_sha256"]):
                        raise ValueError("Best verified snapshot/archive changed")
                    if until == state["phase"]:
                        state["status"] = "paused"
                        store.save_campaign(state, "campaign_paused")
                        write_report(root, state, store)
                        return state
                    state["status"] = "running"
                    phase = state["phase"]
                    if phase == "baseline":
                        if time.time() >= state["deadline_at"] and not (frozen / ".foreman" / "runs" / state["baseline_id"]).exists():
                            finish(root, state, store, "wall_clock_budget")
                            break
                        child = execute_child(frozen, state, state["baseline_id"], "Campaign baseline evaluation",
                                              frozen / state["config"]["source"], "evaluate")
                        if child["status"] != "completed":
                            if time.time() >= state["deadline_at"]:
                                state["error"] = child.get("error")
                                finish(root, state, store, "wall_clock_budget")
                                break
                            raise ValueError(f"Baseline evaluation failed: {child.get('error')}")
                        run = frozen / ".foreman" / "runs" / child["id"]
                        state["best"] = candidate(root, run, child, child["baseline_metric"], baseline=True)
                        state["initial_metric"] = child["baseline_metric"]
                        state["phase"] = "decide"
                        store.save_campaign(state, "baseline_verified")
                    elif phase == "decide":
                        # Existing planner jobs are drained even after the deadline, then no action is launched.
                        control = root / "decisions" / f"{len(state['rounds']):03d}-{state['decision_attempt']:02d}"
                        job = control / "job"
                        reason = None
                        if time.time() >= state["deadline_at"]:
                            reason = "wall_clock_budget"
                        elif len(state["rounds"]) >= state["max_rounds"]:
                            reason = "max_rounds"
                        elif state["target"] is not None:
                            value = state["best"]["metric"]
                            if (value >= state["target"] if state["config"]["metric"]["direction"] == "maximize" else value <= state["target"]):
                                reason = "target_reached"
                        if reason and not (job / "spec.json").exists():
                            finish(root, state, store, reason)
                            break
                        if (job / "spec.json").exists():
                            spec = read_json(job / "spec.json")
                        else:
                            spec = manager.make_spec(control, control / "context", state, root / state["best"]["source"])
                            remaining = state["deadline_at"] - time.time()
                            if remaining <= 0:
                                finish(root, state, store, "wall_clock_budget")
                                break
                            spec["timeout_seconds"] = min(spec["timeout_seconds"], remaining)
                            spec["deadline_at"] = state["deadline_at"]
                        result = wait_for_job(job, spec)
                        if result["exit_code"] != 0 or result["timed_out"] or result["cancelled"] or result["error"]:
                            if state.get("quota_policy"):
                                info = classify(job, result)
                                if info:
                                    settings = {**state["config"]["codex"], **state["config"].get("manager", {})}
                                    raise QuotaPause(job, info, scope="manager", settings=settings)
                            if time.time() >= state["deadline_at"]:
                                state["error"] = f"Manager interrupted at deadline: {result}"
                                finish(root, state, store, "wall_clock_budget")
                                break
                            raise RuntimeError(f"Manager decision failed: {result}; inspect {job}")
                        decision = manager.read_decision(control, state)
                        store.save_campaign(state, "manager_decision", decision=decision)
                        if reason or time.time() >= state["deadline_at"] or decision["action"] == "stop":
                            finish(root, state, store, reason or ("wall_clock_budget" if time.time() >= state["deadline_at"] else "manager_stop"))
                            break
                        if decision["action"] == "repair":
                            last = state["rounds"][-1]
                            source, hashes = last["repair_source"], last["repair_hashes"]
                        else:
                            source, hashes = state["best"]["source"], state["best"]["hashes"]
                        state["pending"] = {"run_id": uuid.uuid4().hex[:12], "decision": decision,
                                            "source": source, "source_hashes": hashes}
                        state["phase"] = "execute"
                        store.save_campaign(state, "round_allocated", pending=state["pending"])
                    elif phase == "execute":
                        pending = state["pending"]
                        source = root / pending["source"]
                        if inventory(source) != pending["source_hashes"]:
                            raise ValueError("Selected round source changed")
                        if time.time() >= state["deadline_at"] and not (frozen / ".foreman" / "runs" / pending["run_id"]).exists():
                            finish(root, state, store, "wall_clock_budget")
                            break
                        decision = pending["decision"]
                        goal = state["goal"] + "\nManager instruction: " + decision["instruction"]
                        execute_child(frozen, state, pending["run_id"], goal, source,
                                      "evaluate" if decision["action"] == "retest" else "experiment",
                                      baseline_source=root / state["best"]["source"] if decision["action"] == "repair" else None)
                        state["phase"] = "record"
                        store.save_campaign(state, "round_executed")
                    elif phase == "record":
                        child = child_state(frozen, state["pending"]["run_id"])
                        state["rounds"].append(observe(root, state, child))
                        state.update(phase="decide", pending=None, decision_attempt=0)
                        store.save_campaign(state, "round_recorded", observation=state["rounds"][-1])
                        write_report(root, state, store)
                return state
            except QuotaPause as pause:
                if state["quota_retries"] >= state["quota_policy"]["max_retries"]:
                    state["error"] = pause.info["reason"]
                    finish(root, state, store, "quota_retry_limit")
                    return state
                state["quota_wait"] = wait_plan(pause, state)
                state["status"] = "waiting_quota"
                store.save_campaign(state, "quota_waiting", wait=state["quota_wait"])
                write_report(root, state, store)
                print(f"[{campaign_id}] waiting_quota until {state['quota_wait']['wake_at']} (no model calls)", flush=True)
                return state
            except KeyboardInterrupt:
                state["status"] = "paused"
                store.save_campaign(state, "campaign_interrupted")
                raise
            except Exception as exc:
                state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                store.save_campaign(state, "campaign_failed", error=state["error"])
                write_report(root, state, store)
                raise
    finally:
        store.close()
