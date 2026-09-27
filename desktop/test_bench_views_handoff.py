import fcntl
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bench_views as views


class HandoffFailure(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bench-handoff-fixture-')
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.bench = self.runtime / 'fixture'
        self.bench.mkdir()
        self.hold = self.bench / 'human-control'
        for target, value in (('RUNTIME', self.runtime),
                              ('entries', {'fixture': {'workspace': 6}})):
            mock = patch.object(views, target, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.info = {'name': 'fixture', 'protocol_version': 2,
                     'tasks': {'current': 490, 'max': 512}}
        self.failure = RuntimeError('vncconfig não iniciou: fixture')

    def failed_takeover(self, status):
        with patch.object(views, 'live', side_effect=[self.info, status]), \
             patch.object(views, 'ensure') as ensure, \
             patch.object(views, 'rpc', side_effect=self.failure) as rpc, \
             patch.object(views, 'dock') as dock, \
             self.assertRaises(RuntimeError) as caught:
            views.handoff('fixture', True)
        ensure.assert_called_once_with('fixture')
        rpc.assert_called_once_with(self.bench / 'control.sock',
                                    {'action': 'human-input', 'enabled': True})
        dock.assert_not_called()
        return caught.exception

    def test_confirmed_disabled_rolls_back_only_the_new_pause(self):
        error = self.failed_takeover({'human_input': {'confirmed': True, 'enabled': False}})
        self.assertFalse(self.hold.exists())
        self.assertIs(error.__cause__, self.failure)
        self.assertIn('490/512', str(error))
        self.assertIn('não liberou mouse/teclado', str(error))

    def test_prior_human_pause_is_preserved_when_takeover_fails(self):
        self.hold.touch()
        self.failed_takeover({'human_input': {'confirmed': True, 'enabled': False}})
        self.assertTrue(self.hold.exists())

    def test_enabled_or_uncertain_state_preserves_pause(self):
        states = [None, {}, {'confirmed': True, 'enabled': True},
                  {'confirmed': False, 'enabled': False}, {'confirmed': True},
                  {'confirmed': 1, 'enabled': False}, {'confirmed': True, 'enabled': 0},
                  {'confirmed': 'true', 'enabled': False}, {'confirmed': True, 'enabled': None}]
        for state in states:
            with self.subTest(state=state):
                self.hold.unlink(missing_ok=True)
                self.failed_takeover({'human_input': state})
                self.assertTrue(self.hold.exists())

    def test_unreachable_status_preserves_pause_and_original_failure(self):
        error = self.failed_takeover(ConnectionError('status unavailable'))
        self.assertTrue(self.hold.exists())
        self.assertIs(error.__cause__, self.failure)

    def test_incomplete_task_status_cannot_hide_handoff_failure(self):
        self.info['tasks'] = {'max': 512}
        error = self.failed_takeover({'human_input': {'confirmed': True, 'enabled': False}})
        self.assertIs(error.__cause__, self.failure)
        self.assertIn('agent-bench@fixture.service', str(error))

    def test_success_creates_pause_before_enabling_human_input(self):
        def enable(path, payload):
            self.assertTrue(self.hold.exists())
            self.assertEqual(payload, {'action': 'human-input', 'enabled': True})
        with patch.object(views, 'live', return_value=self.info), \
             patch.object(views, 'ensure'), patch.object(views, 'rpc', side_effect=enable):
            result = views.handoff('fixture', True)
        self.assertEqual(result['mode'], 'humano')
        self.assertTrue(self.hold.exists())

    def test_failed_giveback_keeps_pause_and_does_not_dock(self):
        self.hold.touch()
        with patch.object(views, 'live', return_value=self.info), \
             patch.object(views, 'rpc', side_effect=self.failure), \
             patch.object(views, 'dock') as dock, self.assertRaises(RuntimeError):
            views.handoff('fixture', False)
        self.assertTrue(self.hold.exists())
        dock.assert_not_called()

    def test_successful_giveback_disables_input_before_removing_pause(self):
        self.hold.touch()
        def disable(path, payload):
            self.assertTrue(self.hold.exists())
            self.assertEqual(payload, {'action': 'human-input', 'enabled': False})
        def dock(name):
            self.assertFalse(self.hold.exists())
        with patch.object(views, 'live', return_value=self.info), \
             patch.object(views, 'rpc', side_effect=disable), \
             patch.object(views, 'dock', side_effect=dock):
            result = views.handoff('fixture', False)
        self.assertEqual(result['mode'], 'agente')

    def test_busy_input_lock_refuses_without_changing_pause_or_input(self):
        with (self.bench / 'input.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_SH)
            with patch.object(views, 'live', return_value=self.info), \
                 patch.object(views, 'ensure') as ensure, patch.object(views, 'rpc') as rpc, \
                 self.assertRaisesRegex(RuntimeError, 'operação em andamento'):
                views.handoff('fixture', True)
        ensure.assert_not_called()
        rpc.assert_not_called()
        self.assertFalse(self.hold.exists())

    def test_old_server_is_refused_before_acquiring_or_creating_input_files(self):
        with patch.object(views, 'live', return_value={'protocol_version': 1}), \
             patch.object(views, 'ensure') as ensure, patch.object(views, 'rpc') as rpc, \
             self.assertRaisesRegex(RuntimeError, 'antes da atualização'):
            views.handoff('fixture', True)
        ensure.assert_not_called()
        rpc.assert_not_called()
        self.assertEqual(list(self.bench.iterdir()), [])


class HandoffDiagnostic(unittest.TestCase):
    def test_tasks_warning_requires_known_positive_limit_and_ninety_percent_use(self):
        self.assertIn('90/100', views.tasks_hint({'tasks': {'current': 90, 'max': 100}}))
        self.assertIn('110/100', views.tasks_hint({'tasks': {'current': 110, 'max': 100}}))
        self.assertNotIn('cgroup', views.tasks_hint({'tasks': {'current': 89, 'max': 100}}))

    def test_missing_unlimited_or_invalid_tasks_use_log_hint(self):
        for tasks in (None, [], {}, {'max': 512}, {'current': 50, 'max': None},
                      {'current': 50, 'max': 0}, {'current': '490', 'max': 512},
                      {'current': True, 'max': 1}, {'current': -1, 'max': 100}):
            with self.subTest(tasks=tasks):
                self.assertIn('agent-bench@fixture.service',
                              views.tasks_hint({'name': 'fixture', 'tasks': tasks}))

    def test_missing_info_uses_generic_log_hint(self):
        for info in (None, [], {}, {'name': None}, {'name': 3}):
            with self.subTest(info=info):
                self.assertIn('agent-bench@NOME.service', views.tasks_hint(info))

    def test_config_keeps_xdg_and_home_fallbacks_without_running_hyprland(self):
        original = views.CONFIG
        try:
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/fixture/custom-config'}), \
                 patch.object(views.subprocess, 'run') as run:
                importlib.reload(views)
                self.assertEqual(views.CONFIG, Path('/fixture/custom-config/hypr/agent-bench.lua'))
                run.assert_not_called()
            environment = {key: value for key, value in os.environ.items() if key != 'XDG_CONFIG_HOME'}
            with patch.dict(os.environ, environment, clear=True), \
                 patch.object(Path, 'home', return_value=Path('/fixture/home')), \
                 patch.object(views.subprocess, 'run') as run:
                importlib.reload(views)
                self.assertEqual(views.CONFIG, Path('/fixture/home/.config/hypr/agent-bench.lua'))
                run.assert_not_called()
        finally:
            views.CONFIG = original


if __name__ == '__main__':
    unittest.main()
