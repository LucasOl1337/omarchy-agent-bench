import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bench_ops as ops


class CdpIdentity(unittest.TestCase):
    def fixture(self, root, *, multiple=False, owner_socket=True):
        state = Path(root)
        profile = state / 'chromium'
        profile.mkdir()
        (profile / 'Default').mkdir()
        (profile / 'SingletonLock').symlink_to(f'{socket.gethostname()}-42')
        (profile / 'DevToolsActivePort').write_text('9222\n/devtools/browser/fixture\n')
        proc = state / 'proc'
        for pid, inode in [(42, '123' if owner_socket else '999'), *(([(43, '123')] if multiple else []))]:
            directory = proc / str(pid)
            (directory / 'fd').mkdir(parents=True)
            (directory / 'cmdline').write_bytes(f'/usr/lib/chromium/chromium\0--user-data-dir={profile}\0'.encode())
            (directory / 'cgroup').write_text('0::/agent-bench@fixture.service\n')
            (directory / 'fd/8').symlink_to(f'socket:[{inode}]')
        (proc / 'net').mkdir()
        (proc / 'net/tcp').write_text('sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n' +
            '0: 0100007F:2406 00000000:0000 0A 0:0 00:00000000 00000000 1000 0 123\n')
        return state, proc

    def test_owner_socket_accepts_endpoint_without_listing_tabs(self):
        with tempfile.TemporaryDirectory() as root:
            state, proc = self.fixture(root)
            version = {'Browser': 'Chromium', 'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/browser/fixture'}
            with patch.object(ops, 'PROC', proc), patch.object(ops, '_opener') as opener:
                opener.return_value.open.return_value = io.StringIO(json.dumps(version))
                result = ops.cdp_snapshot(state, include_pages=False)
                self.assertEqual(result['status'], 'conectado')
                opener.return_value.open.assert_called_once()

    def test_mismatched_socket_rejected_without_network_or_launch(self):
        with tempfile.TemporaryDirectory() as root:
            state, proc = self.fixture(root, owner_socket=False)
            with patch.object(ops, 'PROC', proc), patch.object(ops, '_opener') as opener, patch.object(ops, 'request') as launch:
                result = ops.cdp_snapshot(state, include_pages=False)
                self.assertEqual(result['status'], 'bloqueado')
                self.assertIn('CHROMIUM_PERFIL_AMBIGUO', result['error'])
                opener.assert_not_called()
                launch.assert_not_called()
                self.assertTrue((state / 'chromium/SingletonLock').is_symlink())

    def test_two_main_processes_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as root:
            state, proc = self.fixture(root, multiple=True)
            with patch.object(ops, 'PROC', proc), patch.object(ops, '_opener') as opener:
                result = ops.cdp_snapshot(state, include_pages=False)
                self.assertIn('CHROMIUM_PERFIL_AMBIGUO', result['error'])
                opener.assert_not_called()

    def test_snapshot_blocks_multiple_processes_even_without_endpoint(self):
        with tempfile.TemporaryDirectory() as root:
            state, proc = self.fixture(root, multiple=True)
            (state / 'chromium/DevToolsActivePort').unlink()
            with patch.object(ops, 'PROC', proc), patch.object(ops, 'paths', return_value=(None, state)), patch.object(ops, 'request') as launch:
                result = ops.bench_browser_snapshot('fixture')
                self.assertIn('CHROMIUM_PERFIL_AMBIGUO', result['error'])
                launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
