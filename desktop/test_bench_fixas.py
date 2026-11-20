"""Etapa 4 da v2: 5 bancadas fixas, livre, fecha só o Chromium, cofre sempre ligado."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import bench_fixas
import bench_ops as ops

CONFIG = 'cofre\ndailywork-campanhas\nf3 livre\nf4 livre  # comentário\nf5 livre\nlivre livre\nNOME-RUIM\n'


class ParseTests(unittest.TestCase):
    def test_parse(self):
        found = bench_fixas.parse(CONFIG)
        self.assertEqual([(i['name'], i['slot'], i['pool']) for i in found],
                         [('cofre', 0, False), ('dailywork-campanhas', 1, False),
                          ('f3', 2, True), ('f4', 3, True), ('f5', 4, True)])
        self.assertEqual(bench_fixas.parse(''), [])


class FixedBench(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'cfg').write_text(CONFIG)
        self.me = {'pid': os.getpid(), 'start': ops.proc_start(os.getpid()), 'harness': 'claude'}
        self.patches = [patch.object(bench_fixas, 'CONFIG', root / 'cfg'),
                        patch.object(ops, 'STATE', root / 'state'),
                        patch.object(ops, 'RUNTIME', root / 'run'),
                        patch.object(ops, 'paths', lambda n: (root / 'run' / n, root / 'state' / n)),
                        patch.object(ops, 'current_driver', lambda: self.me)]
        for item in self.patches:
            item.start()
        self.root = root

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def claim(self, name, driver, at=None):
        ops._write_claim(name, driver, time.time() if at is None else at)


class PickTests(FixedBench):
    def test_first_free_then_same_for_same_agent(self):
        self.assertEqual(ops.pick_free(), {'name': 'f3', 'picked': 'livre'})
        self.assertEqual(ops.pick_free(), {'name': 'f3', 'picked': 'sua'})
        self.assertEqual(ops.resolve_name('livre'), 'f3')
        self.assertEqual(ops.resolve_name('cofre'), 'cofre')

    def test_skips_busy_and_reuses_dead_or_idle(self):
        other = {'pid': os.getpid(), 'start': self.me['start'] + 1}  # gone: start time differs
        alive_other = {'pid': 1, 'start': ops.proc_start(1)}
        self.claim('f3', alive_other)
        with patch.object(ops, 'driver_gone', lambda d: d == other):
            self.claim('f4', other)
            self.assertEqual(ops.pick_free()['name'], 'f4')

    def test_human_inside_or_pending_tab_is_busy(self):
        (self.root / 'run' / 'f3').mkdir(parents=True)
        (self.root / 'run' / 'f3' / 'human-control').touch()
        missions = self.root / 'state' / 'f4' / 'web-missions'
        missions.mkdir(parents=True)
        (missions / 'm.json').write_text(json.dumps({'tabs': {'t': {'checkpoint': {
            'reason': 'r', 'next_step': 'n', 'at': time.time()}}}}))
        self.assertEqual(ops.pick_free()['name'], 'f5')

    def test_all_busy_gives_extra(self):
        alive_other = {'pid': 1, 'start': ops.proc_start(1)}
        for name in ('f3', 'f4', 'f5'):
            self.claim(name, alive_other)
        with patch.object(ops, 'last_activity', return_value=time.time()):
            picked = ops.pick_free()
        self.assertEqual(picked, {'name': 'extra-1', 'picked': 'extra', 'busy': ['f3', 'f4', 'f5']})

    def test_fixed_profiles_are_persistent(self):
        for name in ('cofre', 'f3', 'dailywork-campanhas'):
            self.assertTrue(ops.persistent_profile(name))
        self.assertFalse(ops.persistent_profile('extra-1'))
        profile = ops.provide_bench_profile(self.root / 'state' / 'f4')
        self.assertFalse((profile / ops.EPHEMERAL_MARK).exists())


class ReleaseTests(FixedBench):
    def test_release_by_kind(self):
        self.claim('f3', self.me)
        with patch.object(ops, 'close_browser', return_value={'closed': True}) as close, \
                patch.object(ops, 'stop') as stop:
            self.assertIn('kept', ops.release_bench('cofre'))
            self.assertEqual(ops.release_bench('f3'), {'name': 'f3', 'browser_closed': True, 'desktop': 'mantido'})
            self.assertFalse((self.root / 'state' / 'f3' / ops.CLAIM).exists())
            ops.release_bench('qualquer')
        close.assert_called_once_with('f3')
        stop.assert_called_once_with('qualquer')

    def test_cofre_never_reaped(self):
        self.assertFalse(ops.should_reap('cofre', time.time() + 10 ** 6, check_input=False))

    def test_reaper_closes_only_chromium_on_fixed(self):
        for name in ('f3', 'extra-1', 'f4'):
            (self.root / 'run' / name).mkdir(parents=True)
            (self.root / 'run' / name / 'control.sock').touch()
        self.claim('f3', self.me)
        with patch.object(ops, 'sample_cpu'), patch.object(ops, 'should_reap', return_value=True), \
                patch.object(ops, '_main_chromium_pids', lambda p: [] if p.parent.name == 'f4' else [1]), \
                patch.object(ops, 'close_browser') as close, patch.object(ops, 'stop') as stop:
            stopped = ops.reap_idle()
        self.assertEqual(sorted(stopped), ['extra-1', 'f3:chromium'])
        close.assert_called_once_with('f3')
        stop.assert_called_once_with('extra-1', preserve_profile=True)
        self.assertFalse((self.root / 'state' / 'f3' / ops.CLAIM).exists())


class SlotTests(unittest.TestCase):
    def test_fixed_bench_gets_its_key_and_overflow_avoids_cofre(self):
        import bench_views as views
        with tempfile.TemporaryDirectory() as temp:
            cfg = Path(temp) / 'cfg'
            cfg.write_text(CONFIG)
            with patch.object(bench_fixas, 'CONFIG', cfg), \
                    patch.object(views, 'AGENT_WORKSPACES', (26, 27, 28, 29, 30)), \
                    patch.object(views, 'clients', return_value=[]), \
                    patch.object(views, 'hypr', return_value='[]'), \
                    patch.dict(views.entries, {n: {'workspace': w} for n, w in
                                               (('cofre', 26), ('dailywork-campanhas', 27), ('f3', 28),
                                                ('f4', 29), ('f5', 30))}, clear=True):
                self.assertEqual(views.pick_slot('f4'), 29)
                self.assertEqual(views.pick_slot('cofre'), 26)
                self.assertEqual(views.pick_slot('extra-1'), 28)


class VisitTests(unittest.TestCase):
    def test_slot_with_two_benches_prefers_fixed(self):
        import bench_human as human
        with tempfile.TemporaryDirectory() as temp:
            cfg = Path(temp) / 'cfg'
            cfg.write_text(CONFIG)
            entries = {'f3': {'workspace': 28}, 'extra-1': {'workspace': 28}}
            with patch.object(bench_fixas, 'CONFIG', cfg), patch.object(human, 'from_slot', return_value=28):
                self.assertEqual(human.resolve_visit('8', entries), (28, 'f3'))


if __name__ == '__main__':
    unittest.main()
