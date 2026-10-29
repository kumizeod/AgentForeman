import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentforeman.agents import command, make_spec
from agentforeman.codex import resolve_command
from agentforeman.connections import Connections
from tests.test_agents import SCHEMA


class ClientDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)

    def test_windows_opencode2_npm_native_entry_does_not_run_through_node(self):
        shim=self.root/'opencode.cmd';shim.touch()
        package=self.root/'node_modules/@opencode/cli';(package/'bin').mkdir(parents=True)
        binary=package/'bin/opencode.exe';binary.touch()
        (package/'package.json').write_text(json.dumps({'bin':{'opencode':'bin/opencode.exe'}}))
        with patch('sys.platform','win32'),patch('shutil.which',return_value=None):
            argv=resolve_command({'command':[str(shim)]},npm_package='opencode-ai',bin_name='opencode')
        self.assertEqual(argv,[str(binary.resolve())])

    def test_windows_known_user_install_works_when_console_path_is_stale(self):
        binary=self.root/'.opencode/bin/opencode.exe';binary.parent.mkdir(parents=True);binary.touch()
        with patch('sys.platform','win32'),patch('shutil.which',return_value=None),patch('pathlib.Path.home',return_value=self.root):
            self.assertEqual(command({'provider':'opencode'}),[str(binary)])

    def test_v2_selects_private_server_and_new_deny_configuration(self):
        with patch('agentforeman.agents.command',return_value=['opencode.exe']),patch('agentforeman.agents.opencode_version',return_value=2):
            spec=make_spec(self.root,self.root/'context',{'prompt':'TEST ONLY','schema':SCHEMA},{'provider':'opencode','timeout_seconds':10})
        self.assertIn('--standalone',spec['argv'])
        config=json.loads(spec['env']['OPENCODE_CONFIG_CONTENT'])
        self.assertEqual(config['permissions'],[{'action':'*','resource':'*','effect':'deny'}])
        self.assertIn('foreman',config['agents']);self.assertNotIn('permission',config)
        self.assertFalse(config['warming'])

    def test_v2_accepts_text_only_after_native_success_and_rejects_tools(self):
        from agentforeman.agents import read_answer
        from agentforeman.files import atomic_json,atomic_text
        folder=self.root/'attempt';job=folder/'job'
        atomic_json(folder/'opencode-protocol.json',{'major':2})
        atomic_text(job/'stdout.log',json.dumps({'type':'text','part':{'type':'text','text':'{"answer":"42"}'}})+'\n')
        with self.assertRaises((OSError,ValueError)):read_answer(job,folder,{'schema':SCHEMA},'opencode')
        atomic_json(job/'result.json',{'exit_code':0,'cancelled':False,'timed_out':False,'error':None})
        self.assertEqual(read_answer(job,folder,{'schema':SCHEMA},'opencode'),{'answer':'42'})
        atomic_text(job/'stdout.log',json.dumps({'type':'tool_use','part':{'type':'tool'}})+'\n')
        with self.assertRaises(ValueError):read_answer(job,folder,{'schema':SCHEMA},'opencode')

    def test_authentication_diagnostic_does_not_match_timestamp_or_answer_digits(self):
        from agentforeman.files import atomic_json,atomic_text
        from agentforeman.worker_diagnostics import diagnose
        root=self.root/'worker'
        atomic_text(root/'tasks/a/1/job/stdout.log',json.dumps({'type':'text','timestamp':1793274012345,'part':{'text':'Answer is 403'}})+'\n')
        self.assertEqual(diagnose(root),'provider_execution_or_output_failed')
        atomic_json(root/'tasks/a/1/api-status.json',{'outcome':'failed','code':401})
        self.assertEqual(diagnose(root),'authentication_failed')

    def test_manual_path_requires_a_file_without_arguments_and_stays_scoped(self):
        binary=self.root/'custom CLI.exe';binary.touch()
        self.assertEqual(Connections.executable_path(str(binary)),str(binary))
        for value in ('opencode',str(binary)+' --auto',str(self.root/'missing.exe'),'bad\npath'):
            with self.assertRaises(ValueError):Connections.executable_path(value)
