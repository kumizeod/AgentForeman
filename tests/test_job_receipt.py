from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from agentforeman.files import atomic_json
from agentforeman.job import wait_for_job


class JobReceiptTests(unittest.TestCase):
    def test_cleanup_timeout_keeps_receipt_and_raises_recoverable_error_without_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);wrapper=Mock()
            wrapper.wait.side_effect=subprocess.TimeoutExpired(['TEST supervisor'],30)
            wrapper.poll.return_value=None
            def launch(job):
                atomic_json(job/'result.json',{'exit_code':0,'error':None,'timed_out':False,'cancelled':False})
                return wrapper
            with patch('agentforeman.job.launch',side_effect=launch) as start:
                with self.assertRaisesRegex(RuntimeError,'without replaying'):
                    wait_for_job(root,{'test':'immutable spec'})
                start.assert_called_once();self.assertTrue((root/'result.json').exists())
            with patch('agentforeman.job.launch') as start:
                result=wait_for_job(root,{'test':'immutable spec'})
                self.assertEqual(result['exit_code'],0);start.assert_not_called()
