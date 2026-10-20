"""Contract checks for native branches. Actual OS execution belongs to the CI matrix."""
import errno
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psutil

from agentforeman.codex import resolve_command
from agentforeman.files import atomic_text, read_json
from agentforeman.platforms import alive, detached_options, file_lock, identity, stop_process


class PlatformTests(unittest.TestCase):
    def test_windows_receipt_read_retries_transient_denial_with_a_bound(self):
        path = Path("receipt.json")
        with patch("sys.platform", "win32"), patch.object(Path, "read_text", side_effect=[PermissionError("sharing violation"), '{"ok": true}']), patch("agentforeman.files.time.sleep") as sleep:
            self.assertEqual(read_json(path), {"ok": True})
            sleep.assert_called_once_with(0.01)
        with patch("sys.platform", "win32"), patch.object(Path, "read_text", side_effect=PermissionError("permanent")), patch("agentforeman.files.time.monotonic", side_effect=[0, 2]):
            with self.assertRaises(PermissionError):
                read_json(path)
        with patch("sys.platform", "linux"), patch.object(Path, "read_text", side_effect=PermissionError("permanent")), patch("agentforeman.files.time.sleep") as sleep:
            with self.assertRaises(PermissionError):
                read_json(path)
            sleep.assert_not_called()

    def test_windows_flags_do_not_use_posix_sessions(self):
        with patch("sys.platform", "win32"):
            supervisor = detached_options(supervisor=True)
            child = detached_options(supervisor=False)
        self.assertNotIn("start_new_session", supervisor)
        self.assertTrue(supervisor["creationflags"] & 0x8)
        self.assertTrue(child["creationflags"] & 0x08000000)
        self.assertTrue(child["close_fds"])

    def test_posix_detaches_both_supervisor_and_child(self):
        for name in ("linux", "darwin"):
            with patch("sys.platform", name):
                self.assertTrue(detached_options(supervisor=True)["start_new_session"])
                self.assertTrue(detached_options(supervisor=False)["start_new_session"])

    def test_windows_and_mac_identity_use_creation_time_without_os_kill(self):
        process = Mock()
        process.is_running.return_value = True
        process.status.return_value = psutil.STATUS_RUNNING
        process.create_time.return_value = 1234.25
        for platform in ("win32", "darwin"):
            with patch("sys.platform", platform), patch("agentforeman.platforms.psutil.Process", return_value=process), patch("os.kill", side_effect=AssertionError("must not call os.kill")):
                ref = identity(42)
                self.assertEqual(ref, {"pid": 42, "platform": platform, "created_at": 1234.25})
                self.assertTrue(alive(ref))
                self.assertFalse(alive({**ref, "created_at": 1235.0}))

    def test_access_denied_is_conservatively_alive(self):
        with patch("sys.platform", "win32"), patch("agentforeman.platforms.psutil.Process", side_effect=psutil.AccessDenied(42)):
            self.assertTrue(alive({"pid": 42, "platform": "win32", "created_at": 123.0}))

    def test_receipt_from_another_platform_is_not_a_local_process(self):
        with patch("sys.platform", "darwin"):
            self.assertFalse(alive({"pid": os.getpid(), "platform": "win32", "created_at": 123.0}))

    def test_windows_byte_lock_acquires_and_releases_same_region(self):
        module = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=Mock())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.lock"
            with patch.dict(sys.modules, {"msvcrt": module}), patch("sys.platform", "win32"):
                with file_lock(path):
                    self.assertGreaterEqual(path.stat().st_size, 1)
            self.assertEqual([call.args[1:] for call in module.locking.call_args_list], [(2, 1), (0, 1)])

    def test_windows_lock_contention_is_nonblocking(self):
        module = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0,
                                 locking=Mock(side_effect=OSError(errno.EACCES, "owned")))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.lock"
            with patch.dict(sys.modules, {"msvcrt": module}), patch("sys.platform", "win32"):
                with self.assertRaises(BlockingIOError):
                    with file_lock(path):
                        self.fail("must not acquire an owned lock")

    def test_windows_atomic_write_does_not_open_directory_for_fsync(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "metadata.json"
            with patch("sys.platform", "win32"), patch("agentforeman.files.os.fsync", wraps=os.fsync) as fsync:
                atomic_text(path, "中文\n")
                self.assertEqual(fsync.call_count, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), "中文\n")

    def test_windows_stop_terminates_known_descendants_and_root(self):
        root, descendant = Mock(), Mock()
        root.children.return_value = [descendant]
        child = Mock(pid=42)
        child.poll.return_value = None
        with patch("sys.platform", "win32"), patch("agentforeman.platforms.psutil.Process", return_value=root), patch("agentforeman.platforms.psutil.wait_procs", return_value=([], [descendant])):
            stop_process(child)
        root.terminate.assert_called_once()
        descendant.terminate.assert_called_once()
        descendant.kill.assert_called_once()
        child.wait.assert_called_once_with(timeout=5)

    def test_windows_npm_shim_resolves_to_node_argv_without_shell(self):
        with tempfile.TemporaryDirectory(prefix="foreman 中文 ") as temp:
            root = Path(temp)
            shim = root / "codex.cmd"
            shim.write_text("@echo off\n", encoding="utf-8")
            package = root / "node_modules" / "@openai" / "codex"
            (package / "bin").mkdir(parents=True)
            (package / "package.json").write_text(json.dumps({"bin": {"codex": "bin/codex.js"}}))
            script = package / "bin" / "codex.js"
            script.write_text("// fixture only\n")
            with patch("sys.platform", "win32"), patch("agentforeman.codex.shutil.which", return_value="C:/Node/node.exe"):
                command = resolve_command({"executable": str(shim)})
            self.assertEqual(command, ["C:/Node/node.exe", str(script.resolve())])

    def test_unknown_windows_batch_shim_fails_with_configuration_hint(self):
        with tempfile.TemporaryDirectory() as temp:
            shim = Path(temp) / "codex.cmd"
            shim.touch()
            with patch("sys.platform", "win32"):
                with self.assertRaisesRegex(ValueError, "codex.command"):
                    resolve_command({"executable": str(shim)})

    def test_explicit_python_prefix_preserves_arguments(self):
        command = resolve_command({"command": [sys.executable, "path with spaces/worker.py"]})
        self.assertEqual(command[0], os.path.abspath(sys.executable))
        self.assertEqual(command[1:], ["path with spaces/worker.py"])


if __name__ == "__main__":
    unittest.main()
