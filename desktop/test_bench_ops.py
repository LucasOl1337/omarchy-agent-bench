import os
from contextlib import nullcontext
import tempfile
import time
import unittest
from subprocess import CompletedProcess
from pathlib import Path
from unittest.mock import patch

import bench_ops as ops
from bench_hub_mcp import HUB_TOOLS, handle_hub, inject_bench_schema


class ChromiumFlags(unittest.TestCase):
    def test_browser_always_uses_bench_directory(self):
        argv = ops.chromium_argv('/tmp/state', port=19080)
        self.assertIn('--user-data-dir=/tmp/state/chromium', argv)
        self.assertIn('--password-store=basic', argv)
        self.assertEqual(argv[-1], 'about:blank')

    def test_argv_avoids_automation_markers(self):
        argv = ops.chromium_argv('/tmp/state', port=19080)
        self.assertIn('--remote-debugging-port=19080', argv)
        self.assertNotIn('--remote-debugging-port=0', argv)
        self.assertNotIn('--disable-gpu', argv)

    def test_cdp_port_follows_display(self):
        self.assertEqual(ops.cdp_port({'display': ':93'}), 19093)
        with self.assertRaises(RuntimeError):
            ops.cdp_port({'display': None})

    def test_existing_browser_only_receives_owned_url(self):
        snap = {'status': 'conectado', 'port': 4321, 'lives_in': 'teste'}
        with patch.object(ops, 'control', return_value=nullcontext()), \
             patch.object(ops, 'browser_launch_lock', return_value=nullcontext()), \
             patch.object(ops, 'provide_bench_profile'), \
             patch.object(ops, '_listener_inodes', return_value=set()), \
             patch.object(ops, 'publish_endpoint', return_value=True), \
             patch.object(ops, 'bench_browser_snapshot', return_value=snap), \
             patch.object(ops, 'open_personal_tab') as new_tab, \
             patch.object(ops, 'request') as request:
            self.assertEqual(ops.open_browser('teste', {'state': '/tmp/bench', 'display': ':80'}, ['https://example.com/']), snap)
            new_tab.assert_called_once_with(snap, 'https://example.com/')
            request.assert_not_called()

    def test_launch_never_passes_task_url_until_ownership_verified(self):
        connected = {'status': 'conectado', 'port': 1, 'profile': 'bancada', 'lives_in': 'teste'}
        with patch.object(ops, 'control', return_value=nullcontext()), \
             patch.object(ops, 'browser_launch_lock', return_value=nullcontext()), \
             patch.object(ops, 'provide_bench_profile'), \
             patch.object(ops, '_listener_inodes', return_value=set()), \
             patch.object(ops, 'publish_endpoint', return_value=True), \
             patch.object(ops, 'bench_browser_snapshot', side_effect=[{'status': 'fechado'}, connected, connected]), \
             patch.object(ops, 'open_personal_tab') as new_tab, \
             patch.object(ops, 'request') as request:
            ops.open_browser('teste', {'state': '/tmp/bench', 'display': ':80'}, ['https://example.com/'])
            self.assertIn('--user-data-dir=/tmp/bench/chromium', request.call_args.args[1]['argv'])
            self.assertEqual(request.call_args.args[1]['argv'][-1], 'about:blank')
            new_tab.assert_called_once_with(connected, 'https://example.com/')

    def test_no_cdp_does_not_launch_over_existing_process(self):
        with patch.object(ops, 'control', return_value=nullcontext()), \
             patch.object(ops, 'browser_launch_lock', return_value=nullcontext()), \
             patch.object(ops, 'provide_bench_profile'), \
             patch.object(ops, '_listener_inodes', return_value=set()), \
             patch.object(ops, 'publish_endpoint', return_value=True), \
             patch.object(ops, 'bench_browser_snapshot', return_value={'status': 'fechado', 'pid': 5}), \
             patch.object(ops, 'request') as request:
            with self.assertRaises(RuntimeError):
                ops.open_browser('teste', {'state': '/tmp/bench', 'display': ':80'})
            request.assert_not_called()


class Owner(unittest.TestCase):
    def test_infer_from_env(self):
        with patch.dict(os.environ, {'AGENT_BENCH_OWNER': 'cursor-test'}, clear=False):
            self.assertEqual(ops.infer_owner(), 'cursor-test')
        with patch.dict(os.environ, {'AGENT_BENCH_OWNER': '', 'CURSOR_TRACE_ID': 'abc'}, clear=False):
            os.environ.pop('AGENT_BENCH_OWNER', None)
            self.assertEqual(ops.infer_owner(), 'cursor')


