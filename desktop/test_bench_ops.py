import os
from contextlib import nullcontext
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import bench_ops as ops
from bench_hub_mcp import HUB_TOOLS, handle_hub, inject_bench_schema


class ChromiumFlags(unittest.TestCase):
    def test_browser_always_uses_bench_directory(self):
        argv = ops.chromium_argv('/tmp/state')
        self.assertIn('--user-data-dir=/tmp/state/chromium', argv)
        self.assertIn('--password-store=basic', argv)
        self.assertEqual(argv[-1], 'about:blank')

    def test_existing_browser_only_receives_owned_url(self):
        snap = {'status': 'conectado', 'port': 4321, 'lives_in': 'teste'}
        with patch.object(ops, 'control', return_value=nullcontext()), \
             patch.object(ops, 'require_bench_profile'), \
             patch.object(ops, 'bench_browser_snapshot', return_value=snap), \
             patch.object(ops, 'open_personal_tab') as new_tab, \
             patch.object(ops, 'request') as request:
            self.assertEqual(ops.open_browser('teste', {'state': '/tmp/bench'}, ['https://example.com/']), snap)
            new_tab.assert_called_once_with(snap, 'https://example.com/')
            request.assert_not_called()

    def test_launch_never_passes_task_url_until_ownership_verified(self):
        connected = {'status': 'conectado', 'port': 1, 'profile': 'bancada', 'lives_in': 'teste'}
        with patch.object(ops, 'control', return_value=nullcontext()), \
             patch.object(ops, 'require_bench_profile'), \
             patch.object(ops, 'bench_browser_snapshot', side_effect=[{'status': 'fechado'}, connected, connected]), \
             patch.object(ops, 'open_personal_tab') as new_tab, \
             patch.object(ops, 'request') as request:
            ops.open_browser('teste', {'state': '/tmp/bench'}, ['https://example.com/'])
            self.assertIn('--user-data-dir=/tmp/bench/chromium', request.call_args.args[1]['argv'])
            self.assertEqual(request.call_args.args[1]['argv'][-1], 'about:blank')
            new_tab.assert_called_once_with(connected, 'https://example.com/')

    def test_no_cdp_does_not_launch_over_existing_process(self):
        with patch.object(ops, 'control', return_value=nullcontext()), \
             patch.object(ops, 'require_bench_profile'), \
             patch.object(ops, 'bench_browser_snapshot', return_value={'status': 'fechado', 'pid': 5}), \
             patch.object(ops, 'request') as request:
            with self.assertRaises(RuntimeError):
                ops.open_browser('teste', {'state': '/tmp/bench'})
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


class Reaper(unittest.TestCase):
    def test_never_reaps_padrao(self):
        self.assertFalse(ops.should_reap('padrao'))

    def test_busy_chromium_is_kept(self):
        with patch.object(ops, 'chromium_busy', return_value=True), \
             patch.object(ops, 'last_activity', return_value=0):
            self.assertFalse(ops.should_reap('dailywork-candidaturas', now=time.time()))

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
