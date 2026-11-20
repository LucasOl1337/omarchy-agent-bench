"""Etapa 2 da v2: erros de uso que a ferramenta resolve sozinha."""
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import bench_web as web
from test_bench_web import FakeCDP, ax


class ClickCDP(FakeCDP):
    def __init__(self):
        super().__init__()
        self.nodes = [ax(1, 'RootWebArea', 'Formulário', [2, 3, 4]),
                      ax(2, 'textbox', 'Nome'), ax(3, 'textbox', 'Email'), ax(4, 'button', 'Enviar')]
        self.disconnected = set()
        self.fail_click = False

    def call(self, method, params=None, session=None):
        if method == 'DOM.resolveNode':
            self.calls.append((method, params))
            return {'object': {'objectId': f"obj{params['backendNodeId']}"}}
        if method == 'Runtime.callFunctionOn':
            self.calls.append((method, params))
            backend = int(params['objectId'][3:])
            if 'return this.isConnected}' in params['functionDeclaration']:
                return {'result': {'value': backend not in self.disconnected}}
            return {'result': {'value': True}}
        if method == 'DOM.getContentQuads':
            self.calls.append((method, params))
            return {'quads': [[0, 0, 10, 0, 10, 10, 0, 10]]}
        if method == 'Input.dispatchMouseEvent' and params['type'] == 'mousePressed' and self.fail_click:
            self.calls.append((method, params))
            raise web.WebError('cdp_timeout: reconcile UI before retrying a mutation')
        return super().call(method, params, session)

    def presses(self):
        return [p for m, p in self.calls if m == 'Input.dispatchMouseEvent' and p['type'] == 'mousePressed']


class RefRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.cdp = ClickCDP()
        self.state = {'tabs': {'owned': {}}}

    def op(self, action, **args):
        result = web.operate(self.cdp, self.state, lambda: None, {'action': action, 'tab': 'owned', **args})
        self.state['tabs']['owned'].pop('pending', None)  # run() does this on a normal return
        return result

    def refs(self):
        items = self.op('observe')['items']
        return {i['name']: i['ref'] for i in items}

    def test_fill_fill_click_from_one_observation(self):
        refs = self.refs()
        self.op('fill', ref=refs['Nome'], text='Ana')
        args = {'action': 'fill', 'tab': 'owned', 'ref': refs['Email'], 'text': 'a@b.c'}
        web.operate(self.cdp, self.state, lambda: None, args)
        self.assertEqual(args['_ref_recovered'], 'same_node')
        self.state['tabs']['owned'].pop('pending')
        self.op('click', ref=refs['Enviar'])
        self.assertEqual(len(self.cdp.presses()), 1)

    def test_same_control_twice_needs_new_observe(self):
        refs = self.refs()
        self.op('click', ref=refs['Enviar'])
        with self.assertRaisesRegex(web.WebError, 'stale_ref'):
            self.op('click', ref=refs['Enviar'])
        self.op('click', ref=refs['Nome'])  # another control is fine
        with self.assertRaisesRegex(web.WebError, 'stale_ref'):
            self.op('click', ref=refs['Enviar'])  # A, B, A is still a repeat
        self.assertEqual(len(self.cdp.presses()), 2)
        fresh = self.refs()
        self.op('click', ref=fresh['Enviar'])  # decided again after looking
        self.assertEqual(len(self.cdp.presses()), 3)

    def test_uncertain_mutation_blocks_recovery(self):
        refs = self.refs()
        self.cdp.fail_click = True
        with self.assertRaisesRegex(web.WebError, 'cdp_timeout'):
            web.operate(self.cdp, self.state, lambda: None, {'action': 'click', 'tab': 'owned', 'ref': refs['Enviar']})
        self.cdp.fail_click = False
        with self.assertRaisesRegex(web.WebError, 'stale_ref'):
            web.operate(self.cdp, self.state, lambda: None, {'action': 'click', 'tab': 'owned', 'ref': refs['Nome']})

    def test_navigation_blocks_recovery(self):
        refs = self.refs()
        self.op('click', ref=refs['Nome'])
        self.cdp.tree['frame']['loaderId'] = 'other-document'
        with self.assertRaisesRegex(web.WebError, 'stale_ref'):
            self.op('click', ref=refs['Enviar'])

    def test_rerendered_control_rematched_by_unique_role_and_name(self):
        refs = self.refs()
        self.op('click', ref=refs['Nome'])
        self.cdp.disconnected.add(4)
        self.cdp.nodes = [ax(1, 'RootWebArea', 'Formulário', [2, 3, 40]),
                          ax(2, 'textbox', 'Nome'), ax(3, 'textbox', 'Email'), ax(40, 'button', 'Enviar')]
        args = {'action': 'click', 'tab': 'owned', 'ref': refs['Enviar']}
        web.operate(self.cdp, self.state, lambda: None, args)
        self.assertEqual(args['_ref_recovered'], 'rematched')
        self.assertIn({'backendNodeId': 40}, [p for m, p in self.cdp.calls if m == 'DOM.resolveNode'])

    def test_ambiguous_rematch_refused(self):
        refs = self.refs()
        self.op('click', ref=refs['Nome'])
        self.cdp.disconnected.add(4)
        self.cdp.nodes = [ax(1, 'RootWebArea', 'F', [40, 41]), ax(40, 'button', 'Enviar'), ax(41, 'button', 'Enviar')]
        with self.assertRaisesRegex(web.WebError, 'stale_ref'):
            self.op('click', ref=refs['Enviar'])

    def test_forgotten_tab_uses_the_only_own_tab(self):
        refs = self.refs()
        args = {'action': 'click', 'ref': refs['Enviar']}
        web.operate(self.cdp, self.state, lambda: None, args)
        self.assertEqual(args['tab'], 'owned')

    def test_foreign_tab_error_lists_own_tabs(self):
        with self.assertRaises(web.WebError) as caught:
            web.operate(self.cdp, self.state, lambda: None, {'action': 'click', 'tab': 'foreign', 'ref': 'x:1'})
        self.assertEqual(caught.exception.details['own_tabs'], ['owned'])


