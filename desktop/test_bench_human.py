import unittest
from unittest.mock import patch

import bench_human as h


class VisitAndClose(unittest.TestCase):
    def setUp(self):
        self.entries = {
            'padrao': {'workspace': 6, 'viewer_pid': 1},
            'login-app': {'workspace': 7, 'viewer_pid': 2},
        }
        self.viewer = {
            'class': 'Vncviewer',
            'title': 'Bancada dos agentes — login-app - TigerVNC',
            'workspace': {'id': 7},
            'address': '0x1',
        }

    def test_visit_workspace_picks_unique_bench(self):
        workspace, name = h.resolve_visit('7', self.entries)
        self.assertEqual((workspace, name), (7, 'login-app'))

    def test_visit_here_uses_focused_viewer(self):
        workspace, name = h.resolve_visit('--here', self.entries, window=self.viewer, workspace=2)
        self.assertEqual((workspace, name), (7, 'login-app'))

    def test_visit_here_on_human_workspace_is_noop(self):
        workspace, name = h.resolve_visit('--here', self.entries, window={'class': 'foot'}, workspace=2)
        self.assertEqual((workspace, name), (2, None))

    def test_visit_named_bench(self):
        workspace, name = h.resolve_visit('padrao', self.entries)
        self.assertEqual((workspace, name), (6, 'padrao'))

    def test_close_focused_viewer_stops_bench(self):
        self.assertEqual(h.close_decision(self.viewer, 7, self.entries, [self.viewer]), ('stop', 'login-app'))

    def test_close_human_window_on_bench_workspace(self):
        foot = {'class': 'foot', 'title': 'term', 'workspace': {'id': 6}}
        viewer = {
            'class': 'Vncviewer',
            'title': 'Bancada dos agentes — padrao - TigerVNC',
            'workspace': {'id': 6},
        }
        self.assertEqual(h.close_decision(foot, 6, self.entries, [foot, viewer]), ('close', None))

    def test_close_empty_bench_workspace_stops_unique_bench(self):
        viewer = {
            'class': 'Vncviewer',
            'title': 'Bancada dos agentes — padrao - TigerVNC',
            'workspace': {'id': 6},
        }
        self.assertEqual(h.close_decision({'class': ''}, 6, self.entries, [viewer]), ('stop', 'padrao'))

    def test_close_human_workspace_closes_window(self):
        chromium = {'class': 'chromium', 'title': 'YouTube', 'workspace': {'id': 2}}
        self.assertEqual(h.close_decision(chromium, 2, self.entries, [chromium]), ('close', None))

    def test_viewer_name_ignores_other_windows(self):
        self.assertIsNone(h.viewer_name({'class': 'chromium', 'title': 'Bancada dos agentes — padrao - TigerVNC'}))
        self.assertEqual(h.viewer_name(self.viewer), 'login-app')


if __name__ == '__main__':
    unittest.main()
