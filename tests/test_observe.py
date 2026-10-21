from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agentforeman.cli import main
from agentforeman.files import atomic_json, atomic_text
from agentforeman.observe import diagnostics, job_usage, monitor_snapshot, render_monitor, statistics
from agentforeman.quota import probe_limits
from agentforeman.usage import normalize, usage_snapshot
from tests import test_campaign as helpers


def payload(used=0):
    return {"rateLimitsByLimitId": {"codex": {"planType": "plus", "secret": "NEVER_SHARE",
            "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": 2000000000},
            "secondary": {"usedPercent": 80, "windowDurationMins": 10080, "resetsAt": 2000100000}},
            "other": {"primary": {"usedPercent": 100, "resetsAt": 2100000000}}}, "accessToken": "NEVER_SHARE"}


class ObserveTests(unittest.TestCase):
    setUp = helpers.CampaignTests.setUp
    tearDown = helpers.CampaignTests.tearDown

    def test_normalization_zero_weekly_independence_and_allowlist(self):
        result = normalize(payload())
        self.assertEqual([w["used_percent"] for w in result["windows"]], [0, 80])
        self.assertEqual([w["duration_minutes"] for w in result["windows"]], [300, 10080])
        self.assertNotIn("NEVER_SHARE", json.dumps(result))
        for value in (True, -1, 101, float("nan"), float("inf"), "0"):
            data = payload(value)
            data["rateLimitsByLimitId"]["codex"]["secondary"] = None
            with self.assertRaises(ValueError):
                normalize(data)
        with self.assertRaises(ValueError):
            normalize({"rateLimitsByLimitId": {"other": {}}, "rateLimits": payload()["rateLimitsByLimitId"]["codex"]})

    def test_refresh_cache_failure_and_no_secret_retention(self):
        with patch("agentforeman.usage.probe_limits", return_value={"ok": True, "data": payload()}) as rpc:
            value = usage_snapshot(self.project, refresh=True)
            self.assertTrue(value["available"])
            self.assertFalse(value["stale"])
            self.assertEqual(rpc.call_count, 1)
        with patch("agentforeman.usage.probe_limits", side_effect=AssertionError("Cached lookup launched Codex")):
            cached = usage_snapshot(self.project)
            self.assertTrue(cached["cached"])
            self.assertEqual(cached["windows"], value["windows"])
        self.assertNotIn("NEVER_SHARE", (self.project / ".foreman" / "usage-worker.json").read_text())
        with patch("agentforeman.usage.probe_limits", return_value={"ok": False, "error": "timeout"}):
            failed = usage_snapshot(self.project, refresh=True)
            self.assertTrue(failed["stale"])
            self.assertEqual(failed["windows"], value["windows"])
            self.assertEqual(failed["fetched_at"], value["fetched_at"])
            self.assertEqual(failed["error"], "timeout")

    def test_role_and_command_source_isolation_and_expired_reset(self):
        with patch("agentforeman.usage.probe_limits", return_value={"ok": True, "data": payload()}):
            usage_snapshot(self.project, refresh=True)
        self.assertFalse(usage_snapshot(self.project, "manager")["available"])
        with patch.dict(os.environ, {"CODEX_HOME": "different-home"}):
            self.assertFalse(usage_snapshot(self.project)["available"])
        path = self.project / ".foreman" / "usage-worker.json"
        value = json.loads(path.read_text())
        value["windows"][0]["resets_at"] = time.time() - 1
        atomic_json(path, value)
        self.assertTrue(usage_snapshot(self.project)["stale"])
        value["fetched_at"] = time.time() - 1000
        atomic_json(path, value)
        self.assertTrue(usage_snapshot(self.project)["stale"])

    def test_missing_and_invalid_cache_never_implies_zero_usage(self):
        for content in (None, "bad-json", "[]"):
            path = self.project / ".foreman" / "usage-worker.json"
            if content is not None:
                atomic_text(path, content)
            result = usage_snapshot(self.project)
            self.assertFalse(result["available"])
            self.assertIsNone(result["windows"])

    def test_local_monitor_stats_do_not_launch_codex_or_create_database(self):
        with patch("agentforeman.quota.subprocess.Popen", side_effect=AssertionError("Observation launched Codex")):
            snapshot = monitor_snapshot(self.project)
            self.assertIn("No experiments", render_monitor(snapshot))
            self.assertEqual(statistics(self.project)["model_jobs"], 0)
            self.assertTrue(diagnostics(self.project)["ok"])
        self.assertFalse((self.project / ".foreman").exists())

    def test_diagnostics_redacts_commands_paths_and_config_errors(self):
        cfg = self.project / "foreman.toml"
        cfg.write_text(cfg.read_text() + '\n[manager]\ncommand = ["NEVER_SHARE/path", "--token=NEVER_SHARE"]\n')
        result = diagnostics(self.project)
        self.assertFalse(result["ok"])
        self.assertNotIn("NEVER_SHARE", json.dumps(result))
        self.assertNotIn(str(self.project), json.dumps(result))
        cfg.write_text('token = "NEVER_SHARE"\nbroken [')
        result = diagnostics(self.project)
        self.assertFalse(result["ok"])
        self.assertNotIn("NEVER_SHARE", json.dumps(result))

    def test_statistics_counts_only_known_model_jobs_and_cached_subset(self):
        root = self.project / ".foreman"
        records = [
            {"type": "item.completed", "item": {"usage": {"input_tokens": 99999}}},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}}]
        data = "\n".join(json.dumps(r) for r in records) + "\n"
        atomic_text(root / "runs" / "a" / "jobs" / "codex-00" / "stdout.log", data)
        atomic_text(root / "campaigns" / "a" / "decisions" / "000-00" / "job" / "stdout.log", data)
        atomic_text(root / "campaigns" / "a" / "project" / ".foreman" / "runs" / "b" / "jobs" / "codex-00" / "stdout.log", "no usage\n")
        # Non-model logs and quota evidence copies must never double-count.
        atomic_text(root / "runs" / "a" / "jobs" / "train-00" / "stdout.log", data)
        atomic_text(root / "runs" / "a" / "quota_attempts" / "codex-00" / "stdout.log", data)
        result = statistics(self.project)
        self.assertEqual(result["model_jobs"], 3)
        self.assertEqual(result["reported_usage"]["total_tokens"], 266)
        self.assertEqual(result["reported_usage"]["cached_input_tokens"], 80)
        self.assertEqual(result["reported_usage"]["jobs_with_incomplete_usage"], 1)
        self.assertIsNone(result["billing_estimate"])
        self.assertEqual(statistics(self.project), result)

    def test_partial_oversized_and_invalid_usage_is_marked_incomplete(self):
        path = self.project / "usage.log"
        good = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 1}}) + "\n"
        atomic_text(path, "x" * 1048580 + "\n" + good)
        self.assertEqual(job_usage(path)["total_tokens"], 11)
        self.assertFalse(job_usage(path)["complete"])
        self.assertTrue(job_usage(path, limit=1000)["truncated"])
        for usage in ({"input_tokens": True, "output_tokens": 1}, {"input_tokens": 1, "cached_input_tokens": 2, "output_tokens": 1}, None):
            atomic_text(path, json.dumps({"type": "turn.completed", "usage": usage}))
            self.assertFalse(job_usage(path)["complete"])
            self.assertEqual(job_usage(path)["reported_turns"], 0)

    def test_cli_cached_usage_exit_code_watch_json_and_validation(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["usage", "--project", str(self.project), "--cached", "--json"]), 2)
        self.assertFalse(json.loads(out.getvalue())["available"])
        with redirect_stdout(io.StringIO()) as out, patch("agentforeman.cli.time.sleep") as sleep:
            self.assertEqual(main(["monitor", "--project", str(self.project), "--watch", "--samples", "2", "--json"]), 0)
        self.assertEqual(len(out.getvalue().splitlines()), 2)
        sleep.assert_called_once_with(5)
        with redirect_stdout(io.StringIO()), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main(["monitor", "--project", str(self.project), "--interval", "nan"]), 2)

    def test_actual_metadata_probe_has_no_model_methods(self):
        settings = {"command": [os.sys.executable, str(Path(__file__).with_name("fixture_campaign.py").resolve())]}
        self.assertTrue(probe_limits(settings, timeout=3)["ok"])
