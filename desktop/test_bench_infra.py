"""Control plane and CUA lifetime regressions; no desktop input is used."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import uuid

import bench_ops as ops
from bench_hub_mcp import CuaPool


class UserManagerEnvironment(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.runtime = self.root / str(os.getuid())
        self.runtime.mkdir(mode=0o700)
        self.bus = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.bus.close)
        self.bus.bind(str(self.runtime / 'bus'))
        self.patcher = patch.object(ops, 'USER_RUNTIME_ROOT', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_cold_harness_discovers_uid_socket_without_global_mutation(self):
        with patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=True):
            env = ops.systemd_env()
            self.assertEqual(str(self.runtime), env['XDG_RUNTIME_DIR'])
            self.assertEqual(f'unix:path={self.runtime}/bus', env['DBUS_SESSION_BUS_ADDRESS'])
            self.assertNotIn('DBUS_SESSION_BUS_ADDRESS', os.environ)

    def test_nested_bench_keeps_its_bus_while_systemctl_uses_manager(self):
        nested = {'XDG_RUNTIME_DIR': '/bench/run', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/bench/bus', 'DISPLAY': ':88'}
        with patch.dict(os.environ, nested, clear=True):
            env = ops.systemd_env()
            self.assertNotEqual(nested['DBUS_SESSION_BUS_ADDRESS'], env['DBUS_SESSION_BUS_ADDRESS'])
            self.assertEqual(nested, dict(os.environ))
            self.assertEqual(':88', env['DISPLAY'])

    def test_rejects_non_socket_or_symlink_bus(self):
        endpoint = self.runtime / 'bus'
        endpoint.unlink()
        endpoint.write_text('not a bus')
        with self.assertRaisesRegex(RuntimeError, 'USER_BUS_INDISPONIVEL'):
            ops.systemd_env()
        endpoint.unlink()
        endpoint.symlink_to(self.root / 'foreign-bus')
        with self.assertRaisesRegex(RuntimeError, 'USER_BUS_INDISPONIVEL'):
            ops.systemd_env()

    def test_rejects_writable_runtime(self):
        self.runtime.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, 'USER_BUS_INDISPONIVEL'):
            ops.systemd_env()

    def test_start_passes_recovered_environment_to_systemctl_only(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(ops, 'request', side_effect=[OSError(), {'name': 'fixture'}]), \
             patch.object(ops, 'claim_owner'), patch.object(ops, 'touch_activity'), \
             patch.object(ops.subprocess, 'run') as run:
            ops.start('fixture')
            self.assertEqual(['systemctl', '--user', 'start', 'agent-bench@fixture.service'], run.call_args.args[0])
            self.assertEqual(str(self.runtime), run.call_args.kwargs['env']['XDG_RUNTIME_DIR'])


class CuaLifecycle(unittest.TestCase):
    def test_driver_gets_bench_environment_and_dependency_not_human_context(self):
        app_env = {'DISPLAY': ':88', 'XDG_RUNTIME_DIR': '/bench/run',
                   'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/bench/bus',
                   'AGENT_BENCH_NAME': 'fixture', 'SECRET_TOKEN': 'do-not-forward'}
        manager = {'XDG_RUNTIME_DIR': '/run/user/test', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/manager/bus'}
        with patch('bench_cua_env.validate_cua_env'), \
             patch.object(ops, 'systemd_env', return_value=manager), \
             patch.object(ops.shutil, 'which', return_value='/usr/bin/cua-driver'), \
             patch.object(ops.subprocess, 'Popen') as popen:
            ops.launch_cua('fixture', app_env)
            argv = popen.call_args.args[0]
            self.assertIn('--property=BindsTo=agent-bench@fixture.service', argv)
            self.assertIn('--property=After=agent-bench@fixture.service', argv)
            self.assertIn('DBUS_SESSION_BUS_ADDRESS=unix:path=/bench/bus', argv)
            self.assertIn('DISPLAY=:88', argv)
            self.assertIn('-i', argv)
            self.assertFalse(any('SECRET_TOKEN' in arg for arg in argv))
            self.assertEqual(manager, popen.call_args.kwargs['env'])

    def test_initialize_failure_stops_the_driver_it_started(self):
        proc = MagicMock()
        with patch('bench_hub_mcp.ensure', return_value={'env': {}}), \
             patch('bench_hub_mcp.launch_cua', return_value=proc), \
             patch('bench_hub_mcp.stop_cua') as stop, \
             patch.object(CuaPool, '_initialize', side_effect=RuntimeError('bad initialize')):
            with self.assertRaisesRegex(RuntimeError, 'bad initialize'):
                CuaPool().driver('fixture')
            stop.assert_called_once_with(proc)

    def test_pool_stop_does_not_stop_neighbors(self):
        pool = CuaPool()
        first, neighbor = MagicMock(), MagicMock()
        pool.drivers = {'first': {'proc': first}, 'neighbor': {'proc': neighbor}}
        with patch('bench_hub_mcp.stop_cua') as stop:
            pool._stop('first')
            stop.assert_called_once_with(first)
            self.assertIs(neighbor, pool.drivers['neighbor']['proc'])


class CuaTimeouts(unittest.TestCase):
    FAKE = '''import json, pathlib, sys, time
mode, log = sys.argv[1:]
for line in sys.stdin:
    msg = json.loads(line)
    method = msg['method']
    if method == 'initialize':
        if mode == 'silent_init':
            continue
        if mode == 'partial_init':
            print('{"jsonrpc":', end='', flush=True)
            continue
    if 'id' not in msg:
        continue
    if method == 'tools/list' and mode == 'silent_list':
        continue
    if method == 'tools/call':
        with open(log, 'a') as out:
            out.write('call\\n')
        if mode == 'silent_call':
            continue
        if mode == 'eof_call':
            sys.exit(0)
        if mode == 'partial_call':
            print('{"jsonrpc":', end='', flush=True)
            continue
        if mode == 'notify_call':
            while True:
                print(json.dumps({'jsonrpc':'2.0','method':'notifications/progress'}), flush=True)
                time.sleep(.005)
    result = {'tools': []} if method == 'tools/list' else {'ok': True, 'text': 'ação'}
    notice = json.dumps({'jsonrpc':'2.0','method':'notifications/progress'})
    response = json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}, ensure_ascii=False)
    print(notice + '\\n' + response, flush=True)
    if mode == 'blocked_input' and method == 'tools/list':
        time.sleep(30)
'''

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.processes = []
        self.stopped = []
        self.modes = {}
        self.pool = CuaPool(init_timeout=.12, call_timeout=.12)
        for target, value in [('ensure', lambda *a, **k: {'env': {}}),
                              ('launch_cua', self.launch), ('stop_cua', self.stop)]:
            patcher = patch('bench_hub_mcp.' + target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.cleanup)

    def launch(self, name, env):
        mode = self.modes.get(name, 'healthy')
        proc = subprocess.Popen([sys.executable, '-u', '-c', self.FAKE, mode,
                                 str(Path(self.folder.name) / (name + '.log'))],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        self.processes.append(proc)
        return proc

    def stop(self, proc):
        self.stopped.append(proc)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
        proc.stdin.close()
        proc.stdout.close()

    def cleanup(self):
        self.pool.close()
        for proc in self.processes:
            self.stop(proc)

    @staticmethod
    def message(tool='get_screen_size', **arguments):
        return {'jsonrpc': '2.0', 'id': 77, 'method': 'tools/call',
                'params': {'name': tool, 'arguments': arguments}}

    def test_silent_partial_initialize_and_tools_list_have_one_deadline(self):
        for mode, phase in [('silent_init', 'initialize'), ('partial_init', 'initialize'),
                            ('silent_list', 'tools/list')]:
            with self.subTest(mode=mode):
                self.modes['first'] = mode
                start = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, 'CUA_INIT_TIMEOUT: ' + phase):
                    self.pool.driver('first')
                self.assertLess(time.monotonic() - start, 1)
                self.assertNotIn('first', self.pool.drivers)
                self.assertIsNotNone(self.processes[-1].poll())

    def test_mutation_timeout_is_uncertain_and_not_retried(self):
        self.modes['first'] = 'silent_call'
        with self.assertRaisesRegex(RuntimeError, 'CUA_RESULTADO_INCERTO'):
            self.pool.call('first', self.message('click'))
        self.assertEqual('call\n', (Path(self.folder.name) / 'first.log').read_text())
        self.assertEqual(1, len(self.processes))
        self.assertNotIn('first', self.pool.drivers)
        self.modes['first'] = 'healthy'
        reply = self.pool.call('first', self.message())
        self.assertEqual('ação', reply['result']['text'])
        self.assertEqual(2, len(self.processes))

    def test_eof_after_mutation_is_also_uncertain(self):
        self.modes['first'] = 'eof_call'
        with self.assertRaisesRegex(RuntimeError, 'CUA_RESULTADO_INCERTO'):
            self.pool.call('first', self.message('click'))

    def test_read_timeout_partial_frame_and_notifications_are_bounded(self):
        for mode in ('silent_call', 'partial_call', 'notify_call'):
            with self.subTest(mode=mode):
                self.modes['first'] = mode
                start = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, 'CUA_TIMEOUT:'):
                    self.pool.call('first', self.message())
                self.assertLess(time.monotonic() - start, 1)
                self.assertNotIn('first', self.pool.drivers)

    def test_nonreading_driver_cannot_block_request_write(self):
        self.modes['first'] = 'blocked_input'
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'CUA_RESULTADO_INCERTO'):
            self.pool.call('first', self.message('type_text', text='x' * (1024 * 1024)))
        self.assertLess(time.monotonic() - start, 1)

    def test_silent_driver_does_not_lock_or_kill_neighbor(self):
        self.pool.call_timeout = .3
        self.modes['first'] = 'silent_call'
        neighbor = self.pool.driver('neighbor')['proc']
        errors = []
        def first_call():
            try:
                self.pool.call('first', self.message())
            except RuntimeError as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=first_call)
        thread.start()
        try:
            log = Path(self.folder.name) / 'first.log'
            deadline = time.monotonic() + 1
            while not log.exists() and time.monotonic() < deadline:
                time.sleep(.005)
            reply = self.pool.call('neighbor', self.message())
            self.assertTrue(reply['result']['ok'])
            self.assertTrue(thread.is_alive())
        finally:
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors[0].startswith('CUA_TIMEOUT:'))
        self.assertNotIn(neighbor, self.stopped)
        self.assertIsNone(neighbor.poll())


@unittest.skipUnless(os.environ.get('AGENT_BENCH_RUN_SYSTEMD_TESTS') == '1',
                     'opt in to transient, non-graphical user units')
class RealSystemdLifecycle(unittest.TestCase):
    def test_stopping_parent_kills_only_its_driver_and_preserves_neighbor(self):
        tag = uuid.uuid4().hex[:10]
        units = {name: f'agent-bench-infra-test-{tag}-{name}.service' for name in ('first', 'neighbor')}
        pool = CuaPool()
        manager = ops.systemd_env()
        with tempfile.TemporaryDirectory() as folder:
            driver = Path(folder) / 'cua-driver'
            driver.write_text('#!' + sys.executable + '\n' + '''import json, os, pathlib, sys
for line in sys.stdin:
    msg = json.loads(line)
    if 'id' not in msg:
        continue
    result = {'tools': []} if msg['method'] == 'tools/list' else {
        'pid': os.getpid(), 'cgroup': pathlib.Path('/proc/self/cgroup').read_text(),
        'display': os.getenv('DISPLAY'), 'bus': os.getenv('DBUS_SESSION_BUS_ADDRESS')}
    print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}), flush=True)
''')
            driver.chmod(0o700)
            try:
                for unit in units.values():
                    subprocess.run(['systemd-run', '--user', '--quiet', '--collect', '--unit=' + unit,
                                    '--property=RuntimeMaxSec=60', '/usr/bin/sleep', '60'],
                                   env=manager, check=True, timeout=10)
                # This tests unit cleanup with JSON-only fakes, not endpoint validation.
                with patch('bench_cua_env.validate_cua_env'), \
                     patch.object(ops, 'bench_service', side_effect=lambda name: units[name]), \
                     patch.object(ops.shutil, 'which', return_value=str(driver)), \
                     patch('bench_hub_mcp.ensure', side_effect=lambda name, **kwargs: {
                         'env': {'AGENT_BENCH_NAME': name, 'DISPLAY': ':188',
                                 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/fixture/bus'}}):
                    first = pool.driver('first')['proc']
                    neighbor = pool.driver('neighbor')['proc']
                    msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {}}
                    before = pool.call('first', msg)['result']
                    self.assertIn(first.bench_cua_unit, before['cgroup'])
                    self.assertEqual(':188', before['display'])
                    self.assertEqual('unix:path=/fixture/bus', before['bus'])
                    subprocess.run(['systemctl', '--user', 'stop', units['first']],
                                   env=manager, check=True, timeout=10)
                    first.wait(timeout=8)
                    self.assertFalse(Path('/proc').joinpath(str(before['pid'])).exists())
                    self.assertIsNone(neighbor.poll())
                    after = pool.call('neighbor', msg)['result']
                    self.assertIn(neighbor.bench_cua_unit, after['cgroup'])
                    print('systemd lifecycle: first driver gone; neighbor responds in its own cgroup')
                    neighbor.kill()
                    neighbor.wait(timeout=5)
                    pool._stop('neighbor')
                    self.assertFalse(Path('/proc').joinpath(str(after['pid'])).exists())
                    print('systemd lifecycle: cleanup also removes service after pipe client dies')
            finally:
                pool.close()
                subprocess.run(['systemctl', '--user', 'stop', *units.values()],
                               env=manager, check=False, capture_output=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
