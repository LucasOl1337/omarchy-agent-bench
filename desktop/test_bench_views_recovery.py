import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import bench_views as v

class ViewerRecovery(unittest.TestCase):
    def setUp(self):
        v.entries.clear(); v.processes.clear(); v.departed_since.clear()
        v.entries['test'] = {'workspace': 6, 'viewer_pid': 123, 'launched': True}
    def probe(self, code):
        p=Mock();p.poll.return_value=code;p.returncode=code;v.processes['test']=p
        with patch.object(v,'live',return_value={}),patch.object(v,'save'):
            return v.ensure('test',reopen=False)
    def test_crash_is_scheduled_for_recovery(self):
        r=self.probe(1)
        self.assertFalse(r['launched']);self.assertIsNone(r['viewer_pid']);self.assertGreater(r['retry_at'],v.time.monotonic())
    def test_normal_close_stays_closed(self):
        r=self.probe(0)
        self.assertTrue(r['launched']);self.assertIsNone(r['viewer_pid'])
    def test_signal_exit_is_recovered(self):
        self.assertFalse(self.probe(-11)['launched'])
    def test_live_viewer_is_preserved(self):
        self.assertEqual(self.probe(None)['viewer_pid'],123)

class VisitAndLeave(unittest.TestCase):
    def setUp(self):
        v.entries.clear(); v.processes.clear(); v.departed_since.clear()
        v.entries['login-app'] = {'workspace': 6, 'viewer_pid': 1, 'launched': True}

    def held(self, runtime):
        (runtime / 'login-app').mkdir()
        (runtime / 'login-app' / 'human-control').touch()

    def test_stays_while_reserved_workspace_is_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder); self.held(runtime)
            with patch.object(v, 'RUNTIME', runtime), \
                 patch.object(v, 'monitors', return_value=[{'focused': True, 'activeWorkspace': {'id': 6}}]), \
                 patch.object(v, 'handoff') as handoff:
                self.assertEqual(v.release_departed(now=10), [])
                handoff.assert_not_called()

    def test_glance_without_assume_does_not_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime / 'login-app').mkdir()
            with patch.object(v, 'RUNTIME', runtime), \
                 patch.object(v, 'monitors', return_value=[{'focused': True, 'activeWorkspace': {'id': 1}}]), \
                 patch.object(v, 'handoff') as handoff:
                self.assertEqual(v.release_departed(now=10), [])
                self.assertEqual(v.release_departed(now=20), [])
                handoff.assert_not_called()

    def test_resumes_after_leaving_reserved_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder); self.held(runtime)
            with patch.object(v, 'RUNTIME', runtime), \
                 patch.object(v, 'monitors', return_value=[{'focused': True, 'activeWorkspace': {'id': 1}}]), \
                 patch.object(v, 'handoff') as handoff:
                self.assertEqual(v.release_departed(now=10), [])
                handoff.assert_not_called()
                self.assertEqual(v.release_departed(now=12), ['login-app'])
                handoff.assert_called_once_with('login-app', False)

    def test_mouse_on_other_monitor_does_not_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder); self.held(runtime)
            with patch.object(v, 'RUNTIME', runtime), \
                 patch.object(v, 'monitors', return_value=[
                     {'focused': False, 'activeWorkspace': {'id': 6}},
                     {'focused': True, 'activeWorkspace': {'id': 2}},
                 ]), \
                 patch.object(v, 'handoff') as handoff:
                self.assertEqual(v.release_departed(now=10), [])
                self.assertEqual(v.release_departed(now=20), [])
                handoff.assert_not_called()

    def test_returning_cancels_leave(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder); self.held(runtime)
            shown = [{'focused': True, 'activeWorkspace': {'id': 1}}]
            with patch.object(v, 'RUNTIME', runtime), \
                 patch.object(v, 'monitors', side_effect=lambda: shown), \
                 patch.object(v, 'handoff') as handoff:
                self.assertEqual(v.release_departed(now=10), [])
                shown[0]['activeWorkspace']['id'] = 6
                self.assertEqual(v.release_departed(now=12), [])
                handoff.assert_not_called()

    def test_empty_monitors_skips(self):
        with patch.object(v, 'monitors', return_value=[]), patch.object(v, 'handoff') as handoff:
            self.assertEqual(v.release_departed(now=10), [])
            handoff.assert_not_called()

    def test_focused_bench_from_viewer_title(self):
        window = json.dumps({'class': 'Vncviewer', 'title': 'Bancada dos agentes — login-app - TigerVNC'})
        with patch.object(v, 'hypr', return_value=window):
            self.assertEqual(v.focused_bench_name(), 'login-app')

    def test_focused_bench_from_unique_workspace(self):
        replies = {
            ('-j', 'activewindow'): json.dumps({'class': 'Alacritty', 'title': 'term'}),
            ('-j', 'monitors'): json.dumps([{'focused': True, 'activeWorkspace': {'id': 6}}]),
        }
        with patch.object(v, 'hypr', side_effect=lambda *args: replies[args]):
            self.assertEqual(v.focused_bench_name(), 'login-app')

    def test_focused_bench_ambiguous_workspace(self):
        v.entries['other'] = {'workspace': 6, 'viewer_pid': 2, 'launched': True}
        replies = {
            ('-j', 'activewindow'): json.dumps({'class': 'Alacritty', 'title': 'term'}),
            ('-j', 'monitors'): json.dumps([{'focused': True, 'activeWorkspace': {'id': 6}}]),
        }
        with patch.object(v, 'hypr', side_effect=lambda *args: replies[args]):
            with self.assertRaises(RuntimeError):
                v.focused_bench_name()

    def test_resolve_here(self):
        with patch.object(v, 'focused_bench_name', return_value='login-app'):
            self.assertEqual(v.resolve('--here'), 'login-app')
            self.assertEqual(v.resolve('aqui'), 'login-app')
            self.assertEqual(v.resolve('login-app'), 'login-app')

if __name__ == '__main__': unittest.main()
