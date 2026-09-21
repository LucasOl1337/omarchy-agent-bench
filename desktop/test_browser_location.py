import tempfile
from contextlib import nullcontext
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import bench_ops as ops


class BrowserLocation(unittest.TestCase):
    def test_stale_local_lock_allows_restart_without_deleting_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lock = root / 'SingletonLock'
            lock.symlink_to('testhost-42')
            with patch.object(ops, 'PROC', root / 'proc'), \
                 patch.object(ops.socket, 'gethostname', return_value='testhost'):
                self.assertIsNone(ops.profile_process(root))
            self.assertEqual(lock.readlink(), Path('testhost-42'))

    def test_foreign_host_lock_still_blocks_even_without_local_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'SingletonLock').symlink_to('another-host-42')
            with patch.object(ops, 'PROC', root / 'proc'), \
                 patch.object(ops.socket, 'gethostname', return_value='testhost'):
                with self.assertRaisesRegex(RuntimeError, 'LOCALIZACAO_INDETERMINADA'):
                    ops.profile_process(root)

    def test_endpoint_inspection_does_not_enumerate_tabs(self):
        with tempfile.TemporaryDirectory() as folder:
            endpoint = Path(folder) / 'chromium/DevToolsActivePort'
            endpoint.parent.mkdir()
            endpoint.write_text('9222\n/devtools/browser/test\n')
            version = {'Browser': 'Chromium', 'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/browser/test'}
            with patch.object(ops, '_opener') as opener:
                opener.return_value.open.return_value = io.StringIO(json.dumps(version))
                result = ops.cdp_snapshot(folder, include_pages=False)
                opener.return_value.open.assert_called_once_with('http://127.0.0.1:9222/json/version', timeout=2)
            self.assertEqual(result['status'], 'conectado')

    def test_stale_port_cannot_bind_another_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            endpoint = Path(folder) / 'chromium/DevToolsActivePort'
            endpoint.parent.mkdir()
            endpoint.write_text('9222\n/devtools/browser/old\n')
            with patch.object(ops, '_opener') as opener:
                opener.return_value.open.return_value = io.StringIO(json.dumps({'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/browser/new'}))
                self.assertNotEqual(ops.cdp_snapshot(folder)['status'], 'conectado')
                self.assertEqual(opener.return_value.open.call_count, 1)

    def test_foreign_browser_never_receives_a_tab_or_a_snapshot_request(self):
        for location in (None, 'other-bench'):
            with self.subTest(location=location), \
                 patch.object(ops, 'control', return_value=nullcontext()), \
                 patch.object(ops, 'require_bench_profile'), \
                 patch.object(ops, 'profile_process', return_value={'pid': 7, 'bench': location}), \
                 patch.object(ops, 'cdp_snapshot', return_value={'status': 'conectado', 'port': 4321}) as snapshot, \
                 patch.object(ops, 'open_personal_tab') as tab, \
                 patch.object(ops, 'request') as request:
                with self.assertRaisesRegex(RuntimeError, 'FORA_DA_BANCADA'):
                    ops.open_browser('test-bench', {'state': '/tmp/state'}, ['https://example.com/'])
                snapshot.assert_not_called()
                tab.assert_not_called()
                request.assert_not_called()

    def test_status_does_not_expose_foreign_endpoint(self):
        with patch.object(ops, 'profile_process', return_value={'pid': 7, 'bench': None}), \
             patch.object(ops, 'cdp_snapshot') as snapshot:
            result = ops.bench_browser_snapshot('test-bench')
        self.assertEqual(result['status'], 'bloqueado')
        self.assertNotIn('browser_url', result)
        snapshot.assert_not_called()

    def test_process_identity_comes_from_profile_lock_not_first_chromium(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = root / 'chromium'
            profile.mkdir()
            (profile / 'SingletonLock').symlink_to('testhost-42')
            proc = root / 'proc/42'
            proc.mkdir(parents=True)
            (proc / 'cmdline').write_bytes(b'/usr/lib/chromium/chromium\0--ozone-platform=wayland\0')
            (proc / 'cgroup').write_text('0::/app-org.chromium.Chromium-42.scope\n')
            with patch.object(ops, 'PROC', root / 'proc'), \
                 patch.object(ops.socket, 'gethostname', return_value='testhost'):
                self.assertEqual(ops.profile_process(profile), {'pid': 42, 'bench': None})


if __name__ == '__main__':
    unittest.main()
