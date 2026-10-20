"""Model-free background controller for a deadline-bound campaign."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import os
import subprocess
import sys
import threading
import time
import uuid

from .campaign import drive_campaign
from .files import atomic_json, read_json
from .platforms import alive, detached_options, file_lock, identity
from .store import Store


def parse_deadline(value: str) -> float:
    """HH:MM means the next local occurrence; full ISO requires an explicit zone."""
    try:
        if len(value) == 5 and value[2] == ":":
            hour, minute = map(int, value.split(":"))
            now = datetime.now()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target.astimezone().timestamp()
        target = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if target.tzinfo is None:
            raise ValueError("Full datetime needs a timezone")
        stamp = target.timestamp()
        if stamp <= time.time():
            raise ValueError("Deadline is in the past")
        return stamp
    except (ValueError, OverflowError):
        raise ValueError("Deadline must be the next local HH:MM or a future ISO time with offset, e.g. 2026-10-21T08:00:00+08:00") from None


def serve(project: Path, campaign_id: str, token: str | None = None, *, retry_failed: bool = False):
    root = project / ".foreman" / "campaigns" / campaign_id
    root.mkdir(parents=True, exist_ok=True)
    try:
        with file_lock(root / "runner.lock"):
            receipt = {"process": identity(os.getpid()), "token": token, "started_at": time.time(), "status": "running"}
            atomic_json(root / "runner.json", receipt)
            try:
                while True:
                    state = drive_campaign(project, campaign_id, retry_failed=retry_failed)
                    retry_failed = False
                    if state["status"] != "waiting_quota":
                        receipt["status"] = state["status"]
                        return state
                    wake = min(state["quota_wait"]["wake_at"], state["deadline_at"])
                    print(f"Waiting without model calls until {datetime.fromtimestamp(wake).astimezone().isoformat()}", flush=True)
                    while time.time() < wake and not (root / "stop.request").exists():
                        time.sleep(min(1, max(0, wake - time.time())))
            except KeyboardInterrupt:
                receipt["status"] = "paused"
                raise
            except Exception as exc:
                receipt.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                raise
            finally:
                receipt["finished_at"] = time.time()
                atomic_json(root / "runner.json", receipt)
    except BlockingIOError:
        raise ValueError("An overnight controller already owns this campaign") from None


def launch(project: Path, campaign_id: str, *, retry_failed: bool = False) -> dict:
    store = Store(project)
    try:
        state = store.get_campaign(campaign_id)
        root = store.root / "campaigns" / campaign_id
    finally:
        store.close()
    if state["status"] == "completed" or (state["status"] == "failed" and not retry_failed):
        raise ValueError("Campaign is completed or failed; inspect status before launching")
    with file_lock(root / "runner-launch.lock"):
        path = root / "runner.json"
        previous = read_json(path) if path.exists() else {}
        if alive(previous.get("process")):
            return previous
        token = uuid.uuid4().hex
        with (root / "controller.log").open("ab") as log:
            argv = [sys.executable, "-m", "agentforeman.overnight", str(project.resolve()), campaign_id, token]
            if retry_failed:
                argv.append("--retry-failed")
            child = subprocess.Popen(argv,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     **detached_options(supervisor=True))
        # Reap when embedded in a long-lived caller; daemon threads don't keep the
        # submission CLI open and the detached controller outlives that CLI.
        threading.Thread(target=child.wait, daemon=True).start()
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if path.exists():
                receipt = read_json(path)
                if receipt.get("token") == token:
                    return receipt
            if child.poll() is not None:
                break
            time.sleep(0.05)
        raise RuntimeError(f"Controller did not acknowledge startup; inspect {root / 'controller.log'} before retrying")


if __name__ == "__main__":
    serve(Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None,
          retry_failed="--retry-failed" in sys.argv[4:])