class NameTests(unittest.TestCase):
    def test_typed_names_are_normalized(self):
        for raw, expected in [('Cofre', 'cofre'), ('Minha Missão', 'minha-missao'), ('sfr_qa', 'sfr-qa'),
                              ('  QA -- Teste  ', 'qa-teste'), ('x' * 60, 'x' * 40)]:
            self.assertEqual(web.normalize_name(raw, 'mission'), expected)
        with self.assertRaisesRegex(web.WebError, 'name_invalid'):
            web.normalize_name('!!!', 'mission')


class ScreenshotPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def target(self, output=None):
        args = {'bench': 'demo', 'mission': 'qa'}
        if output is not None:
            args['output'] = output
        with patch.object(web, 'paths', return_value=(self.root / 'run', self.root / 'state')):
            return web.screenshot_target(args)

    def test_missing_relative_folder_and_suffix(self):
        self.assertEqual(self.target().parent, self.root / 'state' / 'capturas')
        with patch('os.getcwd', return_value=str(self.root)):
            self.assertEqual(self.target('novo/tela.png'), self.root / 'novo' / 'tela.png')
        self.assertTrue((self.root / 'novo').is_dir())
        folder = self.target(str(self.root / 'pasta') + '/')
        self.assertEqual(folder.parent, self.root / 'pasta')
        self.assertEqual(self.target(str(self.root / 'semext')).name, 'semext.png')

    def test_taken_name_gets_suffix_without_overwrite(self):
        path = self.root / 'a.png'
        path.write_bytes(b'old')
        written = web.write_new(path, b'new')
        self.assertEqual(written.name, 'a-2.png')
        self.assertEqual(path.read_bytes(), b'old')

    def test_old_captures_pruned(self):
        directory = self.root / 'state' / 'capturas'
        directory.mkdir(parents=True)
        old = directory / 'velha.png'
        old.write_bytes(b'x')
        import os
        os.utime(old, (0, 0))
        self.target()
        self.assertFalse(old.exists())


