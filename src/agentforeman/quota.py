"""Conservative Codex quota classification and model-free reset-time lookup."""
from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from .codex import resolve_command
from .platforms import detached_options, stop_process

DEFAULT_POLICY = {"fallback_seconds": 18000, "minimum_seconds": 60,
                  "max_retries": 8, "probe": True}


class QuotaPause(Exception):
    def __init__(self, job: Path, info: dict, *, scope: str, settings: dict, run_id: str | None = None):
        super().__init__("Codex usage limit reached")
        self.job, self.info, self.scope, self.settings, self.run_id = job, info, scope, settings, run_id


def tail(path: Path, limit: int = 131072) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        return stream.read(limit).decode("utf-8", errors="replace")


def timestamp(value) -> float | None:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo is not None else None
        except ValueError:
            return None
    if type(value) in {int, float} and math.isfinite(value) and 0 < value < 253402214400:
        return float(value)
    return None


def classify(job: Path, result: dict) -> dict | None:
    # Do not interpret timeouts, cancellation, host errors or arbitrary agent text as quota.
    if result.get("timed_out") or result.get("cancelled") or result.get("error"):
        return None
    if result.get("exit_code") in {None, 0}:
        return None
    errors = []
    for line in tail(job / "stdout.log").splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict) and event.get("type") in {"error", "turn.failed"}:
            error = event.get("error", event)
            if isinstance(error, dict):
                errors.append(error)
            elif isinstance(error, str):
                errors.append({"message": error})
    # Text fallback is anchored to the CLI's explicit usage-limit error, never a generic 429.
    for line in tail(job / "stderr.log").splitlines():
        if re.match(r"^(?:error:\s*)?you(?:'|’)ve hit your usage limit\b", line.strip(), re.I):
            errors.append({"message": line.strip()})
    for error in reversed(errors):
        code = error.get("code", error.get("type", ""))
        message = error.get("message", "")
        explicit = isinstance(message, str) and re.match(
            r"^(?:error:\s*)?(?:you(?:'|’)ve hit your usage limit\b|usage limit (?:reached|exceeded)\b)",
            message.strip(), re.I)
        known_code = isinstance(code, str) and code in {"usage_limit_reached", "usage_limit_exceeded", "UsageLimitExceeded"}
        if not known_code and not explicit:
            continue
        reset = None
        for key in ("resets_at", "resetsAt", "reset_at"):
            reset = timestamp(error.get(key))
            if reset is not None:
                break
        if reset is None and isinstance(message, str):
            match = re.search(r"\b\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:Z|[+-]\d\d:\d\d)", message)
            reset = timestamp(match.group()) if match else None
        return {"reason": str(message or code)[:2000], "reported_reset_at": reset,
                "detection": "cli_error"}
    return None


def exhausted_reset(data: dict) -> float | None:
    """Only exhausted buckets for Codex; wait for the latest blocking window."""
    buckets = data.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if isinstance(buckets, dict) else data.get("rateLimits")
    if not isinstance(bucket, dict):
        return None
    resets = []
    for key in ("primary", "secondary"):
        window = bucket.get(key)
        if not isinstance(window, dict):
            continue
        used, reset = window.get("usedPercent"), timestamp(window.get("resetsAt"))
        if type(used) in {int, float} and used >= 100 and reset is not None:
            resets.append(reset)
    return max(resets) if resets else None


def probe_reset(settings: dict, timeout: float = 8) -> float | None:
    """Read-only app-server RPC. Never starts a thread/turn or invokes a model."""
    child = None
    reader = None
    messages = queue.Queue(maxsize=128)
    end = time.monotonic() + timeout
    try:
        child = subprocess.Popen([*resolve_command(settings), "app-server"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True, encoding="utf-8", **detached_options(supervisor=False))

        def receive():
            try:
                while line := child.stdout.readline(1048577):
                    if len(line) > 1048576:
                        break
                    try:
                        messages.put_nowait(json.loads(line))
                    except (ValueError, queue.Full):
                        continue
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=receive, daemon=True)
        reader.start()

        def send(value):
            child.stdin.write(json.dumps(value) + "\n")
            child.stdin.flush()

        def response(identifier):
            while True:
                value = messages.get(timeout=max(0.001, end - time.monotonic()))
                if isinstance(value, dict) and value.get("id") == identifier:
                    if "error" in value:
                        raise ValueError("Quota metadata RPC rejected")
                    return value["result"]
                if time.monotonic() >= end:
                    raise queue.Empty

        send({"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "agentforeman", "version": "0.4.0"}}})
        response(0)
        send({"method": "initialized", "params": {}})
        send({"id": 1, "method": "account/rateLimits/read", "params": {}})
        data = response(1)
        return exhausted_reset(data) if isinstance(data, dict) else None
    except (OSError, ValueError, RuntimeError, KeyError, queue.Empty):
        return None
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    stop_process(child)
                child.wait(timeout=3)
            finally:
                if child.stdin:
                    try:
                        child.stdin.close()
                    except OSError:
                        pass
                if reader:
                    reader.join(timeout=1)
                if child.stdout:
                    child.stdout.close()


def wait_plan(pause: QuotaPause, state: dict) -> dict:
    policy = state["quota_policy"]
    reset = pause.info["reported_reset_at"]
    source = "cli_reset" if reset is not None else "fallback_backoff"
    remaining = state["deadline_at"] - time.time()
    if policy["probe"] and remaining > 2:
        reported = probe_reset(pause.settings, timeout=min(8, remaining - 1))
        if reported is not None:
            reset = max(reset or 0, reported)
            source = "account_rate_limits"
    now = time.time()
    if reset is None:
        reset = now + min(86400, policy["fallback_seconds"] * 2 ** min(state.get("quota_retries", 0), 4))
        source = "fallback_backoff"
    elif reset <= now:
        # Receipt processing may cross the reset. Don't unnecessarily wait another
        # five hours; use bounded short backoff, never a tight retry loop.
        reset = now + min(86400, policy["minimum_seconds"] * 2 ** min(state.get("quota_retries", 0), 4))
        source = "stale_reset_backoff"
    return {**pause.info, "scope": pause.scope, "run_id": pause.run_id,
            "job_name": pause.job.name, "started_at": now,
            "wake_at": max(now + policy["minimum_seconds"], reset + min(5, policy["minimum_seconds"])), "reset_source": source}
