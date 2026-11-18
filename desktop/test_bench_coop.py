"""Coop (Lucas, 18/11/2026): entrar na bancada libera o humano sem pausar o agente."""
import fcntl
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bench_control
import bench_human as human
import bench_views as views


class Coop(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bench-coop-fixture-')
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.bench = self.runtime / 'fixture'
        self.bench.mkdir()
        self.marker = self.bench / 'human-coop'
        self.hold = self.bench / 'human-control'
        for module in (views, bench_control):
            mock = patch.object(module, 'RUNTIME', self.runtime)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(views, 'entries', {'fixture': {'workspace': 28}})
        mock.start()
        self.addCleanup(mock.stop)
        self.info = {'name': 'fixture', 'protocol_version': 2}

    def test_coop_enables_input_without_pausing_agent(self):
        def enable(path, payload):
            self.assertTrue(self.marker.exists())
            self.assertEqual(payload, {'action': 'human-input', 'enabled': True})
        with patch.object(views, 'live', return_value=self.info), \
             patch.object(views, 'ensure'), patch.object(views, 'rpc', side_effect=enable) as rpc:
            result = views.coop('fixture')
        rpc.assert_called_once()
        self.assertEqual(result['mode'], 'coop')
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.hold.exists())
        self.assertEqual(views.mode_of('fixture'), 'coop')

    def test_coop_does_not_wait_for_agent_operation(self):
        # Uma operação do agente segura o input.lock; o coop não pode esperar nem recusar.
        with (self.bench / 'input.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_SH)
            with patch.object(views, 'live', return_value=self.info), \
                 patch.object(views, 'ensure'), patch.object(views, 'rpc'):
                self.assertEqual(views.coop('fixture')['mode'], 'coop')

    def test_agent_gate_allows_coop_and_refuses_pause(self):
        self.marker.touch()
        with patch('bench_ops.touch_activity'), patch.object(bench_control, 'views'):
            with bench_control.control('fixture'):
                pass
        self.hold.touch()
        with patch('bench_ops.touch_activity'), patch.object(bench_control, 'views'), \
             self.assertRaisesRegex(RuntimeError, 'humano assumiu'):
            with bench_control.control('fixture'):
                pass

    def test_coop_keeps_an_existing_pause(self):
        self.hold.touch()
        with patch.object(views, 'live', return_value=self.info), \
             patch.object(views, 'ensure', return_value={'workspace': 28}), \
             patch.object(views, 'rpc') as rpc:
            result = views.coop('fixture')
        rpc.assert_not_called()
        self.assertEqual(result['mode'], 'humano')
        self.assertFalse(self.marker.exists())

    def test_failed_coop_rolls_back_only_when_input_confirmed_off(self):
        failure = RuntimeError('vncconfig não iniciou: fixture')
        off = {'human_input': {'confirmed': True, 'enabled': False}}
        with patch.object(views, 'live', side_effect=[self.info, off]), \
             patch.object(views, 'ensure'), patch.object(views, 'rpc', side_effect=failure), \
             self.assertRaisesRegex(RuntimeError, 'não liberou mouse/teclado'):
            views.coop('fixture')
        self.assertFalse(self.marker.exists())
        with patch.object(views, 'live', side_effect=[self.info, {}]), \
             patch.object(views, 'ensure'), patch.object(views, 'rpc', side_effect=failure), \
             self.assertRaises(RuntimeError):
            views.coop('fixture')
        self.assertTrue(self.marker.exists())

    def test_old_server_refused(self):
        with patch.object(views, 'live', return_value={'protocol_version': 1}), \
             patch.object(views, 'rpc') as rpc, self.assertRaisesRegex(RuntimeError, 'antes da atualização'):
            views.coop('fixture')
        rpc.assert_not_called()
        self.assertFalse(self.marker.exists())

    def test_leaving_coop_disables_input_and_clears_marker(self):
        self.marker.touch()
        with patch.object(views, 'monitors', return_value=[{'activeWorkspace': {'id': 1}}]), \
             patch.object(views, 'rpc') as rpc, patch.object(views, 'handoff') as handoff:
            self.assertEqual(views.release_departed(now=0), [])
            self.assertEqual(views.release_departed(now=views.LEAVE_SECONDS + 1), ['fixture'])
        rpc.assert_called_once_with(self.bench / 'control.sock', {'action': 'human-input', 'enabled': False})
        handoff.assert_not_called()
        self.assertFalse(self.marker.exists())

    def test_leaving_a_pause_still_resumes_agent(self):
        self.hold.touch()
        with patch.object(views, 'monitors', return_value=[{'activeWorkspace': {'id': 1}}]), \
             patch.object(views, 'handoff') as handoff, patch.object(views, 'leave_coop') as leave:
            views.release_departed(now=0)
            views.release_departed(now=views.LEAVE_SECONDS + 1)
        handoff.assert_called_once_with('fixture', False)
        leave.assert_not_called()

    def test_staying_on_workspace_keeps_coop(self):
        self.marker.touch()
        with patch.object(views, 'monitors', return_value=[{'activeWorkspace': {'id': 28}}]), \
             patch.object(views, 'rpc') as rpc:
            views.release_departed(now=0)
            views.release_departed(now=10)
        rpc.assert_not_called()
        self.assertTrue(self.marker.exists())

    def test_giveback_after_pause_also_clears_coop(self):
        self.marker.touch()
        self.hold.touch()
        with patch.object(views, 'live', return_value=self.info), \
             patch.object(views, 'rpc'), patch.object(views, 'dock'):
            views.handoff('fixture', False)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.hold.exists())

    def test_visit_enters_in_coop(self):
        entries = {'fixture': {'workspace': 28, 'viewer_pid': 1}}
        with patch.object(human, 'load_entries', return_value=entries), \
             patch.object(human, 'focus_workspace'), patch.object(human, 'clients', return_value=[]), \
             patch.object(human, 'views', return_value={'mode': 'coop'}) as call:
            result = human.visit('fixture')
        call.assert_called_once_with('coop', 'fixture')
        self.assertTrue(result['visited'])

    def test_bar_and_reaper_see_coop(self):
        self.marker.touch()
        with patch('bench_ops.RUNTIME', self.runtime):
            import bench_ops
            self.assertFalse(bench_ops.should_reap('fixture', check_input=False))
        self.assertTrue(views.bar_state()['fixture']['coop'])
        self.assertFalse(views.bar_state()['fixture']['human'])


if __name__ == '__main__':
    unittest.main()