class Cdp(unittest.TestCase):
    def test_invalid_port_does_not_open_network(self):
        with tempfile.TemporaryDirectory() as folder:
            endpoint = Path(folder) / 'chromium/DevToolsActivePort'
            endpoint.parent.mkdir()
            endpoint.write_text('invalid')
            with patch('bench_ops.build_opener') as opener:
                snap = ops.cdp_snapshot(folder)
                opener.assert_not_called()
            self.assertIn('sem conexão', snap['status'])


class ConcurrentLaunch(unittest.TestCase):
    def test_parallel_ensures_launch_chromium_once(self):
        import threading
        launched = []
        results, errors = [], []
        def request(name, payload, **_):
            time.sleep(.05)
            launched.append(payload)
        def snapshot(name, include_pages=True):
            if launched:
                return {'status': 'conectado', 'port': 1, 'lives_in': 'teste'}
            return {'status': 'fechado'}
        def run(state):
            try:
                results.append(ops.open_browser('teste', {'state': state, 'display': ':80'}))
            except Exception as exc:
                errors.append(exc)
        with tempfile.TemporaryDirectory() as state, \
             patch.object(ops, 'control', side_effect=lambda name: nullcontext()), \
             patch.object(ops, 'provide_bench_profile'), \
             patch.object(ops, '_listener_inodes', return_value=set()), \
             patch.object(ops, 'publish_endpoint', return_value=True), \
             patch.object(ops, 'bench_browser_snapshot', side_effect=snapshot), \
             patch.object(ops, 'request', side_effect=request):
            threads = [threading.Thread(target=run, args=(state,))
                       for _ in range(3)]
            for t in threads: t.start()
            for t in threads: t.join()
        self.assertEqual(len(launched), 1)
        self.assertFalse(errors)
        self.assertEqual(len(results), 3)

    def test_argv_hides_crash_restore_bubble(self):
        self.assertIn('--hide-crash-restore-bubble', ops.chromium_argv('/tmp/state', port=19080))


