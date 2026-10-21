import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from agentforeman.campaign import create_campaign
from agentforeman.files import read_json
from agentforeman.observe import monitor_snapshot, statistics
from tests import test_overnight as helpers


# Reuse fixtures/helpers, not the parent's entire test suite.
class PreflightTests(unittest.TestCase):
    setUp = helpers.OvernightTests.setUp
    tearDown = helpers.OvernightTests.tearDown
    root = helpers.OvernightTests.root
    state = helpers.OvernightTests.state
    drive = helpers.OvernightTests.drive
    create = helpers.OvernightTests.create
    calls = helpers.OvernightTests.calls
    wake_now = helpers.OvernightTests.wake_now

    def test_manager_preflight_wait_resume_and_local_monitor_no_calls(self):
        cid = self.create(quota_policy={"preflight": True, "probe": True})
        with patch("agentforeman.quota.probe_limits", return_value={"ok": True, "data": {"rateLimits": {
            "primary": {"usedPercent": 100, "resetsAt": time.time() + 100}}}}) as rpc:
            state = self.drive(cid)
            self.assertEqual(state["status"], "waiting_quota")
            self.assertEqual(state["quota_wait"]["detection"], "preflight")
            self.assertFalse(list(self.root(cid).glob("decisions/*/job/spec.json")))
            self.assertEqual(self.calls(), [])
            self.drive(cid)
            self.assertEqual(rpc.call_count, 1)
        with patch("agentforeman.quota.probe_limits", side_effect=AssertionError("Observation probed")):
            snapshot = monitor_snapshot(self.project, cid)
            self.assertTrue(snapshot["campaigns"][0]["needs_resume"])
            self.assertEqual(statistics(self.project)["model_jobs"], 0)
        self.wake_now(cid)
        with patch("agentforeman.quota.probe_limits", return_value={"ok": False, "error": "unavailable"}):
            state = self.drive(cid)
        self.assertEqual(state["status"], "completed")
        self.assertGreater(state["best"]["metric"], state["initial_metric"])
        self.assertTrue((self.root(cid) / "decisions" / "000-00" / "job" / "result.json").exists())

    def test_worker_preflight_preserves_baseline_and_resumes_without_failed_receipt(self):
        cid = self.create(quota_policy={"preflight": True, "probe": True})
        with patch("agentforeman.quota.probe_limits", return_value={"ok": False, "error": "unavailable"}):
            self.drive(cid, until="execute")
        def guard(job, state, settings, *, scope, run_id=None):
            if scope == "worker":
                from agentforeman.quota import QuotaPause
                raise QuotaPause(job, {"detection": "preflight", "reason": "fixture exhausted", "reported_reset_at": time.time() + 600}, scope=scope, settings=settings, run_id=run_id)
        with patch("agentforeman.campaign.preflight", side_effect=guard):
            state = self.drive(cid)
        self.assertEqual(state["status"], "waiting_quota")
        run = self.root(cid) / "project" / ".foreman" / "runs" / state["pending"]["run_id"]
        baseline = read_json(run / "jobs" / "baseline_train-00" / "result.json")
        self.assertFalse((run / "jobs" / "codex-00" / "spec.json").exists())
        self.wake_now(cid)
        with patch("agentforeman.quota.probe_limits", return_value={"ok": False, "error": "unavailable"}):
            state = self.drive(cid)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(read_json(run / "jobs" / "baseline_train-00" / "result.json"), baseline)
        self.assertTrue((run / "jobs" / "codex-00" / "result.json").exists())
        self.assertFalse((run / "jobs" / "codex-01").exists())

    def test_preflight_skips_existing_jobs_and_validates_policy(self):
        cid = self.create(quota_policy={"preflight": True, "probe": True})
        self.drive(cid, until="decide")
        # Complete a manager decision then pause before execute; resume must never replay it.
        with patch("agentforeman.quota.probe_limits", return_value={"ok": False, "error": "unavailable"}):
            self.drive(cid, until="execute")
        receipt = self.root(cid) / "decisions" / "000-00" / "job" / "result.json"
        before = read_json(receipt)
        with patch("agentforeman.quota.probe_limits", return_value={"ok": False, "error": "unavailable"}):
            self.drive(cid)
        self.assertEqual(read_json(receipt), before)
        for policy in ({"preflight": "yes"}, {"preflight": True, "probe": False}, {"probe": 1}):
            with self.assertRaises(ValueError):
                create_campaign(self.project, "goal", 1, 100, quota_policy=policy)
