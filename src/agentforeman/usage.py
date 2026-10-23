"""Sanitized quota snapshots. Cached views never launch Codex or read credentials."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import time

from .engine import load_config
from .files import atomic_json, read_json
from .platforms import file_lock
from .quota import probe_limits, timestamp


def settings_for(project: Path, role: str) -> dict:
    if role not in {"worker", "manager", "reviewer"}:
        raise ValueError("Role must be worker, manager or reviewer")
    cfg = load_config(project)
    return {**cfg["codex"], **(cfg.get(role, {}) if role != "worker" else {})}


def normalize(data: dict) -> dict:
    buckets = data.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if isinstance(buckets, dict) else data.get("rateLimits")
    if not isinstance(bucket, dict) or bucket.get("limitId", "codex") != "codex":
        raise ValueError("Codex bucket unavailable")
    windows = []
    for name in ("primary", "secondary"):
        raw = bucket.get(name)
        if not isinstance(raw, dict):
            continue
        used = raw.get("usedPercent")
        if type(used) not in {int, float} or not math.isfinite(used) or not 0 <= used <= 100:
            continue
        duration = raw.get("windowDurationMins")
        if type(duration) not in {int, float} or not math.isfinite(duration) or duration <= 0:
            duration = None
        windows.append({"name": name, "used_percent": used, "remaining_percent": 100 - used,
                        "duration_minutes": duration, "resets_at": timestamp(raw.get("resetsAt"))})
    if not windows:
        raise ValueError("Codex windows unavailable")
    # Strict allowlist: no account id, email, tokens, credit details or arbitrary text.
    plan = bucket.get("planType")
    if plan not in {"free", "go", "plus", "pro", "team", "business", "enterprise", "edu"}:
        plan = None
    return {"windows": windows, "plan_type": plan}


def usage_snapshot(project: Path, role: str = "worker", *, refresh: bool = False,
                   max_age: float = 300, timeout: float = 8) -> dict:
    if not math.isfinite(max_age) or max_age <= 0:
        raise ValueError("max_age must be positive and finite")
    settings = settings_for(project, role)
    # An opaque source binding avoids showing a previous command/provider's cache.
    binding = hashlib.sha256(json.dumps({"settings": settings,
        "codex_home": os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))}, sort_keys=True).encode()).hexdigest()
    path = project / ".foreman" / f"usage-{role}.json"

    def cached():
        try:
            value = read_json(path)
            if not isinstance(value, dict) or value.get("binding") != binding:
                return {}
            return value
        except (OSError, ValueError):
            return {}

    def view(value):
        now = time.time()
        fetched = value.get("fetched_at")
        age = max(0, now - fetched) if type(fetched) in {int, float} else None
        result = {k: value.get(k) for k in ("fetched_at", "attempted_at", "error", "windows", "plan_type")}
        result.update(role=role, age_seconds=age, available=age is not None,
                      stale=age is None or age > max_age or bool(value.get("error")),
                      cached=not refresh, source="codex_app_server")
        # Past reset times never imply a refreshed quota percentage.
        result["reset_passed"] = any(w.get("resets_at") is not None and w["resets_at"] <= now
                                     for w in value.get("windows", []))
        result["stale"] = result["stale"] or result["reset_passed"]
        return result

    if not refresh:
        return view(cached())
    # Serialize refreshes and avoid competing writes with stale fallback values.
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix(".lock")):
        value = cached()
        result = probe_limits(settings, timeout)
        error = result.get("error")
        if result["ok"]:
            try:
                snapshot = normalize(result["data"])
            except (ValueError, TypeError):
                error = "invalid_response"
            else:
                value = {**snapshot, "fetched_at": time.time()}
        value.update(binding=binding, attempted_at=time.time(), error=error)
        atomic_json(path, value)
        return view(value)
