from __future__ import annotations

from contextlib import redirect_stdout
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
import zipfile

from agentforeman.campaign import create_campaign, drive_campaign
from agentforeman.cli import init_demo
from agentforeman.engine import create_run
from agentforeman.files import atomic_json, read_json
from agentforeman.job import alive
from agentforeman.report import verify_archive
from agentforeman.store import Store


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="campaign 中文 ")
        self.project = Path(self.temp.name) / "project"
        with redirect_stdout(io.StringIO()):
            init_demo(self.project)
        cfg = self.project / "foreman.toml"
        fixture = Path(__file__).with_name("fixture_campaign.py").resolve()
        cfg.write_text(cfg.read_text().replace('executable = "codex"', f"executable = {json.dumps(str(fixture))}"))
        self.env = patch.dict(os.environ, {"FOREMAN_CAMPAIGN_SCRIPT": '["good", "worse", "stop"]'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def create(self, **kwargs):
        return create_campaign(self.project, "Improve real validation accuracy", **{"max_rounds": 4, "budget_seconds": 180, **kwargs})

    def drive(self, cid, **kwargs):
        with redirect_stdout(io.StringIO()):
            return drive_campaign(self.project, cid, **kwargs)

    def root(self, cid):
        return self.project / ".foreman" / "campaigns" / cid

    def state(self, cid):
        store = Store(self.project)
        try:
            return store.get_campaign(cid)
        finally:
            store.close()

    def test_improve_then_worse_keeps_best_and_archives(self):
        cid = self.create()
        state = self.drive(cid)
        self.assertEqual(state["stop_reason"], "manager_stop")
        self.assertEqual([r["promoted"] for r in state["rounds"]], [True, False])
        self.assertGreater(state["best"]["metric"], state["initial_metric"])
        self.assertEqual(state["best"]["run_id"], state["rounds"][0]["run_id"])
        self.assertTrue((self.root(cid) / "decisions" / "000-00" / "context" / "best_code" / "model.py").exists())
        self.assertTrue(verify_archive(self.root(cid) / state["best"]["archive"])["valid"])
        before = list(self.root(cid).rglob("result.json"))
        self.assertEqual(self.drive(cid), state)
        self.assertEqual(before, list(self.root(cid).rglob("result.json")))

    def test_training_failure_then_repair_then_retest(self):
        os.environ["FOREMAN_CAMPAIGN_SCRIPT"] = '["bad", "repair", "retest", "stop"]'
        cid = self.create()
        state = self.drive(cid)
        failed, repaired, retest = state["rounds"]
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["repairable"])
        self.assertIn("intentional training failure", failed["stderr_tail"])
        self.assertEqual(repaired["source"], failed["repair_source"])
        self.assertTrue(repaired["promoted"])
        self.assertFalse(retest["promoted"])
        run = self.root(cid) / "project" / ".foreman" / "runs" / retest["run_id"]
        self.assertFalse((run / "worker").exists())
        self.assertEqual(state["best"]["run_id"], repaired["run_id"])
        repair_run = self.root(cid) / "project" / ".foreman" / "runs" / repaired["run_id"]
        self.assertTrue((repair_run / "edit_seed" / "config.json").exists())
        self.assertTrue(verify_archive(repair_run / "archive.zip")["valid"])
        extracted = Path(self.temp.name) / "retest replay"
        with zipfile.ZipFile(run / "archive.zip") as archive:
            archive.extractall(extracted)
        replay = subprocess.run([sys.executable, "reproduce.py", "--baseline"], cwd=extracted,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(read_json(extracted / "reproduced" / "baseline" / "verification.json")["metric"], retest["metric"])

    def test_resume_allocated_run_and_record_once(self):
        cid = self.create(max_rounds=1)
        state = self.drive(cid, until="execute")
        pending = state["pending"]
        frozen = self.root(cid) / "project"
        goal = state["goal"] + "\nManager instruction: " + pending["decision"]["instruction"]
        # Simulate death after create_run but before the campaign's next durable transition.
        create_run(frozen, goal, run_id=pending["run_id"], source=self.root(cid) / pending["source"])
        paused = self.drive(cid, until="record")
        self.assertEqual(paused["pending"]["run_id"], pending["run_id"])
        receipts = {str(p): p.stat().st_mtime_ns for p in frozen.rglob("result.json")}
        done = self.drive(cid)
        self.assertEqual(len(done["rounds"]), 1)
        self.assertEqual(done["stop_reason"], "max_rounds")
        self.assertEqual(receipts, {str(p): p.stat().st_mtime_ns for p in frozen.rglob("result.json")})
        self.assertEqual(len(list((frozen / ".foreman" / "runs").iterdir())), 2)

    def test_invalid_repair_is_blocked_and_explicit_retry_preserves_attempt(self):
        os.environ["FOREMAN_CAMPAIGN_SCRIPT"] = '["repair"]'
        cid = self.create()
        with self.assertRaisesRegex(ValueError, "Repair must reference"):
            self.drive(cid)
        self.assertEqual(self.state(cid)["status"], "failed")
        os.environ["FOREMAN_CAMPAIGN_SCRIPT"] = '["stop"]'
        state = self.drive(cid, retry_failed=True)
        self.assertEqual(state["stop_reason"], "manager_stop")
        self.assertEqual(len(list((self.root(cid) / "decisions").glob("*/answer.json"))), 2)

    def test_elapsed_budget_includes_pause_and_blocks_new_work(self):
        cid = self.create()
        self.drive(cid, until="execute")
        state = self.state(cid)
        store = Store(self.project)
        state["deadline_at"] = time.time() - 1
        store.save_campaign(state, "test_clock_advanced")
        store.close()
        done = self.drive(cid)
        self.assertEqual(done["stop_reason"], "wall_clock_budget")
        self.assertEqual(done["rounds"], [])
        self.assertEqual(len(list((self.root(cid) / "project" / ".foreman" / "runs").iterdir())), 1)

    def test_frozen_inputs_reject_tampering_and_user_edits_do_not_affect_resume(self):
        cid = self.create()
        (self.project / "workspace" / "model.py").write_text("user changed original project")
        self.drive(cid, until="decide")
        (self.root(cid) / "project" / "trusted" / "verify.py").write_text("tamper")
        with self.assertRaisesRegex(ValueError, "Frozen campaign inputs changed"):
            self.drive(cid)

    def test_target_stops_before_first_model_call(self):
        cid = self.create(target=0.0)
        state = self.drive(cid)
        self.assertEqual(state["stop_reason"], "target_reached")
        self.assertFalse((self.root(cid) / "decisions").exists())

    def test_uncertain_child_receipt_blocks_replacement(self):
        cid = self.create()
        state = self.drive(cid, until="execute")
        pending = state["pending"]
        frozen = self.root(cid) / "project"
        goal = state["goal"] + "\nManager instruction: " + pending["decision"]["instruction"]
        rid = create_run(frozen, goal, run_id=pending["run_id"], source=self.root(cid) / pending["source"])
        store = Store(frozen)
        child = store.get(rid)
        child.update(status="failed", active_job="baseline_train-00", error="lost supervisor")
        store.save(child, "test_lost_receipt")
        store.close()
        job = frozen / ".foreman" / "runs" / rid / "jobs" / "baseline_train-00"
        atomic_json(job / "spec.json", {"argv": ["never launch"]})
        with self.assertRaisesRegex(RuntimeError, "no result receipt"):
            self.drive(cid)
        self.assertEqual(self.state(cid)["rounds"], [])
        self.assertEqual(self.state(cid)["pending"]["run_id"], rid)

    def test_killed_campaign_reattaches_same_planner_job(self):
        os.environ["FOREMAN_CAMPAIGN_SCRIPT"] = '["stop"]'
        os.environ["FOREMAN_TEST_PLANNER_DELAY"] = "2"
        self.addCleanup(os.environ.pop, "FOREMAN_TEST_PLANNER_DELAY", None)
        cid = self.create()
        self.drive(cid, until="decide")
        with (Path(self.temp.name) / "parent.log").open("wb") as output:
            parent = subprocess.Popen([sys.executable, "-m", "agentforeman", "campaign", "resume", cid,
                                       "--project", str(self.project)], stdout=output, stderr=output)
            try:
                meta = self.root(cid) / "decisions" / "000-00" / "job" / "process.json"
                child = None
                end = time.monotonic() + 15
                while time.monotonic() < end:
                    if meta.exists():
                        child = read_json(meta).get("child")
                        if alive(child):
                            break
                    time.sleep(0.02)
                self.assertTrue(alive(child))
                parent.kill()
                parent.wait(timeout=5)
                self.assertTrue(alive(child))
                done = self.drive(cid)
                self.assertEqual(done["stop_reason"], "manager_stop")
                self.assertEqual(read_json(meta)["child"], child)
                self.assertEqual(len(list((self.root(cid) / "decisions").iterdir())), 1)
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=5)

    def test_manager_timeout_and_round_jobs_are_capped_by_deadline(self):
        cid = self.create(max_rounds=1, budget_seconds=60)
        state = self.drive(cid)
        self.assertEqual(state["stop_reason"], "max_rounds")
        jobs = list(self.root(cid).rglob("spec.json"))
        self.assertGreater(len(jobs), 5)
        for path in jobs:
            self.assertLessEqual(read_json(path)["timeout_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
