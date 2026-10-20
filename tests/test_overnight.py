from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agentforeman.campaign import create_campaign, drive_campaign, resume_quota_worker
from agentforeman.files import atomic_json, atomic_text, read_json
from agentforeman.overnight import launch, parse_deadline
from agentforeman.platforms import alive
from agentforeman.quota import classify, exhausted_reset, probe_reset
from agentforeman.store import Store
from tests import test_campaign as helpers


class QuotaProtocolTests(unittest.TestCase):
    def test_errors_are_classified_without_matching_agent_text_or_generic_429(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            failed = dict(exit_code=1, timed_out=False, cancelled=False, error=None)
            for event in (
                {"type": "item.completed", "item": {"text": "You've hit your usage limit"}},
                {"type": "error", "message": "HTTP 429 rate limit"},
                {"type": "error", "code": "authentication_error", "message": "Please log in"},
                {"type": "error", "message": "Connection reset"},
            ):
                atomic_text(root / "stdout.log", json.dumps(event))
                self.assertIsNone(classify(root, failed))
            atomic_text(root / "stdout.log", json.dumps({"type": "turn.failed", "error": {
                "code": "usage_limit_reached", "message": "You've hit your usage limit", "resets_at": 2000000000}}))
            self.assertEqual(classify(root, failed)["reported_reset_at"], 2000000000)
            self.assertIsNone(classify(root, {**failed, "timed_out": True}))
            self.assertIsNone(classify(root, {**failed, "exit_code": 0}))

    def test_explicit_stderr_and_timezone_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atomic_text(root / "stderr.log", "ERROR: You've hit your usage limit. Try again at 2030-01-01T08:00:00+08:00\n")
            value = classify(root, {"exit_code": 1})
            self.assertEqual(value["reported_reset_at"], datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp())

    def test_latest_exhausted_window_and_correct_meter(self):
        self.assertEqual(exhausted_reset({"rateLimits": {
            "primary": {"usedPercent": 100, "resetsAt": 2000000000},
            "secondary": {"usedPercent": 100, "resetsAt": 2000100000}}}), 2000100000)
        self.assertIsNone(exhausted_reset({"rateLimits": {"primary": {"usedPercent": 50, "resetsAt": 2000000000}}}))
        self.assertIsNone(exhausted_reset({"rateLimitsByLimitId": {"other_model": {"primary": {"usedPercent": 100, "resetsAt": 2000000000}}}}))

    def test_metadata_rpc_without_model_turn(self):
        fixture = Path(__file__).with_name("fixture_campaign.py").resolve()
        with patch.dict(os.environ, {"FOREMAN_TEST_RESET_AT": "2000000000"}):
            self.assertEqual(probe_reset({"command": [sys.executable, str(fixture)]}, timeout=3), 2000000000)

    def test_deadline_parsing(self):
        stamp = parse_deadline("08:00")
        self.assertGreater(stamp, time.time())
        self.assertLessEqual(stamp - time.time(), 25 * 3600)
        future = datetime.now(timezone.utc) + timedelta(hours=9)
        self.assertAlmostEqual(parse_deadline(future.isoformat()), future.timestamp(), places=3)
        for value in ("25:00", "nope", "2026-10-21T08:00:00", "2000-01-01T00:00:00Z"):
            with self.assertRaises(ValueError):
                parse_deadline(value)


class OvernightTests(unittest.TestCase):
    setUp = helpers.CampaignTests.setUp
    tearDown = helpers.CampaignTests.tearDown
    root = helpers.CampaignTests.root
    state = helpers.CampaignTests.state
    drive = helpers.CampaignTests.drive

    def create(self, **kwargs):
        self.marker = Path(self.temp.name) / "quota fixture"
        os.environ["FOREMAN_TEST_QUOTA_MARKER"] = str(self.marker)
        os.environ["FOREMAN_CAMPAIGN_SCRIPT"] = '["good", "stop"]'
        for key in ("FOREMAN_TEST_QUOTA_MARKER", "FOREMAN_TEST_QUOTA_SCOPE", "FOREMAN_TEST_QUOTA_ALWAYS", "FOREMAN_TEST_EXPECT_PARTIAL"):
            self.addCleanup(os.environ.pop, key, None)
        policy = {"probe": False, "minimum_seconds": 0.05, "max_retries": 3}
        policy.update(kwargs.pop("quota_policy", {}))
        return create_campaign(self.project, "Overnight test goal", **{
            "max_rounds": 2, "budget_seconds": 180, "quota_policy": policy, **kwargs})

    def wake_now(self, cid):
        store = Store(self.project)
        try:
            state = store.get_campaign(cid)
            state["quota_wait"]["wake_at"] = time.time() - 1
            store.save_campaign(state, "test_advance_wake")
        finally:
            store.close()

    def calls(self):
        path = self.marker / "calls.jsonl"
        return path.read_text().splitlines() if path.exists() else []

    def wait_for(self, check, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            found = check()
            if found:
                return found
            time.sleep(0.05)
        self.fail("Timed out waiting for background state")

    def test_manager_limit_waits_without_model_calls_then_resumes(self):
        os.environ["FOREMAN_TEST_QUOTA_SCOPE"] = "manager"
        cid = self.create()
        waiting = self.drive(cid)
        self.assertEqual(waiting["status"], "waiting_quota")
        self.assertEqual(waiting["rounds"], [])
        before = self.calls()
        for _ in range(3):
            self.assertEqual(self.drive(cid)["status"], "waiting_quota")
        self.assertEqual(self.calls(), before)
        self.wake_now(cid)
        done = self.drive(cid)
        self.assertEqual(done["stop_reason"], "manager_stop")
        self.assertEqual(len(done["rounds"]), 1)
        self.assertEqual(done["quota_retries"], 1)
        self.assertEqual(len(list((self.root(cid) / "decisions").glob("000-*/job/result.json"))), 2)

    def test_worker_limit_keeps_partial_code_and_never_repeats_baseline(self):
        os.environ["FOREMAN_TEST_QUOTA_SCOPE"] = "worker"
        os.environ["FOREMAN_TEST_EXPECT_PARTIAL"] = "1"
        cid = self.create(max_rounds=1)
        waiting = self.drive(cid)
        self.assertEqual(waiting["phase"], "execute")
        rid = waiting["pending"]["run_id"]
        frozen = self.root(cid) / "project"
        run = frozen / ".foreman" / "runs" / rid
        receipts = {str(p): p.stat().st_mtime_ns for p in run.glob("jobs/baseline_*/result.json")}
        self.assertEqual(len(receipts), 2)
        # Simulate a crash after child recovery committed, before campaign woke.
        resume_quota_worker(frozen, waiting["quota_wait"])
        self.wake_now(cid)
        done = self.drive(cid)
        self.assertEqual(done["rounds"][0]["status"], "completed")
        self.assertEqual(done["rounds"][0]["run_id"], rid)
        self.assertEqual(done["quota_retries"], 1)
        self.assertEqual(receipts, {str(p): p.stat().st_mtime_ns for p in run.glob("jobs/baseline_*/result.json")})
        self.assertEqual(len(list(run.glob("jobs/codex-*/result.json"))), 2)
        self.assertEqual(read_json(run / "quota_attempts" / "codex-00" / "workspace" / "config.json")["epochs"], 77)

    def test_deadline_while_waiting_writes_best_and_unfinished_reason(self):
        os.environ["FOREMAN_TEST_QUOTA_SCOPE"] = "manager"
        cid = self.create()
        self.drive(cid)
        store = Store(self.project)
        state = store.get_campaign(cid)
        state["deadline_at"] = time.time() - 1
        store.save_campaign(state, "test_deadline")
        store.close()
        calls = self.calls()
        done = self.drive(cid)
        self.assertEqual(done["stop_reason"], "wall_clock_budget")
        self.assertIsNotNone(done["best"])
        self.assertEqual(self.calls(), calls)
        self.assertIn("waiting for quota", (self.root(cid) / "report.md").read_text())

    def test_repeated_limits_are_bounded(self):
        os.environ["FOREMAN_TEST_QUOTA_SCOPE"] = "manager"
        os.environ["FOREMAN_TEST_QUOTA_ALWAYS"] = "1"
        cid = self.create(quota_policy={"max_retries": 1})
        self.assertEqual(self.drive(cid)["status"], "waiting_quota")
        self.wake_now(cid)
        done = self.drive(cid)
        self.assertEqual(done["stop_reason"], "quota_retry_limit")
        self.assertEqual(len(self.calls()), 2)

    def test_account_reset_lookup_is_model_free_and_preferred(self):
        os.environ["FOREMAN_TEST_QUOTA_SCOPE"] = "manager"
        cid = self.create(quota_policy={"probe": True})
        waiting = self.drive(cid)
        self.assertEqual(waiting["quota_wait"]["reset_source"], "account_rate_limits")
        self.assertEqual(len(self.calls()), 1)

    def test_background_survives_submission_and_stops_without_another_call(self):
        os.environ["FOREMAN_TEST_QUOTA_SCOPE"] = "manager"
        # Build state to configure a marker, then launch the actual public CLI for a new task.
        self.create()
        deadline = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
        result = subprocess.run([sys.executable, "-m", "agentforeman", "overnight", "start", "--project", str(self.project),
                                 "--goal", "Background CLI test", "--deadline", deadline, "--no-quota-probe"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        cid = result.stdout.split("Campaign ID: ")[1].splitlines()[0]
        root = self.root(cid)
        try:
            self.wait_for(lambda: self.state(cid)["status"] == "waiting_quota")
            receipt = read_json(root / "runner.json")
            self.assertTrue(alive(receipt["process"]))
            self.assertEqual(launch(self.project, cid)["process"], receipt["process"])
            calls = self.calls()
            atomic_text(root / "stop.request", "stop\n")
            self.wait_for(lambda: read_json(root / "runner.json").get("finished_at"))
            self.wait_for(lambda: not alive(receipt["process"]))
            self.assertEqual(self.state(cid)["stop_reason"], "stop_requested")
            self.assertEqual(self.calls(), calls)
        finally:
            atomic_text(root / "stop.request", "stop\n")
            self.wait_for(lambda: read_json(root / "runner.json").get("finished_at"))

    def test_controller_kill_during_wait_preserves_wait_and_restarts(self):
        os.environ["FOREMAN_TEST_QUOTA_SCOPE"] = "manager"
        cid = self.create()
        log = (Path(self.temp.name) / "service.log").open("wb")
        controller = subprocess.Popen([sys.executable, "-m", "agentforeman.overnight", str(self.project), cid, "kill-test"], stdout=log, stderr=log)
        try:
            self.wait_for(lambda: self.state(cid)["status"] == "waiting_quota")
            controller.kill()
            controller.wait(timeout=5)
            calls = self.calls()
            self.assertEqual(self.drive(cid)["status"], "waiting_quota")
            self.assertEqual(self.calls(), calls)
            self.wake_now(cid)
            self.assertEqual(self.drive(cid)["status"], "completed")
        finally:
            if controller.poll() is None:
                controller.kill()
                controller.wait(timeout=5)
            log.close()

    def test_background_timer_automatically_wakes_and_completes(self):
        os.environ["FOREMAN_TEST_QUOTA_SCOPE"] = "manager"
        os.environ["FOREMAN_TEST_RESET_DELAY"] = "0.25"
        self.addCleanup(os.environ.pop, "FOREMAN_TEST_RESET_DELAY", None)
        cid = self.create(max_rounds=1)
        receipt = launch(self.project, cid)
        try:
            self.wait_for(lambda: self.state(cid)["status"] == "completed", timeout=30)
            self.wait_for(lambda: read_json(self.root(cid) / "runner.json").get("finished_at"))
            self.wait_for(lambda: not alive(receipt["process"]))
            done = self.state(cid)
            self.assertEqual(done["quota_retries"], 1)
            self.assertEqual(done["stop_reason"], "max_rounds")
            self.assertTrue(done["metric_criterion_met"])
        finally:
            atomic_text(self.root(cid) / "stop.request", "stop\n")
            self.wait_for(lambda: read_json(self.root(cid) / "runner.json").get("finished_at"))

    def test_protected_partial_edit_blocks_automatic_recovery(self):
        os.environ["FOREMAN_TEST_QUOTA_SCOPE"] = "worker"
        cid = self.create()
        waiting = self.drive(cid)
        run = self.root(cid) / "project" / ".foreman" / "runs" / waiting["pending"]["run_id"]
        (run / "workspace" / "train.csv").write_text("forbidden edit")
        calls = self.calls()
        self.wake_now(cid)
        with self.assertRaisesRegex(ValueError, "violate the allowlist"):
            self.drive(cid)
        self.assertEqual(self.state(cid)["status"], "failed")
        self.assertEqual(self.calls(), calls)


if __name__ == "__main__":
    unittest.main()
