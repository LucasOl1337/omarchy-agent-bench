"""Only temporary /proc/cgroup fixtures and an owned, protocol-free Unix socket."""
import copy
import os
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import bench_cua_env as guard
import bench_ops as ops


class CuaEnvironment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cua-env-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.proc = self.base / 'proc'
        self.cg = self.base / 'cgroup'
        self.runtime = self.base / 'runtime' / 'fixture'
        (self.runtime / 'run').mkdir(parents=True, mode=0o700)
        (self.runtime / 'Xauthority').touch(mode=0o600)
        self.group = '/user.slice/agent-bench@fixture.service'
        self.root = self.cg / self.group.lstrip('/')
        self.root.mkdir(parents=True)
        self.server = 420042
        self.peer_pid = os.getpid()
        (self.root / 'cgroup.procs').write_text(f'{self.server}\n{self.peer_pid}\n')
        self.process(self.server, 'Xvnc', [':83', '-auth', str(self.runtime / 'Xauthority')])
        self.process(self.peer_pid, 'dbus-daemon', ['--session', '--nofork'])
        self.bus_path = self.base / 'bus'
        self.bus = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.bus.close)
        self.bus.bind(str(self.bus_path))
        self.bus.listen(16)
        self.bus.settimeout(1)
        self.env = {'AGENT_BENCH_NAME': 'fixture', 'DISPLAY': ':83',
                    'XAUTHORITY': str(self.runtime / 'Xauthority'),
                    'XDG_RUNTIME_DIR': str(self.runtime / 'run'),
                    'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(self.bus_path) + ',guid=' + 'a' * 32,
                    'HOME': '/fixture/home', **guard.X11_CONTEXT}
        self.info = {'name': 'fixture', 'display': ':83', 'server_pid': self.server,
                     'env': dict(self.env)}
        for name, value in (('PROC', self.proc), ('CGROUP', self.cg), ('RUNTIME', self.runtime.parent)):
            monkey = patch.object(ops, name, value)
            monkey.start()
            self.addCleanup(monkey.stop)
        monkey = patch.object(ops, 'request', return_value=self.info)
        self.request = monkey.start()
        self.addCleanup(monkey.stop)

    def process(self, pid, executable, arguments, start=123):
        directory = self.proc / str(pid)
        directory.mkdir(parents=True, exist_ok=True)
        binary = self.base / 'bin' / executable
        binary.parent.mkdir(exist_ok=True)
        binary.touch()
        (directory / 'exe').unlink(missing_ok=True)
        (directory / 'exe').symlink_to(binary)
        (directory / 'cmdline').write_bytes(b'\0'.join(os.fsencode(a) for a in [executable, *arguments]) + b'\0')
        (directory / 'cgroup').write_text('0::' + self.group + '\n')
        fields = ['S', '1'] + ['0'] * 17 + [str(start)]
        (directory / 'stat').write_text(str(pid) + ' (fixture) ' + ' '.join(fields))

    def assert_refused(self, env=None):
        with patch.object(ops.subprocess, 'Popen') as spawn, \
             patch.object(ops, 'systemd_env') as manager:
            with self.assertRaisesRegex(guard.CuaEnvironmentError, 'CUA_ENV_UNVERIFIED'):
                ops.launch_cua('fixture', self.env if env is None else env)
            spawn.assert_not_called()
            manager.assert_not_called()

    def assert_socket_closed_without_protocol(self):
        connection, _ = self.bus.accept()
        with connection:
            connection.settimeout(1)
            self.assertEqual(b'', connection.recv(1))

    def test_current_environment_and_real_peer_credentials_accept_without_side_effects(self):
        before = copy.deepcopy(self.env)
        environ = dict(os.environ)
        with patch.object(ops, 'start') as start, patch.object(ops, 'views') as views:
            self.assertIsNone(guard.validate_cua_env('fixture', self.env))
            start.assert_not_called()
            views.assert_not_called()
        self.assertEqual(before, self.env)
        self.assertEqual(environ, dict(os.environ))
        self.request.assert_called_once_with('fixture', {'action': 'status'}, timeout=2)
        self.assert_socket_closed_without_protocol()

    def test_missing_empty_and_non_string_fields_never_launch(self):
        for key in (*guard.IDENTITY_KEYS, *guard.X11_CONTEXT):
            for value in (None, '', ' ', 1):
                with self.subTest(key=key, value=value):
                    env = dict(self.env)
                    if value is None:
                        env.pop(key)
                    else:
                        env[key] = value
                    self.assert_refused(env)
        self.request.assert_not_called()

    def test_wrong_bench_paths_display_and_backend_never_launch(self):
        for key, value in [('AGENT_BENCH_NAME', 'neighbor'), ('DISPLAY', ':0.0'),
                           ('DISPLAY', 'host:83'), ('XAUTHORITY', '/human/.Xauthority'),
                           ('XDG_RUNTIME_DIR', '/run/user/1000'), ('GDK_BACKEND', 'wayland'),
                           ('QT_QPA_PLATFORM', 'wayland'), ('GTK_USE_PORTAL', '1')]:
            with self.subTest(key=key):
                self.assert_refused({**self.env, key: value})
        self.request.assert_not_called()

    def test_status_mismatch_failure_or_unknown_shape_never_launch(self):
        cases = [None, {}, {'name': 'neighbor'}, {**self.info, 'display': ':84'},
                 {**self.info, 'server_pid': True}, {**self.info, 'env': {}},
                 {**self.info, 'env': {**self.env, 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/other'}}]
        for info in cases:
            with self.subTest(info=info):
                self.request.return_value = info
                self.assert_refused()
        self.request.side_effect = TimeoutError('fixture status timeout')
        self.assert_refused()

    def test_unknown_transports_fallback_lists_and_bad_escaping_are_refused(self):
        values = ['autolaunch:', 'unixexec:path=/bin/sh', 'tcp:host=127.0.0.1,port=1',
                  'unix:abstract=/tmp/dbus-private', 'unix:path=/one;unix:path=/two',
                  'unix:path=/one,path=/two', 'unix:path=/one,unknown=x', 'unix:guid=' + 'a' * 32,
                  'unix:path=/one,guid=no', 'unix:path=/bad%xy', 'unix:path=/bad%0',
                  'unix:path=/bad space', 'unix:path=/bad%00', 'unix:path=relative',
                  'unix:path=/one/../two', 'unix:path=/one//two']
        for address in values:
            with self.subTest(address=address):
                self.assert_refused({**self.env, 'DBUS_SESSION_BUS_ADDRESS': address})
        self.request.assert_not_called()

    def test_parser_accepts_percent_escaped_path_and_optional_guid(self):
        self.assertEqual('/tmp/dbus-one', guard._bus_path('unix:path=%2Ftmp%2Fdbus-one'))
        self.assertEqual('/tmp/dbus-one', guard._bus_path('unix:guid=' + 'a' * 32 + ',path=/tmp/dbus-one'))

    def test_auth_symlink_or_writable_path_is_refused(self):
        auth = self.runtime / 'Xauthority'
        auth.chmod(0o666)
        self.assert_refused()
        auth.unlink()
        auth.symlink_to(self.base / 'other-auth')
        self.assert_refused()
        self.request.assert_not_called()

    def test_xvnc_requires_real_executable_display_auth_and_membership(self):
        for executable, args in [('fake', [':83', '-auth', str(self.runtime / 'Xauthority')]),
                                  ('Xvnc', [':84', '-auth', str(self.runtime / 'Xauthority')]),
                                  ('Xvnc', [':83', '-auth', '/human/auth']), ('Xvnc', [':83', '-auth'])]:
            with self.subTest(executable=executable, args=args):
                self.process(self.server, executable, args)
                self.assert_refused()
        self.process(self.server, 'Xvnc', [':83', '-auth', str(self.runtime / 'Xauthority')])
        (self.root / 'cgroup.procs').write_text(str(self.peer_pid) + '\n')
        self.assert_refused()

    def test_peer_from_human_or_neighbor_is_refused_and_socket_closed(self):
        for group in ('/user.slice/human.service', '/user.slice/agent-bench@neighbor.service'):
            with self.subTest(group=group):
                (self.proc / str(self.peer_pid) / 'cgroup').write_text('0::' + group + '\n')
                self.assert_refused()
                self.assert_socket_closed_without_protocol()

    def test_peer_missing_from_cgroup_inventory_is_refused_and_socket_closed(self):
        (self.root / 'cgroup.procs').write_text(str(self.server) + '\n')
        self.assert_refused()
        self.assert_socket_closed_without_protocol()

    def test_server_or_peer_identity_change_is_refused_and_socket_closed(self):
        original = guard._record
        for change in (self.server, self.peer_pid):
            counts = {}
            def record(pid, name, root):
                value = original(pid, name, root)
                counts[pid] = counts.get(pid, 0) + 1
                if pid == change and counts[pid] > 1:
                    value['starttime'] += 1
                return value
            with self.subTest(pid=change), patch.object(guard, '_record', side_effect=record):
                self.assert_refused()
                self.assert_socket_closed_without_protocol()

    def test_connect_timeout_and_wrong_uid_close_socket_and_never_launch(self):
        peer = MagicMock()
        peer.__enter__.return_value = peer
        peer.connect.side_effect = TimeoutError('fixture connection timeout')
        with patch.object(guard.socket, 'socket', return_value=peer):
            self.assert_refused()
        peer.settimeout.assert_called_once_with(1)
        peer.__exit__.assert_called_once()
        peer.reset_mock()
        peer.connect.side_effect = None
        peer.getsockopt.return_value = struct.pack('3i', self.peer_pid, os.getuid() + 1, 0)
        with patch.object(guard.socket, 'socket', return_value=peer):
            self.assert_refused()
        peer.__exit__.assert_called_once()
        peer.send.assert_not_called()
        peer.sendall.assert_not_called()

    def test_valid_environment_reaches_launch_without_changing_it(self):
        with patch.object(ops, 'systemd_env', return_value={}), \
             patch.object(ops.shutil, 'which', return_value='/fixture/cua-driver'), \
             patch.object(ops.subprocess, 'Popen') as launch:
            ops.launch_cua('fixture', self.env)
        argv = launch.call_args.args[0]
        for key, value in self.env.items():
            self.assertIn(key + '=' + value, argv)
        self.assertIn('--property=KillMode=control-group', argv)
        self.assertIn(str(ops.BASE / 'desktop/bench_uinput_guard.py'), argv)
        self.assert_socket_closed_without_protocol()


if __name__ == '__main__':
    unittest.main()