class RunFixTests(unittest.TestCase):
    def ok_snap(self, bench):
        return {'status': 'conectado', 'lives_in': bench, 'user_data_dir': str(web.paths(bench)[1] / 'chromium'),
                'port': 9222, 'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/browser/id'}

    def run_with(self, snaps, request_error=None, args=None):
        snaps = list(snaps)
        with patch.object(web, 'request', side_effect=request_error), \
                patch.object(web, 'control', return_value=nullcontext()), \
                patch.object(web, 'views', return_value={'workspace': web.AGENT_WORKSPACES[0]}), \
                patch.object(web, 'bench_browser_snapshot', side_effect=lambda *a, **k: snaps.pop(0)), \
                patch.object(web, 'revive') as revive, \
                patch.object(web, 'session', return_value={'ok': True}) as session:
            result = web.run(args or {'bench': 'demo', 'mission': 'qa', 'action': 'tabs'})
        return result, revive, session

    def test_closed_browser_is_started_once(self):
        result, revive, _ = self.run_with([{'status': 'fechado'}, self.ok_snap('demo')])
        revive.assert_called_once_with('demo')
        self.assertEqual(result['started'], 'browser')

    def test_stopped_bench_is_started(self):
        result, revive, _ = self.run_with([self.ok_snap('demo')], request_error=FileNotFoundError())
        revive.assert_called_once_with('demo')
        self.assertEqual(result['started'], 'bench')

    def test_running_browser_untouched_and_names_reported(self):
        result, revive, session = self.run_with([self.ok_snap('demo')],
                                                args={'bench': 'Demo', 'mission': 'Minha Missão', 'action': 'tabs'})
        revive.assert_not_called()
        self.assertEqual(result['normalized'], {'bench': 'demo', 'mission': 'minha-missao'})
        self.assertEqual(session.call_args[0][:2], ('demo', 'minha-missao'))

    def test_unprepared_bench_is_never_created(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(web, 'paths', return_value=(Path(temp) / 'r', Path(temp) / 's')), \
                patch.object(web, 'STATE', Path(temp)), patch.object(web, 'ensure') as ensure:
            with self.assertRaisesRegex(web.WebError, 'bench_unprepared'):
                web.revive('nova')
            ensure.assert_not_called()


class RepeatedFailureTests(unittest.TestCase):
    def test_third_identical_failure_gets_diagnostic(self):
        cdp = FakeCDP()
        state = {'tabs': {'owned': {}}}
        args = {'action': 'click', 'tab': 'owned', 'bench': 'demo', 'mission': 'qa'}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(web, 'paths', return_value=(Path(temp) / 'r', Path(temp) / 's')):
            cdp.call = lambda m, p=None, s=None: ({'targetInfos': cdp.targets} if m == 'Target.getTargets'
                                                  else {'sessionId': 's'} if m == 'Target.attachToTarget'
                                                  else {'data': 'iVBORw0KGgo='})
            errors = []
            for _ in range(3):
                exc = web.WebError('target_obscured: inspect overlay before clicking')
                web.note_failure(cdp, state, args, exc)
                errors.append(exc)
            self.assertEqual(errors[1].details, {})
            diagnostic = errors[2].details['diagnostic']
            self.assertEqual(diagnostic['same_failure'], 3)
            self.assertTrue(Path(diagnostic['screenshot']).exists())
            self.assertIn('"same_failure": 3', str(errors[2]))
        other = web.WebError('stale_ref: x')
        web.note_failure(cdp, state, args, other)
        self.assertEqual(state['failures']['n'], 1)


if __name__ == '__main__':
    unittest.main()
