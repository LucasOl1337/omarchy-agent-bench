"""Etapa 3 da v2: aba pendente segura, abas órfãs fecham, aviso de envio repetido."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import bench_ops as ops
import bench_web as web
from test_bench_usage_fixes import ClickCDP


class TempBench(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state_dir = self.root / 'state' / 'demo'
        (self.state_dir / 'web-missions').mkdir(parents=True)
        self.patches = [patch.object(web, 'paths', return_value=(self.root / 'run', self.state_dir)),
                        patch.object(ops, 'STATE', self.root / 'state'),
                        patch.object(web, 'audit_event')]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def mission(self, name, tabs, browser='ws://b'):
        (self.state_dir / 'web-missions' / f'{name}.json').write_text(json.dumps({'browser': browser, 'tabs': tabs}))

    def tabs_of(self, name):
        return json.loads((self.state_dir / 'web-missions' / f'{name}.json').read_text())['tabs']


class PendingTests(TempBench):
    def test_only_recent_timestamped_checkpoints_hold(self):
        now = time.time()
        self.mission('a', {'t1': {'checkpoint': {'reason': 'form pela metade', 'next_step': 'enviar', 'at': now - 60}},
                           't2': {'checkpoint': {'reason': 'velho', 'next_step': 'x', 'at': now - 3 * 86400}},
                           't3': {'checkpoint': {'reason': 'sem data', 'next_step': 'x'}},
                           't4': {}})
        found = ops.pending_checkpoints('demo', now)
        self.assertEqual([(f['mission'], f['tab'], f['reason']) for f in found], [('a', 't1', 'form pela metade')])

    def test_pending_tab_keeps_bench_from_reaping(self):
        now = time.time()
        with patch.object(ops, 'last_activity', return_value=now - 99999), \
                patch.object(ops, 'cdp_client_connected', return_value=False), \
                patch.object(ops, 'heavy_work_active', return_value=False), \
                patch.object(ops, 'native_job_present', return_value=False), \
                patch.object(ops, 'bench_cpu_busy', return_value=False):
            self.assertTrue(ops.should_reap('demo', now, check_input=False))
            self.mission('a', {'t1': {'checkpoint': {'reason': 'r', 'next_step': 'n', 'at': now - 60}}})
            self.assertFalse(ops.should_reap('demo', now, check_input=False))


class OrphanTests(TempBench):
    def test_driver_identity(self):
        me = web.proc_start(os.getpid())
        self.assertFalse(web.driver_gone({'pid': os.getpid(), 'start': me}))
        self.assertTrue(web.driver_gone({'pid': os.getpid(), 'start': me + 1}))
        self.assertTrue(web.driver_gone({'pid': 2 ** 22 + 7, 'start': 5}))
        for unknown in (None, {}, {'pid': 5}):
            self.assertFalse(web.driver_gone(unknown))

    def test_sweep_closes_only_dead_drivers_outside_hold(self):
        now = time.time()
        alive = {'pid': os.getpid(), 'start': web.proc_start(os.getpid())}
        dead = {'pid': os.getpid(), 'start': -1}
        self.mission('outra', {
            'dead': {'driver': dead},
            'kept': {'driver': dead, 'checkpoint': {'reason': 'r', 'next_step': 'n', 'at': now - 60}},
            'expired': {'driver': dead, 'checkpoint': {'reason': 'r', 'next_step': 'n', 'at': now - 3 * 86400}},
            'unknown': {},
            'alive': {'driver': alive}})
        self.mission('eu', {'mine-dead': {'driver': dead}})
        self.mission('outro-browser', {'x': {'driver': dead}}, browser='ws://other')
        cdp = ClickCDP()
        cdp.targets = [{'targetId': t, 'type': 'page', 'url': 'about:blank', 'title': ''}
                       for t in ('dead', 'kept', 'expired', 'unknown', 'alive', 'mine-dead', 'x')]
        closes = []
        original = cdp.call

        def call(method, params=None, session=None):
            if method == 'Target.closeTarget':
                closes.append(params['targetId'])
                return {'success': True}
            return original(method, params, session)
        cdp.call = call
        closed = web.sweep_orphans(cdp, 'demo', 'eu', 'ws://b', now)
        self.assertEqual(sorted(closed), ['dead', 'expired'])
        self.assertEqual(sorted(closes), ['dead', 'expired'])
        self.assertEqual(sorted(self.tabs_of('outra')), ['alive', 'kept', 'unknown'])
        self.assertIn('mine-dead', self.tabs_of('eu'))
        self.assertIn('x', self.tabs_of('outro-browser'))


class SendTests(TempBench):
    def test_reminder_lists_previous_send_without_blocking(self):
        now = time.time()
        page = 'https://exemplo.org/form?token=segredo'
        self.assertIsNone(web.note_send('demo', 'qa', {'control': 'Enviar', 'url': page}, 'dispatched', now))
        again = web.note_send('demo', 'qa', {'control': 'Enviar', 'url': 'https://exemplo.org/form?x=2'}, 'dispatched', now + 120)
        self.assertEqual(again['control'], 'Enviar')
        self.assertEqual(again['minutes_ago'], 2.0)
        self.assertEqual(len(web.sends_here('demo', page, now + 180)), 2)
        self.assertEqual(web.sends_here('demo', 'https://exemplo.org/outra', now + 180), [])
        self.assertEqual(web.sends_here('demo', page, now + 3 * 3600), [])
        stored = (self.state_dir / 'envios.json').read_text()
        self.assertNotIn('segredo', stored)
        self.assertNotIn('exemplo.org', stored)

    def test_send_like_click_and_enter_are_marked(self):
        cdp = ClickCDP()
        state = {'tabs': {'owned': {}}}
        refs = {i['name']: i['ref'] for i in web.operate(cdp, state, lambda: None,
                                                         {'action': 'observe', 'tab': 'owned'})['items']}
        args = {'action': 'click', 'tab': 'owned', 'ref': refs['Nome']}
        web.operate(cdp, state, lambda: None, args)
        self.assertNotIn('_send', args)
        state['tabs']['owned'].pop('pending')
        args = {'action': 'click', 'tab': 'owned', 'ref': refs['Enviar']}
        web.operate(cdp, state, lambda: None, args)
        self.assertEqual(args['_send']['control'], 'Enviar')
        args = {'action': 'key', 'tab': 'owned', 'key': 'Enter'}
        web.operate(cdp, state, lambda: None, args)
        self.assertEqual(args['_send']['control'], 'Enter')

    def test_finish_keep_records_checkpoint_time(self):
        cdp = ClickCDP()
        state = {'tabs': {'owned': {}}}
        before = time.time()
        web.operate(cdp, state, lambda: None, {'action': 'finish', 'keep_tabs': ['owned'],
                                               'reason': 'r', 'next_step': 'n'})
        self.assertGreaterEqual(state['tabs']['owned']['checkpoint']['at'], before)


if __name__ == '__main__':
    unittest.main()
