"""Loopback-only control panel, using the existing durable campaign executor."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.resources
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from urllib.parse import urlsplit, parse_qs
import webbrowser

from . import __version__
from .campaign import create_campaign
from .engine import load_config
from .files import atomic_text, digest
from .observe import diagnostics, documents, monitor_snapshot, statistics
from .overnight import launch, parse_deadline
from .quota import tail
from .usage import usage_snapshot

ASSETS = {
    "index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8", "upstream/LICENSE": "text/plain; charset=utf-8",
    **{"upstream/" + name + ".css": "text/css; charset=utf-8" for name in
       ("light", "dark", "base", "layout", "summary", "accounts", "account-details")},
}
ID = re.compile(r"^[0-9a-f]{12}$")



class ControlPanel(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, project: Path, port: int = 8765):
        self.project = project.expanduser().resolve()
        load_config(self.project)
        self.token = secrets.token_urlsafe(32)
        self.actions = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.url = self.origin + "/#" + self.token

    def campaign(self, cid):
        if not ID.fullmatch(cid):
            raise ValueError("Invalid campaign id")
        # Read-only lookup; opening an empty console never creates a state DB.
        state = next((s for s in documents(self.project, "campaigns") if s["id"] == cid), None)
        if state is None:
            raise ValueError("Unknown campaign")
        return state, self.project / ".foreman" / "campaigns" / cid

    def overview(self):
        result = monitor_snapshot(self.project)
        goals = {s["id"]: s["goal"] for s in documents(self.project, "campaigns")}
        for row in result["campaigns"]:
            row["goal"] = goals[row["id"]]
        result.update(version=__version__, project_name=self.project.name)
        return result

    def detail(self, cid):
        state, root = self.campaign(cid)
        keys = ("id", "goal", "status", "phase", "created_at", "deadline_at", "max_rounds", "target",
                "initial_metric", "stop_reason", "metric_criterion_met", "quota_retries", "quota_wait_seconds", "error", "review_policy", "discussion", "discussions")
        result = {k: state.get(k) for k in keys}
        result["best_metric"] = (state.get("best") or {}).get("metric")
        result["has_archive"] = bool((state.get("best") or {}).get("archive"))
        result["rounds"] = [{k: r.get(k) for k in
            ("number", "run_id", "action", "status", "phase", "metric", "promoted", "error", "decision")} for r in state["rounds"]]
        wait = state.get("quota_wait") or {}
        result["quota_wait"] = {k: wait.get(k) for k in ("scope", "wake_at", "reason", "reset_source")} if wait else None
        dbpath = self.project / ".foreman" / "state.sqlite3"
        db = sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            events = db.execute("SELECT sequence,timestamp,kind FROM events WHERE run_id=? ORDER BY sequence DESC LIMIT 60", ("campaign:" + cid,))
            result["events"] = [dict(sequence=r[0], timestamp=r[1], kind=r[2]) for r in events][::-1]
        finally:
            db.close()
        result["report"] = (root / "report.md").read_text(encoding="utf-8") if (root / "report.md").exists() else "Report not ready"
        return result

    def logs(self, cid, kind):
        state, root = self.campaign(cid)
        if kind == "controller":
            paths = [root / "controller.log"]
        elif kind == "manager":
            folders = sorted((root / "decisions").glob("*/job"))
            paths = [folders[-1] / name for name in ("stdout.log", "stderr.log")] if folders else []
        elif kind in {"reviewer", "revision"}:
            prefix = "review" if kind == "reviewer" else "revise"
            folders = sorted((root / "discussions").glob(f"*/{prefix}-*/job"))
            paths = [folders[-1] / name for name in ("stdout.log", "stderr.log")] if folders else []
        elif kind == "worker":
            rid = (state.get("pending") or {}).get("run_id") or (state["rounds"][-1]["run_id"] if state["rounds"] else None)
            folders = sorted((root / "project" / ".foreman" / "runs" / rid / "jobs").glob("codex-*")) if rid else []
            paths = [folders[-1] / name for name in ("stdout.log", "stderr.log")] if folders else []
        else:
            raise ValueError("Unknown log kind")
        # Logs are trusted-local task output, never HTML and never arbitrary paths.
        content = []
        for path in paths:
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Log path escaped campaign")
            content.append(f"--- {path.name} (last 64 KiB) ---\n" + tail(path, 65536))
        return {"kind": kind, "text": "\n".join(content) or "No log output yet.", "bounded": True}

    def submit(self, body):
        goal = body.get("goal")
        deadline = body.get("deadline", "08:00")
        rounds, target = body.get("max_rounds", 5), body.get("target")
        preflight = body.get("preflight", False)
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 8000:
            raise ValueError("Goal must contain 1–8000 characters")
        if not isinstance(deadline, str) or len(deadline) > 64:
            raise ValueError("Invalid deadline")
        if type(rounds) is not int or not 1 <= rounds <= 100:
            raise ValueError("max_rounds must be between 1 and 100")
        if target is not None and (type(target) not in {int, float} or not math.isfinite(target)):
            raise ValueError("Target must be a finite number")
        if type(preflight) is not bool:
            raise ValueError("Preflight must be boolean")
        cid = create_campaign(self.project, goal.strip(), rounds, 86400, target,
                              deadline_at=parse_deadline(deadline), quota_policy={"preflight": preflight},
                              review_policy={"enabled": body.get("review", False), "max_reviews": body.get("max_reviews", 2)})
        # Return the durable id even if startup is uncertain; do not silently resubmit.
        try:
            launch(self.project, cid)
            return {"id": cid, "launched": True}
        except (OSError, ValueError, RuntimeError) as exc:
            return {"id": cid, "launched": False, "error": str(exc)}


class Handler(BaseHTTPRequestHandler):
    server: ControlPanel
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass  # No capabilities, local paths or task output in HTTP access logs.

    def send(self, status, data, mime="application/json; charset=utf-8", *, filename=None):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if mime.startswith("application/json") else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def trusted_host(self):
        return self.headers.get("Host") == self.server.origin.removeprefix("http://")

    def authorized(self):
        token = self.headers.get("X-Foreman-Token", "")
        return self.trusted_host() and hmac.compare_digest(token.encode("utf-8"), self.server.token.encode("utf-8"))

    def do_GET(self):
        try:
            if not self.trusted_host():
                return self.send(403, {"error": "Invalid local host"})
            parsed = urlsplit(self.path)
            path = parsed.path
            if not path.startswith("/api/"):
                asset = "index.html" if path == "/" else path.removeprefix("/static/") if path.startswith("/static/") else None
                if asset not in ASSETS:
                    return self.send(404, {"error": "Not found"})
                resource = importlib.resources.files("agentforeman").joinpath("web", *asset.split("/"))
                return self.send(200, resource.read_bytes(), ASSETS[asset])
            if not self.authorized():
                return self.send(403, {"error": "Open the complete console URL printed in your terminal."})
            if path == "/api/overview":
                result = self.server.overview()
            elif path == "/api/stats":
                result = statistics(self.server.project)
            elif path == "/api/diagnostics":
                result = diagnostics(self.server.project)
            elif match := re.fullmatch(r"/api/campaigns/([0-9a-f]{12})(?:/(logs|archive))?", path):
                cid, action = match.groups()
                if action == "logs":
                    result = self.server.logs(cid, parse_qs(parsed.query).get("kind", ["controller"])[0])
                elif action == "archive":
                    state, root = self.server.campaign(cid)
                    best = state.get("best") or {}
                    if not best.get("archive"):
                        raise ValueError("No verified archive yet")
                    archive = (root / best["archive"]).resolve()
                    if not archive.is_relative_to(root.resolve()) or not archive.is_file() or digest(archive) != best.get("archive_sha256"):
                        raise ValueError("Archive path or integrity check failed")
                    # Stream large real experiment archives rather than load them into memory.
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(archive.stat().st_size))
                    self.send_header("Content-Disposition", f'attachment; filename="{cid}-best.zip"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    with archive.open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            self.wfile.write(chunk)
                    return
                else:
                    result = self.server.detail(cid)
            else:
                return self.send(404, {"error": "Unknown API route"})
            self.send(200, result)
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            self.send(400, {"error": str(exc)})

    def do_POST(self):
        # Capability + exact Host + same-origin JSON rejects cross-site form/fetch
        # and DNS-rebinding access to the local task execution surface.
        if not self.authorized() or self.headers.get("Origin") != self.server.origin:
            self.close_connection = True
            return self.send(403, {"error": "Unauthorized local action"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768 or self.headers.get_content_type() != "application/json":
                self.close_connection = True
                return self.send(400, {"error": "Expected JSON body, maximum 32 KiB"})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            with self.server.actions:
                path = urlsplit(self.path).path
                if path == "/api/campaigns":
                    return self.send(201, self.server.submit(body))
                if path == "/api/usage":
                    role = body.get("role", "worker")
                    if not isinstance(role, str) or role not in {"worker", "manager", "reviewer"}:
                        raise ValueError("Invalid role")
                    return self.send(200, usage_snapshot(self.server.project, role, refresh=True))
                match = re.fullmatch(r"/api/campaigns/([0-9a-f]{12})/(stop|resume)", path)
                if not match:
                    return self.send(404, {"error": "Unknown action"})
                cid, action = match.groups()
                state, root = self.server.campaign(cid)
                if action == "stop":
                    if state["status"] == "completed":
                        raise ValueError("Campaign already completed")
                    atomic_text(root / "stop.request", "stop\n")
                    return self.send(200, {"id": cid, "stop_requested": True})
                retry = body.get("retry_failed", False)
                if type(retry) is not bool:
                    raise ValueError("retry_failed must be boolean")
                receipt = launch(self.server.project, cid, retry_failed=retry)
                return self.send(200, {"id": cid, "controller": {k: receipt.get(k) for k in ("status", "started_at")}})
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            self.send(400, {"error": str(exc)})


def run_ui(project: Path, port=8765, *, open_browser=True):
    if not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    with ControlPanel(project, port) as server:
        print(f"AgentForeman console: {server.url}", flush=True)
        print("Local access only. Keep this URL private. Ctrl-C closes the panel; detached experiments continue.", flush=True)
        if open_browser:
            threading.Timer(0.2, webbrowser.open, args=(server.url,)).start()
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