class Reaper(unittest.TestCase):
    def test_padrao_is_not_pinned(self):
        self.assertNotIn('padrao', ops.NEVER_REAP)

    def test_connected_cdp_client_is_kept(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(ops, 'RUNTIME', Path(folder)), \
             patch.object(ops, 'cdp_client_connected', return_value=True), \
             patch.object(ops, 'bench_cpu_busy', return_value=False), \
             patch.object(ops, 'last_activity', return_value=0):
            self.assertFalse(ops.should_reap('dailywork-candidaturas', now=time.time()))

    def test_leftover_tabs_do_not_pin_an_idle_bench(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(ops, 'RUNTIME', Path(folder)), \
             patch.object(ops, 'chromium_busy', return_value=True) as tabs, \
             patch.object(ops, 'native_work_snapshot', return_value={'status': 'busy'}) as native, \
             patch.object(ops, 'cdp_client_connected', return_value=False), \
             patch.object(ops, 'bench_cpu_busy', return_value=False), \
             patch.object(ops, 'last_activity', return_value=1):
            self.assertTrue(ops.should_reap('idle-job', now=1 + ops.IDLE_SECONDS + 10))
            tabs.assert_not_called()
            native.assert_not_called()

    def test_idle_blank_is_reaped(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(ops, 'RUNTIME', Path(folder)), \
                 patch.object(ops, 'cdp_client_connected', return_value=False), \
                 patch.object(ops, 'bench_cpu_busy', return_value=False), \
                 patch.object(ops, 'last_activity', return_value=1):
                self.assertTrue(ops.should_reap('idle-job', now=1 + ops.IDLE_SECONDS + 10))

    def test_cpu_work_prevents_idle_stop(self):
        with patch.object(ops, 'RUNTIME', Path('/tmp/no-runtime')), \
             patch.object(ops, 'cdp_client_connected', return_value=False), \
             patch.object(ops, 'bench_cpu_busy', return_value=True), \
             patch.object(ops, 'last_activity', return_value=1):
            self.assertFalse(ops.should_reap('idle-job', now=1 + ops.IDLE_SECONDS + 10))

    def test_recent_bench_needs_no_process_or_browser_inspection(self):
        with patch.object(ops, 'last_activity', return_value=100), \
             patch.object(ops, 'cdp_client_connected') as browser, \
             patch.object(ops, 'bench_cpu_busy') as cpu:
            self.assertFalse(ops.should_reap('fixture', now=101))
            browser.assert_not_called()
            cpu.assert_not_called()

    def test_cpu_needs_a_full_quiet_window(self):
        with patch.dict(ops._cpu_samples, clear=True):
            ops._cpu_samples['job'] = [(0, 100.0), (ops.IDLE_CPU_WINDOW - 60, 101.0)]
            self.assertTrue(ops.bench_cpu_busy('job', now=ops.IDLE_CPU_WINDOW - 60))
            ops._cpu_samples['job'].append((ops.IDLE_CPU_WINDOW + 5, 105.0))
            self.assertFalse(ops.bench_cpu_busy('job', now=ops.IDLE_CPU_WINDOW + 5))
            ops._cpu_samples['job'].append((ops.IDLE_CPU_WINDOW + 65, 140.0))
            self.assertTrue(ops.bench_cpu_busy('job', now=ops.IDLE_CPU_WINDOW + 65))

    def test_keep_marker_remains_disk_retention_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(ops, 'STATE', root), patch.object(ops, 'RUNTIME', root / 'runtime'), \
                 patch.object(ops, 'last_activity', return_value=1), \
                 patch.object(ops, 'cdp_client_connected', return_value=False), \
                 patch.object(ops, 'bench_cpu_busy', return_value=False):
                ops.keep_session('fixture')
                self.assertTrue(ops.should_reap('fixture', now=1 + ops.IDLE_SECONDS + 10))
                self.assertTrue((root / 'fixture/.keep').is_file())

    def test_unknown_browser_state_is_not_empty(self):
        for snap in ({'status': 'bloqueado'}, {'status': 'endpoint sem conexão'},
                     {'status': 'fechado', 'pid': 123}):
            with self.subTest(snap=snap), patch.object(ops, 'bench_browser_snapshot', return_value=snap):
                self.assertTrue(ops.chromium_busy('fixture'))
        with patch.object(ops, 'bench_browser_snapshot', return_value={'status': 'fechado', 'pid': None}):
            self.assertFalse(ops.chromium_busy('fixture'))


class ChromiumTitle(unittest.TestCase):
    def test_space_joined_main_process_is_found(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = '/home/x/.agents/desktop/sessions/b/chromium'
            main = root / '100'; main.mkdir()
            (main / 'cmdline').write_bytes(('/usr/lib/chromium/chromium --no-first-run --remote-debugging-port=19080 '
                                            f'--user-data-dir={profile} about:blank').encode() + b'\0')
            child = root / '101'; child.mkdir()
            (child / 'cmdline').write_bytes(b'\0'.join([b'/usr/lib/chromium/chromium', b'--type=renderer',
                                                         f'--user-data-dir={profile}'.encode()]) + b'\0')
            with patch.object(ops, 'PROC', root):
                self.assertEqual(ops._main_chromium_pids(Path(profile)), [100])


class Profiles(unittest.TestCase):
    def test_scratch_bench_gets_ephemeral_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = ops.provide_bench_profile(Path(folder) / 'teste-x')
            self.assertTrue((profile / ops.EPHEMERAL_MARK).is_file())

    def test_vault_profile_is_kept_not_ephemeral(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = ops.provide_bench_profile(Path(folder) / ops.VAULT)
            self.assertFalse((profile / ops.EPHEMERAL_MARK).exists())
            self.assertTrue((Path(folder) / ops.VAULT / '.keep').is_file())

    def test_existing_profile_is_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'antiga/chromium/Default').mkdir(parents=True)
            profile = ops.provide_bench_profile(Path(folder) / 'antiga')
            self.assertFalse((profile / ops.EPHEMERAL_MARK).exists())

    def test_close_removes_only_ephemeral_profiles(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(ops, 'STATE', Path(folder)), \
             patch.object(ops, 'profile_process', return_value=None), \
             patch.object(ops, '_main_chromium_pids', return_value=[]):
            scratch = ops.provide_bench_profile(Path(folder) / 'rascunho')
            kept = Path(folder) / 'antiga/chromium'
            (kept / 'Default').mkdir(parents=True)
            self.assertTrue(ops.close_browser('rascunho')['profile_removed'])
            self.assertFalse(scratch.exists())
            self.assertFalse(ops.close_browser('antiga')['profile_removed'])
            self.assertTrue(kept.exists())


class Owner(unittest.TestCase):
    def fake_proc(self, root, chain):
        for pid, parent, argv in chain:
            folder = root / str(pid)
            folder.mkdir()
            (folder / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv) + b'\0')
            (folder / 'stat').write_text(f'{pid} (x) S {parent} 0 0')

    def test_harness_found_among_ancestors(self):
        cases = {
            'codex': [(30, 20, ['python3', 'agent-bench']), (20, 10, ['/usr/bin/bash']), (10, 1, ['/opt/codex/codex', 'exec'])],
            'jcode': [(30, 10, ['bash']), (10, 1, ['/home/lol/.jcode/builds/jcode-linux-x86_64.bin'])],
            'dailywork': [(30, 10, ['node']), (10, 1, ['/x/electron', '/home/lol/Projects/DailyWork/Daily Work app/electron/main.cjs'])],
        }
        for expected, chain in cases.items():
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder:
                self.fake_proc(Path(folder), chain)
                with patch.object(ops, 'PROC', Path(folder)):
                    self.assertEqual(ops.owner_from_ancestors(30), expected)

    def test_unrelated_chain_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fake_proc(Path(folder), [(30, 10, ['bash', '--cursor-style']), (10, 1, ['systemd'])])
            with patch.object(ops, 'PROC', Path(folder)):
                self.assertIsNone(ops.owner_from_ancestors(30))


class NativeRetention(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.proc = self.root / 'proc'
        self.cgroup_path = '/user.slice/agent-bench@fixture.service'
        self.cgroup = self.root / 'cgroup' / self.cgroup_path.lstrip('/')
        self.cgroup.mkdir(parents=True)
        self.pids = []
        for attribute, value in [('PROC', self.proc), ('CGROUP', self.root / 'cgroup'),
                                 ('STATE', self.root / 'state')]:
            patcher = patch.object(ops, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.info = {'name': 'fixture', 'server_pid': 100}
        patcher = patch.object(ops, 'request', return_value=self.info)
        self.request = patcher.start()
        self.addCleanup(patcher.stop)
        self.add_process(100, ['/usr/bin/Xvnc', ':188'])
        self.add_process(101, ['/usr/bin/python3', str(ops.BASE / 'desktop/welcome.py'),
                               'fixture', str(ops.STATE / 'fixture')])
        self.add_process(102, ['/usr/bin/openbox', '--config-file', str(ops.BASE / 'desktop/openbox.xml')])

    def add_process(self, pid, argv, parent=1, cgroup=None, executable=None):
        folder = self.proc / str(pid)
        folder.mkdir(parents=True)
        (folder / 'cmdline').write_bytes(b'\0'.join(arg.encode() for arg in argv) + b'\0')
        (folder / 'stat').write_text(f'{pid} (test process) S {parent} ' + '0 ' * 17 + f'{pid} 0\n')
        (folder / 'cgroup').write_text('0::' + (cgroup or self.cgroup_path) + '\n')
        executable = executable or argv[0]
        binary = self.root / 'executables' / executable.lstrip('/')
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.touch(exist_ok=True)
        (folder / 'exe').symlink_to(binary)
        self.pids.append(pid)
        (self.cgroup / 'cgroup.procs').write_text(''.join(f'{item}\n' for item in self.pids))

    def snapshot(self):
        return ops.native_work_snapshot('fixture')

    def test_central_and_infrastructure_are_idle_without_touching_activity(self):
        self.add_process(103, ['/usr/bin/dbus-daemon', '--nofork', '--print-address', '4', '--session'])
        self.add_process(104, ['/usr/bin/python3', str(ops.BASE / 'bin/agent-bench'), '_serve', 'fixture'])
        with patch.object(ops, 'touch_activity') as touch:
            self.assertEqual('idle', self.snapshot()['status'])
            touch.assert_not_called()
            self.request.assert_called_once_with('fixture', {'action': 'status'}, timeout=2)

    def test_editor_terminal_and_windowless_job_are_kept(self):
        for executable in ('mousepad', 'xterm', 'python3'):
            with self.subTest(executable=executable):
                pid = 200 + len(self.pids)
                self.add_process(pid, ['/usr/bin/' + executable, 'private-document-path'])
                result = self.snapshot()
                self.assertEqual('busy', result['status'])
                self.assertIn({'pid': pid, 'executable': executable}, result['processes'])
                self.assertNotIn('private-document-path', str(result))

    def test_blank_verified_browser_tree_is_idle_but_native_child_is_not(self):
        self.add_process(200, ['/usr/lib/chromium/chromium'])
        self.add_process(201, ['/usr/lib/chromium/chromium', '--type=renderer'], parent=200)
        with patch.object(ops, 'profile_process', return_value={'pid': 200, 'bench': 'fixture'}):
            self.assertEqual('idle', self.snapshot()['status'])
            self.add_process(202, ['/usr/bin/document-viewer'], parent=201)
            self.assertEqual('busy', self.snapshot()['status'])

    def test_rewritten_chromium_argv0_is_idle_by_executable_identity(self):
        self.add_process(200, ['/usr/lib/chromium/chromium'])
        self.add_process(201, ['chromium --change-stack-guard-on-fork=enable --fixture-secret'],
                         parent=200, executable='/usr/lib/chromium/chromium')
        with patch.object(ops, 'profile_process', return_value={'pid': 200, 'bench': 'fixture'}):
            self.assertEqual('idle', self.snapshot()['status'])

    def test_busy_diagnostic_uses_exe_basename_not_rewritten_argv0(self):
        self.add_process(200, ['native-editor --fixture-private-value'], executable='/usr/bin/native-editor')
        result = self.snapshot()
        self.assertEqual('busy', result['status'])
        self.assertIn({'pid': 200, 'executable': 'native-editor'}, result['processes'])
        self.assertNotIn('fixture-private', str(result))

    def test_chromium_named_child_with_other_executable_is_still_busy(self):
        self.add_process(200, ['/usr/lib/chromium/chromium'])
        self.add_process(201, ['/usr/lib/chromium/chromium'], parent=200,
                         executable='/other-build/chromium')
        with patch.object(ops, 'profile_process', return_value={'pid': 200, 'bench': 'fixture'}):
            result = self.snapshot()
        self.assertEqual('busy', result['status'])
        self.assertIn({'pid': 201, 'executable': 'chromium'}, result['processes'])

    def test_same_executable_inode_with_an_alias_is_recognized(self):
        self.add_process(200, ['/usr/lib/chromium/chromium'])
        self.add_process(201, ['chromium --fixture-secret'], parent=200,
                         executable='/alias/browser-child')
        alias = self.root / 'executables/alias/browser-child'
        alias.unlink()
        alias.hardlink_to(self.root / 'executables/usr/lib/chromium/chromium')
        with patch.object(ops, 'profile_process', return_value={'pid': 200, 'bench': 'fixture'}):
            self.assertEqual('idle', self.snapshot()['status'])

    def test_profile_process_with_different_executable_is_unknown(self):
        self.add_process(200, ['/usr/lib/chromium/chromium'], executable='/usr/bin/native-job')
        with patch.object(ops, 'profile_process', return_value={'pid': 200, 'bench': 'fixture'}):
            self.assertEqual('unknown', self.snapshot()['status'])

    def test_server_argv0_cannot_substitute_its_real_executable(self):
        path = self.proc / '100/exe'
        path.unlink()
        path.symlink_to(self.root / 'executables/usr/bin/openbox')
        self.assertEqual('unknown', self.snapshot()['status'])

    def test_forged_infrastructure_argv0_does_not_hide_native_work(self):
        self.add_process(200, ['/usr/bin/dbus-daemon', '--session', '--nofork'],
                         executable='/usr/bin/native-job')
        self.assertEqual('busy', self.snapshot()['status'])

    def test_missing_or_unreadable_executable_is_unknown(self):
        (self.proc / '102/exe').unlink()
        self.assertEqual('unknown', self.snapshot()['status'])
        with patch.object(ops.os, 'readlink', side_effect=PermissionError()):
            self.assertEqual('unknown', self.snapshot()['status'])

    def test_deleted_or_non_file_executable_is_unknown(self):
        path = self.proc / '102/exe'
        deleted = self.root / 'executables/openbox (deleted)'
        deleted.touch()
        for target in (deleted, self.root / 'executables'):
            with self.subTest(target=target.name):
                path.unlink()
                path.symlink_to(target)
                self.assertEqual('unknown', self.snapshot()['status'])

    def test_reused_pid_or_changed_executable_is_unknown(self):
        original = ops._cgroup_pids
        for change in ('starttime', 'executable'):
            with self.subTest(change=change):
                def changed_inventory(root):
                    result = original(root)
                    if changed_inventory.calls:
                        if change == 'starttime':
                            path = self.proc / '102/stat'
                            path.write_text(path.read_text().replace('102 0\n', '999 0\n'))
                        else:
                            path = self.proc / '102/exe'
                            path.unlink()
                            path.symlink_to(self.proc / '101/exe')
                    changed_inventory.calls += 1
                    return result
                changed_inventory.calls = 0
                with patch.object(ops, '_cgroup_pids', side_effect=changed_inventory):
                    self.assertEqual('unknown', self.snapshot()['status'])

    def test_native_services_remain_busy_without_whitelist_expansion(self):
        for offset, name in enumerate(('at-spi-bus-launcher', 'at-spi2-registryd',
                                       'xdg-desktop-portal', 'gvfsd', 'chrome_crashpad_handler')):
            self.add_process(220 + offset, ['/usr/bin/' + name])
        result = self.snapshot()
        self.assertEqual('busy', result['status'])
        self.assertEqual(len(result['processes']), 5)

    def test_foreign_server_cgroup_is_refused_without_inventory(self):
        (self.proc / '100/cgroup').write_text('0::/user.slice/agent-bench@neighbor.service\n')
        with patch.object(ops, '_cgroup_pids') as inventory:
            self.assertEqual('unknown', self.snapshot()['status'])
            inventory.assert_not_called()

    def test_neighbor_processes_are_not_consulted(self):
        neighbor = self.root / 'cgroup/user.slice/agent-bench@neighbor.service'
        neighbor.mkdir()
        (neighbor / 'cgroup.procs').write_text('999\n')
        self.assertEqual('idle', self.snapshot()['status'])

    def test_missing_process_and_changed_inventory_are_unknown(self):
        with patch.object(ops, '_cgroup_pids', side_effect=[set(self.pids), {*self.pids, 555}]):
            self.assertEqual('unknown', self.snapshot()['status'])
        (self.proc / '102/cmdline').unlink()
        self.assertEqual('unknown', self.snapshot()['status'])

    def test_unreadable_or_empty_inventory_is_unknown(self):
        with patch.object(ops, '_cgroup_pids', side_effect=PermissionError()):
            self.assertEqual('unknown', self.snapshot()['status'])
        (self.cgroup / 'cgroup.procs').write_text('')
        self.assertEqual('unknown', self.snapshot()['status'])

    def test_subgroup_work_is_included(self):
        self.add_process(203, ['/usr/bin/native-job'], cgroup=self.cgroup_path + '/jobs')
        (self.cgroup / 'cgroup.procs').write_text('100\n101\n102\n')
        child = self.cgroup / 'jobs'
        child.mkdir()
        (child / 'cgroup.procs').write_text('203\n')
        self.assertEqual('busy', self.snapshot()['status'])


class Gc(unittest.TestCase):
    def test_keep_and_fresh_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(ops, 'STATE', root), patch.object(ops, 'RUNTIME', Path('/tmp/no-runtime')), \
                 patch.object(ops, 'systemd_env', return_value={}), \
                 patch.object(ops.subprocess, 'run', return_value=CompletedProcess([], 3, stdout='inactive')):
                keep = root / 'dailywork-candidaturas'
                keep.mkdir()
                (keep / '.keep').write_text('login')
                fresh = root / 'fresh-task'
                fresh.mkdir()
                old = root / 'old-promo'
                old.mkdir()
                os.utime(old, (1, 1))
                result = ops.gc_sessions(days=1, apply=False)
                names = {row['name']: row['reason'] for row in result['kept']}
                self.assertEqual(names['dailywork-candidaturas'], '.keep')
                self.assertTrue(any(row['name'] == 'old-promo' for row in result['removed']))
                self.assertTrue(old.exists())


class Hub(unittest.TestCase):
    def test_hub_tool_names(self):
        names = {t['name'] for t in HUB_TOOLS}
        self.assertTrue({'bench_ensure', 'bench_cdp', 'bench_doctor', 'bench_gc'} <= names)

    def test_inject_bench_schema(self):
        tool = inject_bench_schema({'name': 'left_click', 'inputSchema': {'type': 'object', 'properties': {'x': {'type': 'number'}}}})
        self.assertIn('bench', tool['inputSchema']['properties'])

    def test_gc_hub_is_dry_run(self):
        with patch('bench_hub_mcp.gc_sessions', return_value={'apply': False, 'removed': []}) as gc:
            handle_hub('bench_gc', {})
            gc.assert_called_once_with(days=None, apply=False)


if __name__ == '__main__':
    unittest.main()
