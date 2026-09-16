import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import bench_ops as ops
from bench_hub_mcp import HUB_TOOLS, handle_hub, inject_bench_schema


class ChromiumFlags(unittest.TestCase):
    def test_password_store_is_basic(self):
        argv = ops.chromium_argv('/tmp/state')
        self.assertIn('--password-store=basic', argv)
        self.assertTrue(any(a.startswith('--user-data-dir=') for a in argv))
        self.assertEqual(argv[-1], 'about:blank')


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


class Reaper(unittest.TestCase):
    def test_never_reaps_padrao(self):
        self.assertFalse(ops.should_reap('padrao'))

    def test_busy_chromium_is_kept(self):
        with patch.object(ops, 'chromium_busy', return_value=True), \
             patch.object(ops, 'last_activity', return_value=0):
            self.assertFalse(ops.should_reap('login-job', now=time.time()))

    def test_idle_blank_is_reaped(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(ops, 'RUNTIME', Path(folder)), \
                 patch.object(ops, 'chromium_busy', return_value=False), \
                 patch.object(ops, 'last_activity', return_value=1):
                self.assertTrue(ops.should_reap('idle-job', now=1 + ops.IDLE_SECONDS + 10))


class Gc(unittest.TestCase):
    def test_keep_and_fresh_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(ops, 'STATE', root), patch.object(ops, 'RUNTIME', Path('/tmp/no-runtime')):
                keep = root / 'login-job'
                keep.mkdir()
                (keep / '.keep').write_text('login')
                fresh = root / 'fresh-task'
                fresh.mkdir()
                old = root / 'old-promo'
                old.mkdir()
                os.utime(old, (1, 1))
                result = ops.gc_sessions(days=1, apply=False)
                names = {row['name']: row['reason'] for row in result['kept']}
                self.assertEqual(names['login-job'], '.keep')
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
