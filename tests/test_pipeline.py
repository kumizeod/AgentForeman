from __future__ import annotations

from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from agentforeman.cli import init_demo
from agentforeman.codex import make_spec
from agentforeman.engine import create_run, drive, load_config, read_verdict
from agentforeman.files import atomic_json, read_json
from agentforeman.job import alive, launch, wait_for_job
from agentforeman.report import verify_archive
from agentforeman.store import Store, run_lock


@contextmanager
def quiet():
    with patch("sys.stdout", new_callable=io.StringIO):
        yield


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="foreman test ")
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        self.worker = self.base / "fixture-codex"
        shutil.copy2(Path(__file__).with_name("fixture_codex.py"), self.worker)
        self.worker.chmod(0o755)
        with quiet():
            init_demo(self.project)
        cfg = self.project / "foreman.toml"
        cfg.write_text(cfg.read_text().replace('executable = "codex"', f"executable = {json.dumps(str(self.worker))}"))
        self.env = patch.dict(os.environ, {"FOREMAN_TEST_MODE": "improve"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def state(self, run_id):
        store = Store(self.project)
        try:
            return store.get(run_id)
        finally:
            store.close()

    def root(self, run_id):
        return self.project / ".foreman" / "runs" / run_id

    def create(self):
        return create_run(self.project, "Improve validation accuracy with one bounded candidate")

    def execute(self, run_id, **kwargs):
        with quiet():
            return drive(self.project, run_id, **kwargs)

    def wait_path(self, path, timeout=15):
        end = time.monotonic() + timeout
        while not path.exists():
            if time.monotonic() > end:
                self.fail(f"Timed out waiting for {path}")
            time.sleep(0.05)

    def test_real_training_verification_archive_and_replay(self):
        run_id = self.create()
        state = self.execute(run_id)
        root = self.root(run_id)
        self.assertEqual(state["status"], "completed")
        self.assertTrue(state["objective_met"])
        self.assertGreater(state["candidate_metric"], state["baseline_metric"])
        self.assertTrue(verify_archive(root / "archive.zip")["valid"])
        extracted = self.base / "extracted archive"
        with zipfile.ZipFile(root / "archive.zip") as archive:
            archive.extractall(extracted)
        result = subprocess.run([sys.executable, "reproduce.py"], cwd=extracted, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        replay = read_json(extracted / "reproduced" / "candidate" / "verification.json")
        self.assertEqual(replay["metric"], state["candidate_metric"])

    def test_completed_run_resume_is_noop(self):
        run_id = self.create()
        self.execute(run_id)
        root = self.root(run_id)
        before = {str(p): p.stat().st_mtime_ns for p in root.glob("jobs/*/result.json")}
        self.execute(run_id)
        after = {str(p): p.stat().st_mtime_ns for p in root.glob("jobs/*/result.json")}
        self.assertEqual(before, after)

    def test_pause_before_codex_and_resume(self):
        run_id = self.create()
        state = self.execute(run_id, until="codex")
        self.assertEqual(state["status"], "paused")
        self.assertIn("baseline_metric", state)
        self.assertFalse((self.root(run_id) / "worker").exists())
        self.assertEqual(self.execute(run_id)["status"], "completed")

    def test_manager_sigkill_during_training_does_not_duplicate_job(self):
        # Slow the fixed entry point to create a reliable kill/reattach window.
        train = self.project / "workspace" / "train.py"
        train.write_text("import time\ntime.sleep(2)\n" + train.read_text())
        run_id = self.create()
        self.execute(run_id, until="train")
        root = self.root(run_id)
        out = (self.base / "manager.log").open("wb")
        manager = subprocess.Popen([sys.executable, "-m", "agentforeman", "resume", run_id,
                                    "--project", str(self.project)], stdout=out, stderr=out)
        try:
            meta = root / "jobs" / "train-00" / "process.json"
            self.wait_path(meta)
            child = None
            for _ in range(100):
                child = read_json(meta).get("child")
                if child:
                    break
                time.sleep(0.02)
            self.assertTrue(alive(child))
            manager.kill()
            manager.wait(timeout=5)
            self.assertTrue(alive(child))
            self.assertEqual(self.execute(run_id)["status"], "completed")
            self.assertEqual(len(list(root.glob("jobs/train-*"))), 1)
            self.assertEqual(read_json(meta)["child"], child)
        finally:
            if manager.poll() is None:
                manager.kill()
                manager.wait()
            out.close()

    def test_only_one_manager_can_own_run(self):
        run_id = self.create()
        with run_lock(self.root(run_id)):
            with self.assertRaisesRegex(ValueError, "Another manager"):
                self.execute(run_id)

    def test_protected_edit_is_rejected(self):
        os.environ["FOREMAN_TEST_MODE"] = "forbidden"
        run_id = self.create()
        with self.assertRaisesRegex(ValueError, "protected"):
            self.execute(run_id)
        self.assertEqual(self.state(run_id)["status"], "failed")
        self.assertFalse((self.root(run_id) / "jobs" / "train-00").exists())

    def test_noop_worker_is_rejected(self):
        os.environ["FOREMAN_TEST_MODE"] = "nochange"
        with self.assertRaisesRegex(ValueError, "no source changes"):
            self.execute(self.create())

    def test_symlink_edit_is_rejected(self):
        os.environ["FOREMAN_TEST_MODE"] = "symlink"
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            self.execute(self.create())

    def test_untrusted_metrics_are_ignored(self):
        os.environ["FOREMAN_TEST_MODE"] = "fake_metrics"
        state = self.execute(self.create())
        self.assertEqual(state["candidate_metric"], 0.5)
        self.assertFalse(state["objective_met"])
        self.assertEqual(state["status"], "completed")

    def test_changed_verifier_is_rejected(self):
        run_id = self.create()
        (self.root(run_id) / "trusted" / "verify.py").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "verifier/data"):
            self.execute(run_id)

    def test_training_failure_is_not_a_success(self):
        os.environ["FOREMAN_TEST_MODE"] = "bad_training"
        run_id = self.create()
        with self.assertRaisesRegex(RuntimeError, "failed"):
            self.execute(run_id)
        self.assertEqual(self.state(run_id)["phase"], "train")
        self.assertFalse((self.root(run_id) / "archive.zip").exists())

    def test_worker_failure_requires_explicit_retry(self):
        os.environ["FOREMAN_TEST_MODE"] = "worker_failure"
        run_id = self.create()
        with self.assertRaises(RuntimeError):
            self.execute(run_id)
        with self.assertRaisesRegex(ValueError, "Run failed"):
            self.execute(run_id)
        os.environ["FOREMAN_TEST_MODE"] = "improve"
        state = self.execute(run_id, retry=True)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["retries"]["codex"], 1)
        self.assertTrue((self.root(run_id) / "jobs" / "codex-01" / "result.json").exists())

    def test_missing_codex_can_be_installed_then_retried(self):
        self.worker.unlink()
        run_id = self.create()
        with self.assertRaisesRegex(ValueError, "Codex CLI not found"):
            self.execute(run_id)
        shutil.copy2(Path(__file__).with_name("fixture_codex.py"), self.worker)
        self.worker.chmod(0o755)
        self.assertEqual(self.execute(run_id, retry=True)["status"], "completed")

    def test_codex_argv_uses_stdin_and_no_shell(self):
        run_id = self.create()
        state = self.execute(run_id, until="codex")
        spec = make_spec(self.root(run_id), state, state["config"])
        self.assertEqual(spec["argv"][-1], "-")
        self.assertIn("workspace-write", spec["argv"])
        self.assertIn("--output-schema", spec["argv"])
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", spec["argv"])

    def test_archive_detects_tampering(self):
        run_id = self.create()
        self.execute(run_id)
        original = self.root(run_id) / "archive.zip"
        changed = self.base / "tampered.zip"
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(changed, "w") as target:
            for name in source.namelist():
                target.writestr(name, b"changed" if name == "report.md" else source.read(name))
        with self.assertRaisesRegex(ValueError, "Checksum"):
            verify_archive(changed)

    def test_missing_or_invalid_checkpoint_is_rejected_by_external_verifier(self):
        import numpy as np
        run_id = self.create()
        root = self.root(run_id)
        verifier = root / "trusted" / "verify.py"
        output = self.base / "verdict.json"
        checkpoint = self.base / "invalid.npz"
        for content in ({"w1": np.zeros((2, 2))},
                        {"w1": np.full((2, 2), np.nan), "b1": np.zeros(2),
                         "w2": np.zeros((2, 1)), "b2": np.zeros(1)}):
            np.savez(checkpoint, **content)
            result = subprocess.run([sys.executable, str(verifier), "--checkpoint", str(checkpoint),
                                     "--data", str(root / "trusted" / "validation.csv"), "--output", str(output)],
                                    capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(read_json(output)["valid"])
        checkpoint.unlink()
        result = subprocess.run([sys.executable, str(verifier), "--checkpoint", str(checkpoint),
                                 "--data", str(root / "trusted" / "validation.csv"), "--output", str(output)],
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)

    def test_verifier_protocol_rejects_nan_wrong_metric_and_false_validity(self):
        run_id = self.create()
        state = self.state(run_id)
        output = self.root(run_id) / "baseline" / "artifacts" / "verification.json"
        for value in (
            {"valid": True, "metric_name": "accuracy", "metric": float("nan")},
            {"valid": True, "metric_name": "accuracy", "metric": 2.0},
            {"valid": True, "metric_name": "accuracy", "metric": True},
            {"valid": True, "metric_name": "loss", "metric": 0.5},
            {"valid": False, "metric_name": "accuracy", "metric": 0.5},
            [],
        ):
            output.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                read_verdict(self.root(run_id), state, baseline=True)

    def test_completed_result_before_state_commit_does_not_repeat_training(self):
        run_id = self.create()
        self.execute(run_id, until="baseline_verify")
        root = self.root(run_id)
        receipt = root / "jobs" / "baseline_train-00" / "result.json"
        before = receipt.read_bytes()
        # Simulate a manager dying after the result receipt but before transition.
        store = Store(self.project)
        try:
            state = store.get(run_id)
            state.update(phase="baseline_train", status="running", active_job="baseline_train-00")
            store.save(state, "test_simulate_commit_gap")
        finally:
            store.close()
        self.execute(run_id, until="codex")
        self.assertEqual(receipt.read_bytes(), before)
        self.assertEqual(len(list(root.glob("jobs/baseline_train-*"))), 1)

    def test_existing_project_is_not_overwritten(self):
        old = (self.project / "foreman.toml").read_bytes()
        with self.assertRaisesRegex(ValueError, "empty directory"):
            init_demo(self.project)
        self.assertEqual((self.project / "foreman.toml").read_bytes(), old)

    def test_invalid_timeout_configuration_is_rejected(self):
        cfg = self.project / "foreman.toml"
        cfg.write_text(cfg.read_text().replace("timeout_seconds = 120", "timeout_seconds = -1"))
        with self.assertRaisesRegex(ValueError, "positive finite"):
            load_config(self.project)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "job"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def spec(self, code, timeout=5):
        return {"argv": [sys.executable, "-c", code], "cwd": str(self.root), "timeout_seconds": timeout, "env": {}}

    def test_timeout_kills_real_process(self):
        result = wait_for_job(self.root, self.spec("import time; time.sleep(10)", timeout=0.3))
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["exit_code"], 0)
        self.assertFalse(alive(read_json(self.root / "process.json")["child"]))

    def test_supervisor_launch_race_runs_command_once(self):
        spec = self.spec("from pathlib import Path; import time; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); time.sleep(.3)")
        atomic_json(self.root / "spec.json", spec)
        wrappers = [launch(self.root), launch(self.root), launch(self.root)]
        try:
            result = wait_for_job(self.root, spec)
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual((self.root / "count").read_text(), "x")
        finally:
            for p in wrappers:
                p.wait(timeout=10)

    def test_lost_supervisor_does_not_replay_live_child(self):
        spec = self.spec("import time; time.sleep(10)", timeout=15)
        atomic_json(self.root / "spec.json", spec)
        wrapper = launch(self.root)
        child = None
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if (self.root / "process.json").exists():
                    child = read_json(self.root / "process.json").get("child")
                    if child:
                        break
                time.sleep(.03)
            self.assertTrue(alive(child))
            wrapper.kill()
            wrapper.wait()
            with self.assertRaisesRegex(RuntimeError, "No automatic replay"):
                wait_for_job(self.root, spec)
            self.assertTrue(alive(child))
        finally:
            if child and alive(child):
                os.killpg(child["pid"], signal.SIGKILL)
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait()

    def test_cancel_terminates_process(self):
        spec = self.spec("import time; time.sleep(10)")
        atomic_json(self.root / "spec.json", spec)
        (self.root / "cancel.request").write_text("cancel")
        result = wait_for_job(self.root, spec)
        self.assertTrue(result["cancelled"])

    def test_completed_job_reuses_receipt(self):
        spec = self.spec("from pathlib import Path; Path('count').write_text('one')")
        first = wait_for_job(self.root, spec)
        (self.root / "count").write_text("unchanged")
        second = wait_for_job(self.root, spec)
        self.assertEqual(first, second)
        self.assertEqual((self.root / "count").read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
