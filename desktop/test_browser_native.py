"""Contracts for native opt-in mode. All processes and RPCs are synthetic."""
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import bench_browser_native as native
import bench_ops as ops


class NativeBrowserContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.name = 'native-test'
        self.state = self.root / 'sessions' / self.name
        self.profile = self.state / 'chromium'
        (self.profile / 'Default').mkdir(parents=True)
        self.runtime = self.root / 'runtime' / self.name
        self.runtime.mkdir(parents=True)
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.binary = self.root / 'lib/chromium/chromium'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'\x7fELFfixture')
        self.binary.chmod(0o755)
        for resource in ('resources.pak', 'icudtl.dat'):
            (self.binary.parent / resource).write_bytes(b'fixture')
        self.info = {'name': self.name, 'state': str(self.state), 'display': ':84', 'control_mode': 'agente'}
        self.inside_control = False
        self.launches = []
        self.launch_effect = None
        self.start_calls = 0
        self.patch(ops, 'paths', side_effect=lambda name: (self.runtime, self.state))
        self.patch(ops, 'PROC', self.proc)
        self.patch(native, 'BINARIES', (str(self.binary),))
        self.patch(native, 'control', side_effect=self.control)
        self.start = self.patch(ops, 'start', side_effect=self.start_bench)
        self.request = self.patch(ops, 'request', side_effect=self.rpc)
        self.cdp = self.patch(ops, 'cdp_snapshot', side_effect=AssertionError('CDP is forbidden'))
        self.ensure = self.patch(ops, 'ensure', side_effect=AssertionError('ensure probes CDP'))
        self.patch(ops, 'open_personal_tab', side_effect=AssertionError('CDP is forbidden'))
        self.patch(ops, 'views', side_effect=AssertionError('no raw viewer operation'))
        self.patch(ops.subprocess, 'Popen', side_effect=AssertionError('no host process launch'))
        self.patch(ops.subprocess, 'run', side_effect=AssertionError('no host shell commands'))
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop(native.BINARY_ENV, None)

    def patch(self, target, name, *args, **kwargs):
        patched = patch.object(target, name, *args, **kwargs)
        result = patched.start()
        self.addCleanup(patched.stop)
        return result

    @contextmanager
    def control(self, name):
        if (self.runtime / 'human-control').exists():
            raise RuntimeError('human control')
        self.assertEqual(name, self.name)
        self.inside_control = True
        try:
            yield
        finally:
            self.inside_control = False

    def start_bench(self, name):
        self.assertEqual(name, self.name)
        self.start_calls += 1
        return self.info

    def rpc(self, name, payload, timeout=None):
        self.assertEqual(name, self.name)
        if payload['action'] == 'status':
            return self.info.copy()
        self.assertTrue(self.inside_control, 'launch must hold control')
        self.assertEqual(payload['action'], 'launch')
        self.assertLessEqual(timeout, 2)
        self.launches.append(payload)
        if self.launch_effect:
            return self.launch_effect(payload)
        self.process()
        return {'pid': 42, 'display': ':84', 'name': self.name}

    def process(self, flags=None, bench='native-test', display=':84', packed=False,
                pid=42, lock=True, binary=None, start=12345):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        argv = [str(binary or self.binary), *(flags if flags is not None else native.FLAGS),
                '--user-data-dir=' + str(self.profile)]
        encoded = (' '.join(argv) + '\0').encode() if packed else ('\0'.join(argv) + '\0').encode()
        (directory / 'cmdline').write_bytes(encoded)
        group = '/user.slice/agent-bench@' + bench + '.service' if bench else '/user.slice/app-org.chromium.scope'
        (directory / 'cgroup').write_text('0::' + group + '\n')
        (directory / 'environ').write_bytes(('DISPLAY=' + display + '\0AGENT_BENCH_NAME=' + (bench or 'human') + '\0').encode())
        (directory / 'stat').write_text(str(pid) + ' (chromium process) S ' + ' '.join(['0'] * 18) + ' ' + str(start) + ' 0\n')
        exe = directory / 'exe'
        if exe.is_symlink():
            exe.unlink()
        exe.symlink_to(binary or self.binary)
        singleton = self.profile / 'SingletonLock'
        if lock:
            if singleton.is_symlink():
                singleton.unlink()
            singleton.symlink_to(socket.gethostname() + '-' + str(pid))
        return directory

    def assert_error(self, code, call, *args, **kwargs):
        with self.assertRaises(native.NativeBrowserError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_help_does_not_start_inspect_or_mutate(self):
        for argv in (['--help'], ['open', '--help'], ['status', '--help']):
            with self.subTest(argv=argv), redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    native.main(argv)
                self.assertEqual(caught.exception.code, 0)
        self.start.assert_not_called()
        self.request.assert_not_called()
        native.control.assert_not_called()
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_closed_status_is_read_only_and_never_probes_cdp(self):
        before = list(self.runtime.iterdir())
        result = native.status(self.name)
        self.assertEqual(result['status'], 'closed')
        self.assertIsNone(result['browser'])
        self.start.assert_not_called()
        self.ensure.assert_not_called()
        native.control.assert_not_called()
        self.cdp.assert_not_called()
        self.assertEqual(list(self.runtime.iterdir()), before)
        self.assertTrue(all(call.args[1]['action'] == 'status' for call in self.request.call_args_list))

    def test_status_offline_does_not_create_runtime(self):
        self.runtime.rmdir()
        self.request.side_effect = FileNotFoundError('offline')
        self.assertEqual(native.status(self.name)['status'], 'closed')
        self.assertFalse(self.runtime.exists())
        self.start.assert_not_called()

    def test_missing_default_never_starts_or_creates_profile(self):
        (self.profile / 'Default').rmdir()
        for function in (native.status, native.open_browser):
            self.assert_error('profile_missing', function, self.name)
        self.assertFalse((self.profile / 'Default').exists())
        self.start.assert_not_called()
        self.assertEqual(self.launches, [])

    def test_linked_default_refused(self):
        (self.profile / 'Default').rmdir()
        target = self.root / 'human'
        target.mkdir()
        (self.profile / 'Default').symlink_to(target, target_is_directory=True)
        self.assert_error('profile_invalid', native.open_browser, self.name)
        self.start.assert_not_called()

    def test_invalid_explicit_binary_has_no_fallback(self):
        self.assert_error('binary_invalid', native.open_browser, self.name, binary='/missing/chromium')
        self.assert_error('binary_invalid', native.binary_path, 'chromium')
        self.start.assert_not_called()

    def test_env_binary_is_explicit_and_invalid_env_is_not_ignored(self):
        with patch.dict(os.environ, {native.BINARY_ENV: '/missing/chromium'}):
            self.assert_error('binary_invalid', native.binary_path)
            self.assertEqual(native.binary_path(str(self.binary)), self.binary)
        with patch.dict(os.environ, {native.BINARY_ENV: str(self.binary)}):
            self.assertEqual(native.binary_path(), self.binary)

    def test_elf_wrapper_is_rejected_even_with_fake_resources(self):
        wrapper = self.root / 'bin/chromium'
        wrapper.parent.mkdir()
        wrapper.write_bytes(b'\x7fELFwrapper')
        wrapper.chmod(0o755)
        for resource in ('resources.pak', 'icudtl.dat'):
            (wrapper.parent / resource).touch()
        self.assert_error('binary_invalid', native.binary_path, str(wrapper))
        link = self.root / 'wrapper/chromium'
        link.parent.mkdir()
        link.symlink_to(wrapper)
        self.assert_error('binary_invalid', native.binary_path, str(link))

    def test_script_non_executable_and_missing_resources_refused(self):
        self.binary.write_text('#!/bin/sh\nchromium "$@"\n')
        self.assert_error('binary_invalid', native.binary_path)
        self.binary.write_bytes(b'\x7fELFfixture')
        self.binary.chmod(0o644)
        self.assert_error('binary_invalid', native.binary_path)
        self.binary.chmod(0o755)
        (self.binary.parent / 'resources.pak').unlink()
        self.assert_error('binary_invalid', native.binary_path)

    def test_existing_native_identity_nul_and_single_string(self):
        for packed in (False, True):
            with self.subTest(packed=packed):
                self.process(packed=packed)
                result = native.status(self.name)
                self.assertEqual(result['browser']['pid'], 42)
                self.assertEqual(result['browser']['lives_in'], self.name)
                self.assertEqual(result['browser']['user_data_dir'], str(self.profile))
                self.assertEqual(result['browser']['display'], ':84')
                self.assertTrue(result['browser']['flags_verified'])
                self.assertFalse(result['capabilities']['cdp'])
                self.assertFalse(result['capabilities']['webdriver'])
                self.assertFalse(result['capabilities']['accessibility_verified'])
        self.assertEqual(self.launches, [])
        self.start.assert_not_called()

    def test_external_and_other_bench_process_refused_before_start(self):
        for bench in (None, 'another'):
            with self.subTest(bench=bench):
                self.process(bench=bench)
                self.assert_error('browser_outside_bench', native.open_browser, self.name)
        self.start.assert_not_called()
        self.assertEqual(self.launches, [])

    def test_wrong_display_refused(self):
        self.process(display=':0')
        self.assert_error('browser_outside_bench', native.open_browser, self.name)
        self.start.assert_not_called()

    def test_explicit_display_overrides_refused_even_with_valid_or_erased_env(self):
        for flags in (['--display=:0'], ['--display', ':0'], ['-display=:0'],
                      ['-display', ':0'], ['--display=:84']):
            for packed in (False, True):
                for erased in (False, True):
                    with self.subTest(flags=flags, packed=packed, erased=erased):
                        directory = self.process(flags=[*native.FLAGS, *flags], packed=packed)
                        if erased:
                            (directory / 'environ').write_bytes(b'\0' * 7855)
                        self.assert_error('browser_outside_bench', native.open_browser,
                                          self.name, 'https://example.org/')
        self.start.assert_not_called()
        self.assertEqual(self.launches, [])

    def test_ozone_hint_cannot_select_another_backend(self):
        for flags in (['--ozone-platform-hint=wayland'], ['--ozone-platform-hint', 'auto'],
                      ['-ozone-platform-hint=wayland'], ['-ozone-platform-hint', 'auto'],
                      ['--ozone-platform-hint=']):
            for packed in (False, True):
                with self.subTest(flags=flags, packed=packed):
                    self.process(flags=[*native.FLAGS, *flags], packed=packed)
                    self.assert_error('browser_outside_bench', native.open_browser, self.name)
        self.start.assert_not_called()
        self.assertEqual(self.launches, [])
        self.process(flags=[*native.FLAGS, '--ozone-platform-hint=x11'])
        self.assertEqual(native.status(self.name)['status'], 'running')

    def test_erased_environment_reports_inferred_display_not_verified_env(self):
        directory = self.process()
        for value in (b'', b'\0' * 7855):
            with self.subTest(size=len(value)):
                (directory / 'environ').write_bytes(value)
                result = native.status(self.name)
                self.assertEqual(result['browser']['display_source'], 'bench_status/cgroup')
                self.assertFalse(result['browser']['env_verified'])
                self.assertTrue(result['browser']['flags_verified'])
        self.start.assert_not_called()

    def test_partial_environment_cannot_claim_erased_environment_fallback(self):
        directory = self.process()
        for value in (b'DISPLAY=:84\0', b'AGENT_BENCH_NAME=native-test\0', b'malformed'):
            (directory / 'environ').write_bytes(value)
            self.assert_error('browser_outside_bench', native.status, self.name)

    def test_verified_environment_report_is_distinct(self):
        self.process()
        identity = native.status(self.name)['browser']
        self.assertTrue(identity['env_verified'])
        self.assertEqual(identity['display_source'], 'process_environment')

    def test_cdp_automation_and_unsafe_flags_refused_in_both_proc_formats(self):
        for flag in ('--remote-debugging-port=0', '--remote-debugging-pipe',
                     '--remote-debugging-address=127.0.0.1', '--enable-automation',
                     '--no-sandbox', '--disable-web-security', '--user-agent=spoof',
                     '--disable-blink-features=AutomationControlled',
                     '-remote-debugging-port=0', '-enable-automation', '-no-sandbox'):
            for packed in (False, True):
                with self.subTest(flag=flag, packed=packed):
                    self.process(flags=[*native.FLAGS, flag], packed=packed)
                    self.assert_error('browser_not_native', native.open_browser, self.name, 'https://example.org/')
        self.start.assert_not_called()
        self.assertEqual(self.launches, [])
        self.cdp.assert_not_called()

    def test_wrong_profile_and_duplicate_flags_refused(self):
        directory = self.process()
        raw = (directory / 'cmdline').read_bytes()
        (directory / 'cmdline').write_bytes(raw.replace(str(self.profile).encode(), b'/human/profile'))
        self.assert_error('process_identity_mismatch', native.open_browser, self.name)
        self.process(flags=[*native.FLAGS, '--user-data-dir=/human/profile'])
        self.assert_error('process_uncertain', native.open_browser, self.name)
        self.start.assert_not_called()

    def test_wrong_executable_and_required_flags_refused(self):
        fake = self.binary.parent / 'other'
        fake.write_bytes(b'\x7fELFother')
        directory = self.process()
        (directory / 'exe').unlink()
        (directory / 'exe').symlink_to(fake)
        self.assert_error('binary_mismatch', native.open_browser, self.name)
        self.process(flags=['--ozone-platform=x11', '--profile-directory=Default'])
        self.assert_error('process_identity_mismatch', native.open_browser, self.name)

    def test_lock_pid_cgroup_revalidated_not_only_ops_hint(self):
        self.process(bench='another')
        self.patch(ops, 'profile_process', return_value={'pid': 42, 'bench': self.name})
        self.assert_error('browser_outside_bench', native.open_browser, self.name)

    def test_process_start_time_change_refused(self):
        self.process()
        self.patch(native, 'process_stamp', side_effect=[1, 2])
        self.assert_error('process_uncertain', native.status, self.name)

    def test_unreadable_process_is_uncertain(self):
        directory = self.process()
        (directory / 'environ').unlink()
        self.assert_error('process_uncertain', native.open_browser, self.name)
        self.start.assert_not_called()

    def test_stale_foreign_and_orphan_locks_preserved(self):
        for lock, target, code in (
            ('SingletonLock', socket.gethostname() + '-99999', 'profile_lock_uncertain'),
            ('SingletonLock', 'foreign-machine-99999', 'profile_owner_uncertain'),
            ('SingletonSocket', '/missing/socket', 'profile_lock_uncertain'),
            ('SingletonCookie', 'cookie', 'profile_lock_uncertain')):
            with self.subTest(lock=lock, target=target):
                candidate = self.profile / lock
                candidate.symlink_to(target)
                self.assert_error(code, native.open_browser, self.name)
                self.assertEqual(os.readlink(candidate), target)
                candidate.unlink()  # fixture cleanup only
        self.start.assert_not_called()
        self.assertEqual(self.launches, [])

    def test_missing_lock_but_live_profile_process_refused(self):
        self.process(lock=False)
        self.assert_error('profile_owner_uncertain', native.open_browser, self.name)
        self.start.assert_not_called()

    def test_scanner_ignores_non_browser_prompt_containing_flags_and_quotes(self):
        directory = self.proc / '123'
        directory.mkdir()
        (directory / 'exe').symlink_to('/usr/bin/bash')
        (directory / 'cmdline').write_bytes(b"agent prompt ' --user-data-dir=/human --remote-debugging-port=0\0")
        self.assertEqual(native.status(self.name)['status'], 'closed')

    def test_scanner_still_catches_deleted_browser_executable(self):
        directory = self.process(lock=False)
        (directory / 'exe').unlink()
        (directory / 'exe').symlink_to(str(self.binary) + ' (deleted)')
        self.assert_error('profile_owner_uncertain', native.status, self.name)

    def test_stale_devtools_file_is_not_an_endpoint_and_is_preserved(self):
        endpoint = self.profile / 'DevToolsActivePort'
        endpoint.write_text('9222\n/devtools/browser/stale\n')
        self.assertEqual(native.status(self.name)['status'], 'closed')
        self.assertTrue(endpoint.exists())
        self.cdp.assert_not_called()

    def test_initial_launch_url_exactly_once_no_extra_blank(self):
        url = 'https://example.org/path?q=%22hello%22&n=1#fragment'
        result = native.open_browser(self.name, url)
        self.assertEqual(result['status'], 'running')
        self.assertEqual(result['action'], 'started')
        self.assertEqual(result['url_delivery'], 'dispatched_unverified')
        self.assertEqual(len(self.launches), 1)
        argv = self.launches[0]['argv']
        self.assertEqual(argv[0], str(self.binary))
        self.assertEqual(argv[-2:], ['--', url])
        self.assertEqual(argv.count(url), 1)
        self.assertNotIn('about:blank', argv)
        self.assertIn('--user-data-dir=' + str(self.profile), argv)
        for flag in native.FLAGS:
            self.assertIn(flag, argv)
        self.assertFalse(any(flag.startswith('--remote-debugging') for flag in argv))
        self.assertFalse(set(argv) & {'--enable-automation', '--no-sandbox', '--disable-web-security'})
        self.assertEqual(self.start_calls, 1)
        self.ensure.assert_not_called()

    def test_packed_command_line_does_not_parse_url_quotes_as_shell_syntax(self):
        directory = self.process(packed=True)
        raw = (directory / 'cmdline').read_bytes().rstrip(b'\0')
        url = b"https://example.org/it's-a-page?q=%22x%22&option=--remote-debugging-port=0"
        (directory / 'cmdline').write_bytes(raw + b' -- ' + url + b'\0')
        self.assertEqual(native.status(self.name)['status'], 'running')

    def test_nul_command_line_keeps_space_in_profile_argument(self):
        args = native.command_line(b'/lib/chromium\0--user-data-dir=/profile with space\0--\0https://host/\0')
        self.assertEqual(native.switches(args)['--user-data-dir'], '/profile with space')

    def test_initial_launch_without_url_preserves_session_and_does_not_inject_blank(self):
        result = native.open_browser(self.name)
        self.assertEqual(result['url_delivery'], 'not_requested')
        self.assertEqual(self.launches[0]['argv'], [str(self.binary), *native.FLAGS, '--user-data-dir=' + str(self.profile)])

    def test_existing_native_without_url_never_launches_again(self):
        self.process()
        result = native.open_browser(self.name)
        self.assertEqual(result['action'], 'reused')
        self.assertEqual(self.launches, [])

    def test_existing_native_url_via_singleton_keeps_original_pid(self):
        self.process()
        self.launch_effect = lambda payload: {'pid': 43, 'display': ':84', 'name': self.name}
        result = native.open_browser(self.name, 'about:blank')
        self.assertEqual(result['action'], 'url_dispatched')
        self.assertEqual(result['browser']['pid'], 42)
        self.assertEqual(result['launch_pid'], 43)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0]['argv'][-2:], ['--', 'about:blank'])

    def test_human_control_blocks_start_and_launch_but_not_status(self):
        (self.runtime / 'human-control').touch()
        self.assert_error('human_control', native.open_browser, self.name)
        self.assertEqual(native.status(self.name)['control_mode'], 'humano')
        self.start.assert_not_called()
        self.assertEqual(self.launches, [])

    def test_control_gate_rechecked_after_start(self):
        def handover(name):
            (self.runtime / 'human-control').touch()
            return self.info
        self.start.side_effect = handover
        with self.assertRaisesRegex(RuntimeError, 'human control'):
            native.open_browser(self.name)
        self.assertEqual(self.launches, [])

    def test_server_human_state_blocks_mutation(self):
        self.info['control_mode'] = 'humano'
        self.assert_error('human_control', native.open_browser, self.name)
        self.assertEqual(self.launches, [])

    def test_invalid_urls_and_timeout_do_not_start(self):
        for url in ('--no-sandbox', '-x', 'file:///etc/passwd', 'javascript:alert(1)',
                    'data:text/html,x', 'about:config', 'https://', 'https://host:bad',
                    'https://user:password@host/', 'https://example.org/\n--x',
                    'https://example.org/a b', ' https://example.org', ''):
            with self.subTest(url=url):
                self.assert_error('url_invalid', native.open_browser, self.name, url)
        for timeout in (0, -1, 20, float('nan'), float('inf')):
            self.assert_error('timeout_invalid', native.open_browser, self.name, timeout=timeout)
        self.start.assert_not_called()
        self.assertEqual(self.launches, [])

    def test_launch_timeout_never_relaunches_or_removes_artifacts(self):
        self.launch_effect = lambda payload: {'pid': 42, 'name': self.name, 'display': ':84'}
        error = self.assert_error('launch_timeout', native.open_browser, self.name,
                                  'https://example.org/', timeout=.001)
        self.assertEqual(error.details['url_delivery'], 'uncertain')
        self.assertEqual(len(self.launches), 1)
        self.assertTrue((self.profile / 'Default').exists())

    def test_lost_launch_reply_never_retries(self):
        def lost(payload):
            self.process()
            raise TimeoutError('lost reply')
        self.launch_effect = lost
        self.assert_error('launch_uncertain', native.open_browser, self.name, 'https://example.org/')
        self.assertEqual(len(self.launches), 1)
        self.assertTrue((self.proc / '42').exists())
        self.assertTrue((self.profile / 'SingletonLock').is_symlink())

    def test_invalid_launch_ack_never_retries(self):
        self.launch_effect = lambda payload: {'pid': 42, 'display': ':0', 'name': 'human'}
        self.assert_error('launch_uncertain', native.open_browser, self.name)
        self.assertEqual(len(self.launches), 1)

    def test_wrong_start_pid_is_uncertain_and_preserved(self):
        def different(payload):
            self.process(pid=43)
            return {'pid': 42, 'display': ':84', 'name': self.name}
        self.launch_effect = different
        self.assert_error('launch_uncertain', native.open_browser, self.name, 'https://example.org/')
        self.assertEqual(len(self.launches), 1)
        self.assertTrue((self.proc / '43').exists())

    def test_unsafe_flags_observed_after_dispatch_report_url_uncertainty(self):
        def unsafe(payload):
            self.process(flags=[*native.FLAGS, '--enable-automation'])
            return {'pid': 42, 'display': ':84', 'name': self.name}
        self.launch_effect = unsafe
        error = self.assert_error('browser_not_native', native.open_browser, self.name, 'https://example.org/')
        self.assertEqual(error.details['url_delivery'], 'uncertain')
        self.assertEqual(error.details['launch_pid'], 42)
        self.assertEqual(len(self.launches), 1)
        self.assertTrue((self.proc / '42').exists())

    def test_existing_owner_change_does_not_restart_or_repeat_url(self):
        self.process()
        def changed(payload):
            self.process(pid=43)
            return {'pid': 43, 'display': ':84', 'name': self.name}
        self.launch_effect = changed
        self.assert_error('launch_uncertain', native.open_browser, self.name, 'https://example.org/')
        self.assertEqual(len(self.launches), 1)
        self.assertTrue((self.proc / '42').exists())
        self.assertTrue((self.proc / '43').exists())

    def test_cli_json_and_nonzero_errors(self):
        with redirect_stdout(io.StringIO()) as output:
            code = native.main(['status', self.name])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])
        with redirect_stdout(io.StringIO()) as output:
            code = native.main(['open', self.name, '--no-sandbox'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['error']['code'], 'arguments_invalid')
        self.start.assert_not_called()

    def test_cli_start_subprocess_failure_is_json_without_launch(self):
        self.start.side_effect = subprocess.CalledProcessError(1, ['systemctl', '--user', 'start', 'agent-bench@native-test.service'])
        with redirect_stdout(io.StringIO()) as output:
            code = native.main(['open', self.name, 'https://example.org/'])
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'native_error')
        self.assertTrue(result['session_preserved'])
        self.start.assert_called_once_with(self.name)
        self.assertEqual(self.launches, [])
        native.control.assert_not_called()

    def test_mutations_serialized_without_wait_or_second_launch(self):
        with native.operation_lock(self.runtime):
            self.assert_error('operation_in_progress', native.open_browser, self.name)
        self.assertEqual(self.launches, [])


if __name__ == '__main__':
    unittest.main()
