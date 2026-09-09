"""Regressions for the live host's empty-patch and sandbox setup failures."""
import importlib.util
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'live_job', Path(__file__).resolve().parents[1] / 'proofs/approved-job-live-001/run.py')
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)


class LiveSandboxTests(unittest.TestCase):
    def test_preflight_home_is_outside_os_temp_and_is_cleaned_up(self):
        homes = []
        def invoke(command, cwd, env, timeout):
            home = Path(env['CODEX_HOME'])
            self.assertFalse(home.is_relative_to(Path(tempfile.gettempdir())))
            self.assertNotEqual(home, Path(os.environ.get('CODEX_HOME', '/unused')))
            self.assertTrue(home.is_dir())
            homes.append(home)
            return subprocess.CompletedProcess(command, 0, 'SANDBOX_PROBE_PASS', '')
        with tempfile.TemporaryDirectory() as root, patch.object(live, 'command_result', side_effect=invoke):
            live.sandbox_preflight(Path(root) / 'out')
        self.assertTrue(homes)
        self.assertTrue(all(not home.exists() for home in homes))

    @unittest.skipUnless(shutil.which('codex'), 'pinned Codex CLI not installed')
    def test_real_cli_creates_helper_in_preflight_home(self):
        # Exercise release-binary startup, not a mocked claim about its path.
        def invoke(command, cwd, env, timeout):
            result = subprocess.run([command[0], '--version'], cwd=cwd, env=env,
                                    text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('0.153.0', result.stdout)
            self.assertNotIn('Refusing to create helper', result.stderr)
            aliases = list(Path(env['CODEX_HOME']).glob('tmp/arg0/*/codex-linux-sandbox'))
            self.assertTrue(aliases, result.stderr)
            self.assertTrue(all(alias.is_file() for alias in aliases))
            return subprocess.CompletedProcess(command, 0, 'SANDBOX_PROBE_PASS', '')
        with tempfile.TemporaryDirectory() as root, patch.object(live, 'command_result', side_effect=invoke):
            live.sandbox_preflight(Path(root) / 'out')

    def test_empty_change_set_is_not_reported_as_an_unapproved_path(self):
        with patch.object(live, 'changed_paths', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'WORKER_MADE_NO_CHANGES'):
                live.require_only_calculator(None, Path('.'))

    def test_extra_path_still_rejected(self):
        with patch.object(live, 'changed_paths', return_value=['approved_check.py', 'calculator.py']):
            with self.assertRaisesRegex(RuntimeError, 'WORKER_CHANGED_UNAPPROVED_PATHS'):
                live.require_only_calculator(None, Path('.'))

    def test_namespace_failure_stops_preflight_and_preserves_diagnostic(self):
        failure = subprocess.CompletedProcess([], 1, '', 'bwrap: loopback: Operation not permitted')
        with tempfile.TemporaryDirectory() as root, patch.object(live, 'command_result', return_value=failure) as call:
            out = Path(root) / 'out'
            with self.assertRaisesRegex(RuntimeError, 'CODEX_SANDBOX_PREFLIGHT_FAILED'):
                live.sandbox_preflight(out)
            self.assertIn('Operation not permitted', (out / 'sandbox-shell.log').read_text())
            self.assertFalse((out / 'sandbox-preflight.json').exists())
            self.assertEqual(call.call_count, 1)

    def test_zero_exit_without_probe_completion_is_rejected(self):
        with tempfile.TemporaryDirectory() as root, patch.object(
            live, 'command_result', return_value=subprocess.CompletedProcess([], 0, 'blocked', '')
        ):
            with self.assertRaisesRegex(RuntimeError, 'CODEX_SANDBOX_PREFLIGHT_FAILED'):
                live.sandbox_preflight(Path(root) / 'out')

    def test_both_network_policies_are_checked_without_provider_auth(self):
        seen = []
        def invoke(command, cwd, env, timeout):
            seen.append(command)
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('ANTHROPIC_API_KEY', env)
            return subprocess.CompletedProcess(command, 0, 'SANDBOX_PROBE_PASS', '')
        with tempfile.TemporaryDirectory() as root, patch.object(live, 'command_result', side_effect=invoke):
            live.sandbox_preflight(Path(root) / 'out')
        self.assertEqual(len(seen), 2)
        self.assertIn('sandbox_workspace_write.network_access=true', seen[0])
        self.assertIn('sandbox_workspace_write.network_access=false', seen[1])
        for command in seen:
            # Codex 0.153.0 infers the host sandbox. Supplying an old `linux`
            # subcommand makes the CLI try to exec a program literally named linux.
            self.assertEqual(command[:2], ['codex', 'sandbox'])
            self.assertNotEqual(command[2], 'linux')
            self.assertIn('sandbox_mode="workspace-write"', command)
            self.assertNotIn('danger-full-access', command)


if __name__ == '__main__':
    unittest.main()
