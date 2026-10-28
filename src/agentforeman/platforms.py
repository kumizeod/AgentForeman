"""Small native platform boundary: locks, process birth identity and lifecycle.

Linux retains /proc identities (including namespace-aware self receipts).
macOS and Windows use psutil's native PID + creation time. No Windows os.kill(0).
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import psutil


SUPPORTED = {"linux", "darwin", "win32"}


@contextmanager
def file_lock(path: Path):
    """Nonblocking OS lock, automatically released on process exit."""
    with path.open("a+b") as stream:
        if sys.platform == "win32":
            import msvcrt
            # Lock one persistent byte; append mode prevents concurrent truncation.
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise BlockingIOError("Lock is already owned") from exc
                raise
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def identity(pid: int) -> dict | None:
    try:
        if sys.platform == "linux":
            os.kill(pid, 0)
            text = Path("/proc/self/stat" if pid == os.getpid() else f"/proc/{pid}/stat").read_text()
            fields = text[text.rfind(")") + 2:].split()
            if fields[0] == "Z":
                return None
            return {"pid": pid, "platform": "linux", "proc_pid": int(text.split(" ", 1)[0]),
                    "start_ticks": fields[19],
                    "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return {"pid": pid, "platform": sys.platform, "created_at": process.create_time()}
    except (OSError, IndexError, psutil.Error):
        return None


def alive(ref: dict | None) -> bool:
    if not ref:
        return False
    try:
        # v0.1.0 Linux receipts had no explicit platform field.
        backend = ref.get("platform", "linux" if "start_ticks" in ref else None)
        if backend != sys.platform:
            return False
        if backend == "linux":
            os.kill(ref["pid"], 0)
            text = Path(f"/proc/{ref.get('proc_pid', ref['pid'])}/stat").read_text()
            fields = text[text.rfind(")") + 2:].split()
            return (fields[0] != "Z" and fields[19] == ref["start_ticks"] and
                    Path("/proc/sys/kernel/random/boot_id").read_text().strip() == ref["boot_id"])
        process = psutil.Process(ref["pid"])
        return (process.is_running() and process.status() != psutil.STATUS_ZOMBIE
                and process.create_time() == ref["created_at"])
    except psutil.AccessDenied:
        # Conservatively refuse automatic replay when process identity is unavailable.
        return True
    except (OSError, IndexError, KeyError, psutil.Error):
        return False


def detached_options(*, supervisor: bool) -> dict:
    if sys.platform == "win32":
        # DETACHED_PROCESS keeps the supervisor independent of console Ctrl-C.
        # Commands have no visible console, and never inherit the manager's group.
        flags = 0x00000200 | (0x00000008 if supervisor else 0x08000000)
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def stop_process(child: subprocess.Popen):
    """Stop a POSIX process group or the known Windows descendant tree."""
    if child.poll() is not None:
        return
    if sys.platform == "win32":
        denied = []
        try:
            root = psutil.Process(child.pid)
            try:
                processes = root.children(recursive=True)[::-1] + [root]
            except psutil.AccessDenied:
                processes = [root]
                denied.append("enumerating descendants")
        except psutil.NoSuchProcess:
            return
        for process in processes:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                denied.append(f"terminating pid {process.pid}")
        _, running = psutil.wait_procs(processes, timeout=3)
        for process in running:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                denied.append(f"killing pid {process.pid}")
        child.wait(timeout=5)
        if denied:
            raise RuntimeError("Process-tree cleanup incomplete: " + "; ".join(denied))
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    # Keep the root PID reserved until the whole group has been killed. Reaping
    # a fast-exiting parent first can leave a SIGTERM-ignoring grandchild alive.
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        try:
            if psutil.Process(child.pid).status()==psutil.STATUS_ZOMBIE:break
        except psutil.NoSuchProcess:break
        time.sleep(.05)
    try:os.killpg(child.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    child.wait(timeout=5)
