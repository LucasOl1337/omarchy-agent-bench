import unittest
import copy
import io
import json
import os
from pathlib import Path
import tempfile
from contextlib import nullcontext
from unittest.mock import patch

import bench_web as web


class WebTests(unittest.TestCase):
    def test_location_rejects_human_or_other_bench_before_connect(self):
        profile = str(web.paths('demo')[1] / 'chromium')
        valid = {'status': 'conectado', 'lives_in': 'demo', 'user_data_dir': profile,
                 'port': 9222, 'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/browser/id'}
        web.validate_location('demo', {'workspace': 8}, valid)
        for field, value in [('lives_in', 'desktop'), ('lives_in', 'other'),
                             ('user_data_dir', '/home/lol/.config/chromium'),
                             ('webSocketDebuggerUrl', 'ws://example.org:9222/x')]:
            with self.subTest(field=field, value=value), self.assertRaises(web.WebError):
                web.validate_location('demo', {'workspace': 8}, {**valid, field: value})
        for workspace in (1, 5, 12, None):
            with self.subTest(workspace=workspace), self.assertRaises(web.WebError):
                web.validate_location('demo', {'workspace': workspace}, valid)

    def test_human_control_prevents_endpoint_inspection(self):
        with patch.object(web, 'request'), patch.object(web, 'control', side_effect=RuntimeError('human-control')), \
                patch.object(web, 'bench_browser_snapshot') as snapshot, patch.object(web, 'CDP') as cdp:
            with self.assertRaisesRegex(RuntimeError, 'human-control'):
                web.run({'bench': 'demo', 'mission': 'test', 'action': 'tabs'})
            snapshot.assert_not_called()
            cdp.assert_not_called()

    def test_bad_workspace_prevents_endpoint_inspection(self):
        with patch.object(web, 'request'), patch.object(web, 'control', return_value=nullcontext()), \
                patch.object(web, 'views', return_value={'workspace': 5}), \
                patch.object(web, 'bench_browser_snapshot') as snapshot:
            with self.assertRaisesRegex(web.WebError, 'workspace_invalid'):
                web.run({'bench': 'demo', 'mission': 'test', 'action': 'tabs'})
            snapshot.assert_not_called()

    def test_foreign_tab_rejected_without_attaching(self):
        cdp = FakeCDP()
        with self.assertRaisesRegex(web.WebError, 'tab_not_owned'):
            web.operate(cdp, {'tabs': {}}, lambda: None, {'action': 'click', 'tab': 'foreign'})
        self.assertEqual([c[0] for c in cdp.calls], ['Target.getTargets'])

    def test_old_ref_or_navigated_document_rejected(self):
        tree = {'frame': {'id': 'frame', 'loaderId': 'document', 'url': 'https://example.org'}}
        record = {'frame': web.frame_identity(tree), 'refs': {'fresh:1': {'backend': 1}}}
        self.assertEqual(web.resolve_ref(record, 'fresh:1', tree)['backend'], 1)
        with self.assertRaises(web.WebError):
            web.resolve_ref(record, 'old:1', tree)
        tree['frame']['loaderId'] = 'new-document'
        with self.assertRaises(web.WebError):
            web.resolve_ref(record, 'fresh:1', tree)

    def test_ax_retains_unicode_and_hides_protected_value(self):
        nodes = [{'nodeId': '1', 'role': {'value': 'textbox'},
                  'name': {'value': 'Ação própria'}, 'value': {'value': 'secret'},
                  'properties': [{'name': 'protected', 'value': {'value': True}}],
                  'backendDOMNodeId': 7}]
        items, refs = web.render_ax(nodes, 'snapshot')
        self.assertEqual(items[0]['name'], 'Ação própria')
        self.assertEqual(items[0]['value'], '[protected]')
        self.assertEqual(refs['snapshot:1']['backend'], 7)
        self.assertNotIn('secret', str(items) + str(refs))

    def test_navigation_failure_retains_owned_tab(self):
        cdp = FakeCDP(navigation_error=True)
        state, saved = {'tabs': {}}, []
        with self.assertRaisesRegex(web.WebError, 'navigation_failed'):
            web.operate(cdp, state, lambda: saved.append(set(state['tabs'])),
                        {'action': 'open', 'url': 'https://example.org'})
        self.assertIn('new', state['tabs'])
        self.assertIn({'new'}, saved)

    def test_mutation_invalidates_ref_before_dispatch(self):
        cdp = FakeCDP()
        state = {'tabs': {'owned': {'refs': {'old': {'backend': 7}}}}}
        saved = []
        web.operate(cdp, state, lambda: saved.append(dict(state['tabs']['owned'])),
                    {'action': 'key', 'tab': 'owned', 'key': 'Escape'})
        self.assertEqual(saved, [{}])
        self.assertEqual(state['tabs']['owned'], {})

    def test_key_release_attempted_when_press_fails(self):
        calls = []
        def call(method, args):
            calls.append(args['type'])
            if args['type'] == 'rawKeyDown':
                raise RuntimeError('transport lost')
        with self.assertRaises(RuntimeError):
            web.send_key(call, 'Escape')
        self.assertEqual(calls, ['rawKeyDown', 'keyUp'])

    def test_rejects_script_or_local_file_navigation(self):
        for url in ('javascript:alert(1)', 'file:///etc/passwd', 'chrome://settings', 'https:'):
            with self.subTest(url=url), self.assertRaises(web.WebError):
                web.require_url(url)
        self.assertEqual(web.require_url('about:blank'), 'about:blank')


def ax(node, role, name='', children=(), value=None, backend=True):
    result = {'nodeId': str(node), 'role': {'value': role}, 'name': {'value': name},
              'childIds': [str(n) for n in children]}
    if backend:
        result['backendDOMNodeId'] = node
    if value is not None:
        result['value'] = {'value': value}
    return result


def fixture_nodes():
    return [ax(1, 'RootWebArea', 'Prova', [2, 6]),
            ax(2, 'region', 'Dados do projeto', [3, 4, 5]),
            ax(3, 'textbox', 'Título', value='Ação própria çãé 🚀'),
            ax(4, 'button', 'Salvar localmente'), ax(5, 'StaticText', 'Item inicial'),
            ax(6, 'region', 'Texto extenso', [7]), ax(7, 'textbox', 'Descrição', value='Outra região')]


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.cdp = FakeCDP()
        self.state = {'tabs': {'owned': {}}}

    def operation(self, action='observe', **args):
        return web.operate(self.cdp, self.state, lambda: None,
                           {'action': action, 'tab': 'owned', **args})

    def test_region_ref_selects_actual_descendants_and_is_not_clickable(self):
        first = self.operation()
        region = next(i for i in first['items'] if i['role'] == 'region')
        self.assertFalse(region['actionable'])
        with self.assertRaisesRegex(web.WebError, 'not_actionable'):
            self.operation('click', ref=region['ref'])
        self.assertFalse(any(m == 'DOM.resolveNode' for m, _ in self.cdp.calls))
        scoped = self.operation(region=region['ref'])
        self.assertEqual([i['id'] for i in scoped['items']], ['dom:2', 'dom:3', 'dom:4', 'dom:5'])
        self.assertEqual(scoped['total'], 4)
        self.assertEqual(scoped['snapshot_total'], 7)
        self.assertNotIn('Outra região', json.dumps(scoped, ensure_ascii=False))

    def test_cached_pages_and_filters_keep_refs_without_recapture(self):
        first = self.operation(limit=2)
        call_count = sum(m == 'Accessibility.getFullAXTree' for m, _ in self.cdp.calls)
        second = self.operation('read', snapshot=first['snapshot'], offset=2, limit=2)
        selected = self.operation('read', snapshot=first['snapshot'], query='AÇÃO PRÓPRIA')
        self.assertEqual(second['snapshot'], first['snapshot'])
        self.assertEqual(second['source'], 'stored')
        self.assertEqual(selected['items'][0]['ref'], second['items'][0]['ref'])
        self.assertEqual(call_count, sum(m == 'Accessibility.getFullAXTree' for m, _ in self.cdp.calls))
        self.assertEqual(second['next_offset'], 4)

    def test_text_continuation_reconstructs_unicode_name_and_value_without_loss(self):
        long_text = 'ação 🚀 çãé ' * 410 + 'MARCADOR FINAL'
        self.cdp.nodes[2]['name']['value'] = long_text
        self.cdp.nodes[2]['value']['value'] = long_text[::-1]
        first = self.operation()
        item = next(i for i in first['items'] if i['id'] == 'dom:3')
        for field, expected in [('name', long_text), ('value', long_text[::-1])]:
            self.assertTrue(item[field + '_truncated'])
            self.assertEqual(item[field + '_length'], len(expected))
            collected, offset = item[field], item[field + '_next_offset']
            while offset is not None:
                part = self.operation('read', snapshot=first['snapshot'], ref=item['ref'],
                                      field=field, text_offset=offset, text_limit=137)
                collected += part['text']
                offset = part['next_text_offset']
            self.assertEqual(collected, expected)
        matches = self.operation('read', snapshot=first['snapshot'], query='MARCADOR FINAL')
        self.assertEqual(matches['items'][0]['id'], 'dom:3')

    def test_protected_values_never_enter_snapshot_or_continuations(self):
        self.cdp.nodes[2].update(value={'value': 'highly-secret'},
            properties=[{'name': 'protected', 'value': {'value': True}}])
        first = self.operation()
        item = next(i for i in first['items'] if i['id'] == 'dom:3')
        part = self.operation('read', snapshot=first['snapshot'], ref=item['ref'], field='value')
        self.assertEqual(part['text'], '[protected]')
        self.assertNotIn('highly-secret', json.dumps(self.state))

    def test_unchanged_delta_ignores_fresh_refs(self):
        first = self.operation()
        second = self.operation(since=first['snapshot'])
        self.assertEqual(second['delta']['status'], 'compared')
        self.assertEqual(second['delta']['unchanged'], 7)
        self.assertEqual(second['changes'], [])
        self.assertFalse(second['delta']['order_changed'])
        self.assertNotEqual(first['snapshot'], second['snapshot'])

    def test_scoped_delta_detects_values_text_added_removed_and_keeps_baseline_after_action(self):
        first = self.operation()
        scoped = self.operation(region=first['items'][1]['ref'])
        self.operation('key', key='Escape')
        self.cdp.nodes[2]['value']['value'] = 'Título alterado 🚀'
        self.cdp.nodes[3]['name']['value'] = 'Salvo'
        self.cdp.nodes = [n for n in self.cdp.nodes if n['nodeId'] != '5']
        self.cdp.nodes[1]['childIds'] = ['3', '4', '8']
        self.cdp.nodes.append(ax(8, 'StaticText', 'Novo item'))
        self.cdp.nodes[5]['value']['value'] = 'Mudança de fora não aparece'
        result = self.operation(since=scoped['snapshot'])
        self.assertEqual(result['selection']['region'], 'dom:2')
        self.assertEqual({k: result['delta'][k] for k in ('added', 'removed', 'changed')},
                         {'added': 1, 'removed': 1, 'changed': 2})
        self.assertEqual(result['change_total'], 4)
        self.assertNotIn('Mudança de fora', json.dumps(result, ensure_ascii=False))
        changes = {c.get('item', c.get('before'))['id']: c for c in result['changes']}
        self.assertEqual(changes['dom:3']['fields'], ['value'])
        self.assertEqual(changes['dom:4']['fields'], ['name'])
        self.assertNotIn('ref', changes['dom:5']['before'])

    def test_delta_compares_unabridged_text(self):
        self.cdp.nodes[2]['value']['value'] = 'x' * 4000 + 'a'
        first = self.operation()
        self.cdp.nodes[2]['value']['value'] = 'x' * 4000 + 'b'
        result = self.operation(since=first['snapshot'])
        self.assertEqual(result['delta']['changed'], 1)
        self.assertEqual(result['changes'][0]['fields'], ['value'])
        self.assertTrue(result['changes'][0]['item']['value_truncated'])

    def test_delta_pagination_reads_same_capture(self):
        first = self.operation()
        self.cdp.nodes[2]['value']['value'] = 'Novo valor'
        self.cdp.nodes[3]['name']['value'] = 'Outro nome'
        changed = self.operation(since=first['snapshot'], limit=1)
        second = self.operation('read', snapshot=changed['snapshot'], since=first['snapshot'], offset=1, limit=1)
        self.assertEqual(changed['next_offset'], 1)
        self.assertEqual(second['snapshot'], changed['snapshot'])
        self.assertEqual(second['change_total'], 2)
        self.assertNotEqual(changed['changes'][0]['item']['id'], second['changes'][0]['item']['id'])
        self.assertIsNone(second['next_offset'])

    def test_unknown_or_expired_snapshot_resets_instead_of_empty_delta(self):
        first = self.operation()
        for _ in range(web.SNAPSHOT_HISTORY):
            self.operation()
        result = self.operation(since=first['snapshot'])
        self.assertEqual(result['delta']['reason'], 'snapshot_unavailable')
        self.assertEqual(result['mode'], 'full')
        self.assertEqual(len(result['items']), 7)
        self.assertEqual(len(self.state['tabs']['owned']['snapshots']), web.SNAPSHOT_HISTORY)

    def test_navigation_resets_even_when_backend_ids_and_text_are_reused(self):
        first = self.operation()
        first = self.operation(region=first['items'][1]['ref'])
        self.cdp.tree['frame']['loaderId'] = 'new-document'
        result = self.operation(since=first['snapshot'])
        self.assertEqual(result['delta']['reason'], 'document_changed')
        self.assertEqual(result['mode'], 'full')
        self.assertIsNone(result['selection']['region'])
        self.assertEqual(len(result['items']), 7)

    def test_oldest_comparison_base_survives_so_delta_can_be_paged(self):
        first = self.operation()
        for _ in range(web.SNAPSHOT_HISTORY - 1):
            self.operation()
        self.cdp.nodes[2]['value']['value'] = 'Alterado'
        changed = self.operation(since=first['snapshot'])
        again = self.operation('read', snapshot=changed['snapshot'], since=first['snapshot'])
        self.assertEqual(again['delta']['status'], 'compared')
        self.assertEqual(again['changes'], changed['changes'])

    def test_child_frame_and_query_changes_explicitly_reset(self):
        first = self.operation()
        changed_selection = self.operation(since=first['snapshot'], query='Título')
        self.assertEqual(changed_selection['delta']['reason'], 'selection_changed')
        self.cdp.tree['childFrames'] = [{'frame': {'id': 'child', 'loaderId': 'child-doc', 'url': 'https://example.org'}}]
        changed_coverage = self.operation(since=changed_selection['snapshot'])
        self.assertEqual(changed_coverage['delta']['reason'], 'coverage_changed')
        self.assertEqual(changed_coverage['child_frames'], 1)
        self.assertEqual(changed_coverage['mode'], 'full')

    def test_removed_region_expands_to_full_snapshot_with_explicit_reset(self):
        first = self.operation()
        scoped = self.operation(region=first['items'][1]['ref'])
        self.cdp.nodes = [n for n in self.cdp.nodes if n['nodeId'] not in ('2', '3', '4', '5')]
        self.cdp.nodes[0]['childIds'] = ['6']
        result = self.operation(since=scoped['snapshot'])
        self.assertEqual(result['delta']['reason'], 'region_unavailable')
        self.assertIsNone(result['selection']['region'])
        self.assertEqual(len(result['items']), 3)

    def test_read_rejects_old_snapshot_after_action_observation_or_navigation(self):
        for reason in ('action', 'observation', 'navigation'):
            with self.subTest(reason=reason):
                first = self.operation()
                if reason == 'action':
                    self.operation('key', key='Escape')
                elif reason == 'observation':
                    self.operation()
                else:
                    self.cdp.tree['frame']['loaderId'] += '-new'
                with self.assertRaisesRegex(web.WebError, 'stale_snapshot'):
                    self.operation('read', snapshot=first['snapshot'])

    def test_failed_or_raced_capture_invalidates_refs_without_saving_a_false_snapshot(self):
        first = self.operation()
        self.cdp.nodes = []
        with self.assertRaisesRegex(web.WebError, 'observation_empty'):
            self.operation(since=first['snapshot'])
        self.assertNotIn('refs', self.state['tabs']['owned'])
        self.assertEqual(len(self.state['tabs']['owned']['snapshots']), 1)
        self.cdp.nodes = fixture_nodes()
        self.cdp.navigate_during_ax = True
        with self.assertRaisesRegex(web.WebError, 'observation_changed'):
            self.operation(since=first['snapshot'])
        self.assertEqual(len(self.state['tabs']['owned']['snapshots']), 1)

    def test_ax_only_node_identity_is_conservative_between_captures(self):
        self.cdp.nodes.append(ax(9, 'StaticText', 'Virtual text', backend=False))
        first = self.operation()
        result = self.operation(since=first['snapshot'])
        self.assertEqual(result['delta']['added'], 1)
        self.assertEqual(result['delta']['removed'], 1)

    def test_invalid_ranges_and_foreign_read_do_not_observe(self):
        for kwargs in ({'limit': 0}, {'text_limit': 10001}, {'offset': -1}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(web.WebError, 'range_invalid'):
                self.operation(**kwargs)
        self.assertFalse(any(m == 'Accessibility.getFullAXTree' for m, _ in self.cdp.calls))
        with self.assertRaisesRegex(web.WebError, 'tab_not_owned'):
            web.operate(self.cdp, self.state, lambda: None,
                        {'action': 'read', 'tab': 'foreign', 'snapshot': 'unknown'})


def page(target, opener=None, **extra):
    result = {'targetId': target, 'type': 'page', 'title': target,
              'url': 'https://example.org/popup', 'browserContextId': 'context'}
    if opener is not None:
        result['openerId'] = opener
        result['openerFrameId'] = 'opener-frame'
    return {**result, **extra}


class PopupTests(unittest.TestCase):
    def setUp(self):
        self.cdp = FakeCDP()
        self.cdp.targets = [page('owned'), page('foreign'), page('child', 'owned')]
        self.state = {'browser': 'browser-one', 'tabs': {'owned': {'refs': {'existing': {'backend': 7}}}}}

    def operation(self, action='popups', **args):
        return web.operate(self.cdp, self.state, lambda: None,
                           {'action': action, 'tab': 'owned', **args})

    def test_discovery_only_reports_direct_children_and_does_not_grant_ownership(self):
        self.cdp.targets.extend([page('unrelated', 'foreign'), page('orphan'),
                                 page('grandchild', 'child'), page('worker', 'owned', type='worker')])
        result = self.operation()
        self.assertEqual([p['tab'] for p in result['popups']], ['child'])
        self.assertEqual(set(self.state['tabs']), {'owned'})
        self.assertNotIn('unrelated', json.dumps(result))
        self.assertNotIn('grandchild', json.dumps(result))
        with self.assertRaisesRegex(web.WebError, 'tab_not_owned'):
            self.operation('observe', tab='child')
        self.assertTrue(all(m == 'Target.getTargets' for m, _ in self.cdp.calls))

    def test_explicit_adoption_selects_one_of_identical_urls_without_focus_or_attach(self):
        self.cdp.targets.append(page('another-child', 'owned'))
        result = self.operation()
        popup = result['popups'][1]
        adopted = self.operation('adopt', popup=popup['popup'])
        self.assertEqual(adopted['tab'], 'another-child')
        self.assertEqual(adopted['effect'], 'registered')
        self.assertNotIn('child', self.state['tabs'])
        self.assertIn('existing', self.state['tabs']['owned']['refs'])
        self.assertEqual(self.state['tabs']['another-child']['opened_by'], 'owned')
        tabs = self.operation('tabs')
        self.assertEqual(tabs['tabs'][1]['opened_by'], 'owned')
        self.assertTrue(all(m == 'Target.getTargets' for m, _ in self.cdp.calls))

    def test_guessed_target_id_or_ref_is_not_an_adoption_route(self):
        for guessed in ('child', 'https://example.org/popup', 'unknown:1', None):
            with self.subTest(guessed=guessed), self.assertRaisesRegex(web.WebError, 'popup_not_discovered'):
                self.operation('adopt', popup=guessed)
        self.assertEqual(set(self.state['tabs']), {'owned'})

    def test_foreign_or_closed_opener_cannot_discover_or_adopt(self):
        for action in ('popups', 'adopt'):
            with self.subTest(action=action), self.assertRaisesRegex(web.WebError, 'tab_not_owned'):
                self.operation(action, tab='foreign', popup='fake')
        popup = self.operation()['popups'][0]['popup']
        self.cdp.targets = [p for p in self.cdp.targets if p['targetId'] != 'owned']
        with self.assertRaisesRegex(web.WebError, 'tab_closed'):
            self.operation('adopt', popup=popup)

    def test_closed_child_is_not_registered(self):
        popup = self.operation()['popups'][0]['popup']
        self.cdp.targets = [p for p in self.cdp.targets if p['targetId'] != 'child']
        with self.assertRaisesRegex(web.WebError, 'popup_unavailable'):
            self.operation('adopt', popup=popup)
        self.assertEqual(set(self.state['tabs']), {'owned'})

    def test_changed_lineage_frame_context_or_type_is_rejected_before_registration(self):
        changes = [('openerId', 'foreign', 'popup_lineage_invalid'),
                   ('openerId', None, 'popup_lineage_invalid'),
                   ('openerId', ['owned', 'foreign'], 'popup_lineage_invalid'),
                   ('openerFrameId', 'new-frame', 'popup_lineage_changed'),
                   ('openerFrameId', [], 'popup_lineage_invalid'),
                   ('browserContextId', 'other-context', 'popup_context_invalid'),
                   ('browserContextId', [], 'popup_context_invalid'),
                   ('type', 'worker', 'popup_unavailable'),
                   ('subtype', 'prerender', 'popup_unavailable')]
        for field, value, error in changes:
            with self.subTest(field=field, value=value):
                self.setUp()
                popup = self.operation()['popups'][0]['popup']
                self.cdp.targets[2][field] = value
                with self.assertRaisesRegex(web.WebError, error):
                    self.operation('adopt', popup=popup)
                self.assertEqual(set(self.state['tabs']), {'owned'})
                self.assertTrue(all(m == 'Target.getTargets' for m, _ in self.cdp.calls))

    def test_matching_new_context_on_both_sides_does_not_reuse_old_proof(self):
        popup = self.operation()['popups'][0]['popup']
        self.cdp.targets[0]['browserContextId'] = 'another'
        self.cdp.targets[2]['browserContextId'] = 'another'
        with self.assertRaisesRegex(web.WebError, 'popup_lineage_changed'):
            self.operation('adopt', popup=popup)

    def test_popup_navigation_does_not_destroy_valid_lineage(self):
        popup = self.operation()['popups'][0]['popup']
        self.cdp.targets[2].update(url='https://other.example/auth/callback', title='Navigation finished',
                                   canAccessOpener=False)
        self.cdp.targets[0]['url'] = 'https://example.org/next'
        result = self.operation('adopt', popup=popup)
        self.assertEqual(result['tab'], 'child')
        self.assertEqual(result['opened_by'], 'owned')

    def test_incomplete_context_and_prerender_are_not_offered(self):
        self.cdp.targets.extend([page('prerender', 'owned', subtype='prerender'),
                                 page('different-context', 'owned', browserContextId='another'),
                                 page('missing-context', 'owned', browserContextId=None)])
        result = self.operation()
        self.assertEqual([p['tab'] for p in result['popups']], ['child'])
        self.assertEqual(result['unavailable_children'], 3)

    def test_default_context_with_both_ids_omitted_is_supported(self):
        for target in self.cdp.targets:
            target.pop('browserContextId')
        popup = self.operation()['popups'][0]['popup']
        self.assertEqual(self.operation('adopt', popup=popup)['tab'], 'child')

    def test_ambiguous_duplicate_targets_refused_instead_of_last_write_wins(self):
        self.cdp.targets.append(page('child', 'foreign'))
        with self.assertRaisesRegex(web.WebError, 'targets_ambiguous'):
            self.operation()
        self.assertNotIn('popup_candidates', self.state['tabs']['owned'])

    def test_new_discovery_expires_old_refs_and_adoption_cannot_be_replayed(self):
        old = self.operation()['popups'][0]['popup']
        current = self.operation()['popups'][0]['popup']
        with self.assertRaisesRegex(web.WebError, 'popup_not_discovered'):
            self.operation('adopt', popup=old)
        self.operation('adopt', popup=current)
        with self.assertRaisesRegex(web.WebError, 'popup_not_discovered'):
            self.operation('adopt', popup=current)
        self.assertEqual(self.operation()['popups'], [])

    def test_grandchild_becomes_available_only_after_parent_adoption(self):
        self.cdp.targets.append(page('grandchild', 'child'))
        first = self.operation()
        self.assertEqual([p['tab'] for p in first['popups']], ['child'])
        self.operation('adopt', popup=first['popups'][0]['popup'])
        second = self.operation(tab='child')
        self.assertEqual([p['tab'] for p in second['popups']], ['grandchild'])
        self.operation('adopt', tab='child', popup=second['popups'][0]['popup'])
        self.assertEqual(self.state['tabs']['grandchild']['opened_by'], 'child')

    def test_discovery_persists_across_clients_and_browser_restart_refuses_old_proof(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(web, 'paths', return_value=(None, Path(temp))):
            with web.mission_store('demo', 'mission', 'browser-one') as (state, save):
                state['tabs']['owned'] = {}
                first = web.operate(self.cdp, state, save, {'action': 'popups', 'tab': 'owned'})
            with web.mission_store('demo', 'mission', 'browser-one') as (state, save):
                adopted = web.operate(self.cdp, state, save,
                    {'action': 'adopt', 'tab': 'owned', 'popup': first['popups'][0]['popup']})
                self.assertEqual(adopted['tab'], 'child')
            with web.mission_store('demo', 'mission', 'browser-two') as (state, save):
                self.assertEqual(state['tabs'], {})
                with self.assertRaisesRegex(web.WebError, 'tab_not_owned'):
                    web.operate(self.cdp, state, save,
                        {'action': 'adopt', 'tab': 'owned', 'popup': first['popups'][0]['popup']})

    def test_other_mission_cannot_use_discovered_popup_proof(self):
        popup = self.operation()['popups'][0]['popup']
        other_mission = {'tabs': {'foreign': {}}}
        with self.assertRaisesRegex(web.WebError, 'tab_not_owned'):
            web.operate(self.cdp, other_mission, lambda: None,
                        {'action': 'adopt', 'tab': 'owned', 'popup': popup})
        with self.assertRaisesRegex(web.WebError, 'popup_not_discovered'):
            web.operate(self.cdp, other_mission, lambda: None,
                        {'action': 'adopt', 'tab': 'foreign', 'popup': popup})

    def test_human_control_and_workspace_checks_also_precede_popup_inspection(self):
        for action in ('popups', 'adopt'):
            with self.subTest(action=action), patch.object(web, 'request'), \
                    patch.object(web, 'control', side_effect=RuntimeError('human-control')), \
                    patch.object(web, 'bench_browser_snapshot') as snapshot, patch.object(web, 'CDP') as cdp:
                with self.assertRaisesRegex(RuntimeError, 'human-control'):
                    web.run({'bench': 'demo', 'mission': 'test', 'action': action, 'tab': 'owned'})
                snapshot.assert_not_called()
                cdp.assert_not_called()
            with self.subTest(action=action), patch.object(web, 'request'), \
                    patch.object(web, 'control', return_value=nullcontext()), \
                    patch.object(web, 'views', return_value={'workspace': 5}), \
                    patch.object(web, 'bench_browser_snapshot') as snapshot:
                with self.assertRaisesRegex(web.WebError, 'workspace_invalid'):
                    web.run({'bench': 'demo', 'mission': 'test', 'action': action, 'tab': 'owned'})
                snapshot.assert_not_called()
            with self.subTest(action=action), patch.object(web, 'request'), \
                    patch.object(web, 'control', return_value=nullcontext()), \
                    patch.object(web, 'views', return_value={'workspace': 8}), \
                    patch.object(web, 'bench_browser_snapshot', return_value={'status': 'conectado', 'lives_in': 'other'}), \
                    patch.object(web, 'CDP') as cdp:
                with self.assertRaisesRegex(web.WebError, 'browser_location_invalid'):
                    web.run({'bench': 'demo', 'mission': 'test', 'action': action, 'tab': 'owned'})
                cdp.assert_not_called()


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / 'ação própria 🚀.txt'
        self.file.write_text('Conteúdo de prova çãé', encoding='utf-8')
        self.second = Path(self.temp.name) / 'second.txt'
        self.second.write_bytes(b'second fixture')
        self.cdp = UploadCDP()
        self.state = {'tabs': {'owned': {}}}
        self.refresh()

    def refresh(self):
        observed = web.operate(self.cdp, self.state, lambda: None, {'action': 'observe', 'tab': 'owned'})
        self.ref = next(i['ref'] for i in observed['items'] if i['name'] == 'Arquivo local')
        return observed

    def upload(self, **args):
        return web.operate(self.cdp, self.state, lambda: None,
                           {'action': 'upload', 'tab': 'owned', 'ref': self.ref,
                            'files': [str(self.file)], **args})

    def mutations(self):
        return [p for m, p in self.cdp.calls if m == 'DOM.setFileInputFiles']

    def test_selects_unicode_file_once_and_only_verifies_names_and_sizes(self):
        result = self.upload()
        metadata = [{'name': self.file.name, 'size': self.file.stat().st_size}]
        self.assertEqual(result['effect'], 'selection_verified')
        self.assertTrue(result['selection_verified'])
        self.assertEqual(result['selected_files'], metadata)
        self.assertEqual(result['expected_files'], metadata)
        self.assertFalse(result['transfer_verified'])
        self.assertFalse(result['retry_mutation'])
        self.assertEqual(self.mutations(), [{'objectId': 'file-input-object', 'files': [str(self.file)]}])
        self.assertNotIn(self.temp.name, json.dumps(result))
        self.assertFalse(any(m.startswith('Input.') or m in ('DOM.focus', 'Page.navigate') for m, _ in self.cdp.calls))

    def test_multiple_selection_requires_and_uses_input_multiple(self):
        files = [str(self.file), str(self.second)]
        with self.assertRaisesRegex(web.WebError, 'multiple_files_not_allowed'):
            self.upload(files=files)
        self.assertEqual(self.mutations(), [])
        self.cdp.input_info['multiple'] = True
        self.refresh()
        result = self.upload(files=files)
        self.assertTrue(result['selection_verified'])
        self.assertEqual([i['name'] for i in result['selected_files']], [self.file.name, self.second.name])
        self.assertEqual(len(self.mutations()), 1)

    def test_wrong_input_detached_disabled_and_directory_refused_before_mutation(self):
        cases = [({'tag': 'BUTTON'}, 'not_file_input'), ({'type': 'text'}, 'not_file_input'),
                 ({'connected': False}, 'node_unavailable'), ({'disabled': True}, 'file_input_disabled'),
                 ({'directory': True}, 'directory_input_unsupported')]
        normal = dict(self.cdp.input_info)
        for update, reason in cases:
            with self.subTest(update=update):
                self.cdp.input_info = {**normal, **update}
                self.refresh()
                with self.assertRaisesRegex(web.WebError, reason):
                    self.upload()
                self.assertEqual(self.mutations(), [])

    def test_paths_are_absolute_nonempty_regular_and_exist(self):
        fifo = Path(self.temp.name) / 'fifo'
        os.mkfifo(fifo)
        for files in (None, [], str(self.file), ['relative.txt'], [str(self.file.parent)],
                      [str(self.file.parent / 'missing')], [str(fifo)], ['/bad\0path'], [12]):
            with self.subTest(files=files), self.assertRaises(web.WebError):
                self.upload(files=files)
        self.assertEqual(self.mutations(), [])
        self.assertFalse(any(m == 'DOM.resolveNode' for m, _ in self.cdp.calls))

    def test_unreadable_file_is_refused_without_opening_or_transmitting_content(self):
        with patch.object(web.os, 'access', return_value=False), self.assertRaisesRegex(web.WebError, 'file_unavailable'):
            self.upload()
        self.assertEqual(self.mutations(), [])
        self.assertFalse(any(m == 'DOM.resolveNode' for m, _ in self.cdp.calls))

    def test_regular_symlink_uses_chosen_filename_instead_of_renaming_to_target(self):
        alias = Path(self.temp.name) / 'alias.txt'
        alias.symlink_to(self.file)
        result = self.upload(files=[str(alias)])
        self.assertTrue(result['selection_verified'])
        self.assertEqual(result['selected_files'][0]['name'], 'alias.txt')
        self.assertEqual(self.mutations()[0]['files'], [str(alias)])

    def test_foreign_stale_and_navigated_refs_refuse_before_file_inspection(self):
        with patch.object(web, 'upload_paths') as inspect:
            with self.assertRaisesRegex(web.WebError, 'tab_not_owned'):
                self.upload(tab='foreign')
            with self.assertRaisesRegex(web.WebError, 'stale_ref'):
                self.upload(ref='guessed:1')
            self.cdp.tree['frame']['loaderId'] = 'navigated'
            with self.assertRaisesRegex(web.WebError, 'stale_ref'):
                self.upload()
            inspect.assert_not_called()
        self.assertEqual(self.mutations(), [])

    def test_refs_invalidated_before_only_mutation_and_failure_is_never_retried(self):
        def before_dispatch():
            self.assertNotIn('refs', self.state['tabs']['owned'])
            self.assertNotIn('latest_snapshot', self.state['tabs']['owned'])
            self.assertEqual(len(self.state['tabs']['owned']['snapshots']), 1)
        self.cdp.before_dispatch = before_dispatch
        self.cdp.dispatch_error = True
        with self.assertRaisesRegex(web.WebError, 'upload_outcome_unknown'):
            self.upload()
        self.assertEqual(len(self.mutations()), 1)
        with self.assertRaisesRegex(web.WebError, 'stale_ref'):
            self.upload()
        self.assertEqual(len(self.mutations()), 1)
        self.assertTrue(any(m == 'Runtime.releaseObject' for m, _ in self.cdp.calls))

    def test_failed_readback_does_not_claim_selection_or_transfer(self):
        self.cdp.readback_error = True
        result = self.upload()
        self.assertEqual(result['effect'], 'unconfirmed')
        self.assertEqual(result['reason'], 'selection_readback_unavailable')
        self.assertFalse(result['selection_verified'])
        self.assertFalse(result['transfer_verified'])
        self.assertIsNone(result['selected_files'])
        self.assertEqual(len(self.mutations()), 1)

    def test_mismatch_after_page_clears_or_replaces_selection_is_unconfirmed(self):
        for selected in ([], [{'name': self.file.name, 'size': 0}], [{'name': 'other.txt', 'size': self.file.stat().st_size}]):
            with self.subTest(selected=selected):
                self.refresh()
                self.cdp.selected_override = selected
                result = self.upload()
                self.assertFalse(result['selection_verified'])
                self.assertFalse(result['transfer_verified'])
                self.assertEqual(result['selected_files'], selected)
                self.assertEqual(result['reason'], 'selection_did_not_match')
        self.assertEqual(len(self.mutations()), 3)

    def test_detached_or_changed_input_after_selection_cannot_be_verified(self):
        for field in ('connected', 'file_input'):
            with self.subTest(field=field):
                self.refresh()
                self.cdp.readback_flags = {field: False}
                result = self.upload()
                self.assertEqual(result['effect'], 'unconfirmed')
                self.assertFalse(result['selection_verified'])
                self.assertEqual(result['selected_files'][0]['name'], self.file.name)

    def test_document_change_during_preflight_prevents_selection(self):
        self.cdp.navigate_during_check = True
        with self.assertRaisesRegex(web.WebError, 'stale_ref'):
            self.upload()
        self.assertEqual(self.mutations(), [])

    def test_upload_keeps_handoff_gate_before_inspection(self):
        with patch.object(web, 'request'), patch.object(web, 'control', side_effect=RuntimeError('human-control')), \
                patch.object(web, 'bench_browser_snapshot') as snapshot, patch.object(web, 'upload_paths') as inspect:
            with self.assertRaisesRegex(RuntimeError, 'human-control'):
                web.run({'bench': 'demo', 'mission': 'test', 'action': 'upload', 'files': [str(self.file)]})
            snapshot.assert_not_called()
            inspect.assert_not_called()

    def test_cli_repeated_file_flags_produce_mcp_compatible_array(self):
        argv = ['agent-bench-web', '--bench', 'demo', '--mission', 'test', 'upload',
                '--tab', 'owned', '--ref', self.ref, '--file', str(self.file), '--file', str(self.second)]
        with patch('sys.argv', argv), patch('sys.stdout', io.StringIO()), patch.object(web, 'run', return_value={}) as run:
            self.assertEqual(web.main(), 0)
        self.assertEqual(run.call_args.args[0]['files'], [str(self.file), str(self.second)])
        self.assertEqual(run.call_args.args[0]['action'], 'upload')


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.cdp = FrameCDP()
        self.state = {'tabs': {'owned': {}}}
        self.frames = self.operation('frames')['frames']
        self.frame = next(f['frame'] for f in self.frames if f['frame_id'] == 'child-a')
        self.sibling = next(f['frame'] for f in self.frames if f['frame_id'] == 'child-b')
        self.nested = next(f['frame'] for f in self.frames if f['frame_id'] == 'grandchild')

    def operation(self, action='observe', **args):
        return web.operate(self.cdp, self.state, lambda: None, {'action': action, 'tab': 'owned', **args})

    def observed(self, **args):
        return self.operation(frame=self.frame, **args)

    def input_calls(self):
        return [(m, p) for m, p in self.cdp.calls if m.startswith('Input.')]

    def test_discovery_refs_include_nested_lineage_but_do_not_create_or_adopt_tabs(self):
        self.assertEqual(set(self.state['tabs']), {'owned'})
        self.assertEqual([f['frame_id'] for f in self.frames], ['child-a', 'grandchild', 'child-b'])
        grandchild = next(f for f in self.frames if f['frame_id'] == 'grandchild')
        self.assertEqual(grandchild['parent_frame'], self.frame)
        self.assertEqual(grandchild['depth'], 2)
        self.assertEqual(self.frames[0]['availability'], 'available')
        self.assertEqual(self.frames[0]['title'], 'Quadro child-a')
        self.assertEqual([p['targetId'] for m, p in self.cdp.calls if m == 'Target.attachToTarget'], ['owned'])

    def test_raw_ids_foreign_tabs_and_refs_from_another_owned_tab_are_refused(self):
        with self.assertRaisesRegex(web.WebError, 'frame_not_discovered'):
            self.operation(frame='child-a')
        with self.assertRaisesRegex(web.WebError, 'tab_not_owned'):
            self.operation(tab='foreign', frame=self.frame)
        self.state['tabs']['foreign'] = {}
        with self.assertRaisesRegex(web.WebError, 'frame_not_discovered'):
            self.operation(tab='foreign', frame=self.frame)
        self.assertFalse(any(m == 'Accessibility.getFullAXTree' for m, _ in self.cdp.calls))

    def test_oopif_or_missing_local_document_is_explicitly_unavailable(self):
        self.cdp.unavailable.add('child-a')
        result = self.operation('frames')
        child = next(f for f in result['frames'] if f['frame_id'] == 'child-a')
        self.assertEqual(child['availability'], 'unavailable')
        self.assertEqual(child['reason'], 'frame_not_local_or_unloaded')
        with self.assertRaisesRegex(web.WebError, 'frame_unavailable'):
            self.operation(frame=child['frame'])
        self.assertFalse(any(m == 'Accessibility.getFullAXTree' for m, _ in self.cdp.calls))
        self.assertTrue(all(p['targetId'] == 'owned' for m, p in self.cdp.calls if m == 'Target.attachToTarget'))

    def test_selected_ax_tree_and_cached_continuation_are_bound_to_frame_document(self):
        first = self.observed()
        self.assertEqual(first['frame'], self.frame)
        self.assertEqual(first['frame_id'], 'child-a')
        self.assertNotIn('Root fixture', json.dumps(first))
        text = next(i for i in first['items'] if i['role'] == 'textbox')
        part = self.operation('read', frame=self.frame, snapshot=first['snapshot'], ref=text['ref'],
                              field='value', text_offset=2000)
        self.assertEqual(text['value'] + part['text'], self.cdp.values[104])
        self.assertEqual(part['frame_id'], 'child-a')
        self.assertEqual([p for m, p in self.cdp.calls if m == 'Accessibility.getFullAXTree'], [{'frameId': 'child-a'}])
        with self.assertRaisesRegex(web.WebError, 'stale_snapshot'):
            self.operation('read', frame=self.sibling, snapshot=first['snapshot'])
        with self.assertRaisesRegex(web.WebError, 'stale_snapshot'):
            self.operation('read', snapshot=first['snapshot'])

    def test_regions_delta_and_unicode_fill_within_frame(self):
        first = self.observed()
        region = next(i for i in first['items'] if i['role'] == 'region')
        scoped = self.observed(region=region['ref'])
        field = next(i for i in scoped['items'] if i['role'] == 'textbox')
        filled = self.operation('fill', frame=self.frame, ref=field['ref'], text='Ação no quadro 🚀 çãé')
        self.assertTrue(filled['value_matches'])
        changed = self.observed(since=scoped['snapshot'])
        self.assertEqual(changed['delta']['status'], 'compared')
        self.assertEqual(changed['delta']['changed'], 1)
        self.assertEqual(changed['changes'][0]['item']['value'], 'Ação no quadro 🚀 çãé')
        self.assertEqual(changed['selection']['region'], 'dom:101')
        self.assertEqual([p['backendNodeId'] for m, p in self.cdp.calls if m == 'DOM.focus'], [104])

    def test_delta_never_compares_root_or_sibling_as_same_document(self):
        root = self.operation()
        child = self.observed(since=root['snapshot'])
        self.assertEqual(child['delta']['reason'], 'document_changed')
        sibling = self.operation(frame=self.sibling, since=child['snapshot'])
        self.assertEqual(sibling['delta']['reason'], 'document_changed')
        self.assertEqual(sibling['frame_id'], 'child-b')

    def test_nested_frame_observation_requires_all_ancestor_documents(self):
        nested = self.operation(frame=self.nested)
        self.assertEqual(nested['frame_id'], 'grandchild')
        self.cdp.tree['childFrames'][0]['frame']['loaderId'] = 'parent-reloaded'
        with self.assertRaisesRegex(web.WebError, 'stale_frame_ref'):
            self.operation('read', frame=self.nested, snapshot=nested['snapshot'])

    def test_detach_reparent_and_root_navigation_refuse_old_frame_ref(self):
        for change in ('detach', 'reparent', 'root-navigation'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'detach':
                    self.cdp.tree['childFrames'][0]['childFrames'] = []
                elif change == 'reparent':
                    nested = self.cdp.tree['childFrames'][0]['childFrames'].pop()
                    self.cdp.tree['childFrames'][1]['childFrames'] = [nested]
                else:
                    self.cdp.tree['frame']['loaderId'] = 'another-root-document'
                with self.assertRaisesRegex(web.WebError, 'frame_detached|stale_frame_ref'):
                    self.operation(frame=self.nested)
                self.assertEqual(self.input_calls(), [])

    def test_changed_dom_owner_or_document_is_not_hidden_by_same_loader_id(self):
        for position in (0, 1):
            with self.subTest(position=position):
                self.setUp()
                self.cdp.documents['child-a'][position] += 900
                with self.assertRaisesRegex(web.WebError, 'stale_frame_ref'):
                    self.observed()

    def test_new_discovery_expires_frame_ref_without_replacing_current_element_refs(self):
        observed = self.observed()
        old = self.frame
        current = next(f['frame'] for f in self.operation('frames')['frames'] if f['frame_id'] == 'child-a')
        with self.assertRaisesRegex(web.WebError, 'frame_not_discovered'):
            self.operation('read', frame=old, snapshot=observed['snapshot'])
        result = self.operation('read', frame=current, snapshot=observed['snapshot'])
        self.assertEqual(result['items'], observed['items'])
        self.assertEqual(result['frame'], current)

    def test_rediscovery_does_not_resurrect_snapshot_or_refs_after_scope_identity_changes(self):
        for change in ('ancestor', 'owner', 'document'):
            with self.subTest(change=change):
                self.setUp()
                first = self.observed()
                field = next(i for i in first['items'] if i['role'] == 'textbox')
                if change == 'ancestor':
                    self.cdp.tree['frame']['loaderId'] = 'new-ancestor-document'
                elif change == 'owner':
                    self.cdp.documents['child-a'][0] += 900
                else:
                    self.cdp.documents['child-a'][1] += 900
                    self.cdp.frame_nodes['child-a'][0]['backendDOMNodeId'] += 900
                current = next(f['frame'] for f in self.operation('frames')['frames'] if f['frame_id'] == 'child-a')
                with self.assertRaisesRegex(web.WebError, 'stale_snapshot'):
                    self.operation('read', frame=current, snapshot=first['snapshot'])
                with self.assertRaisesRegex(web.WebError, 'stale_ref'):
                    self.operation('fill', frame=current, ref=field['ref'], text='stale')
                fresh = self.operation(frame=current, since=first['snapshot'])
                self.assertEqual(fresh['delta']['reason'], 'document_changed')
                self.assertEqual(self.input_calls(), [])

    def test_wrong_ax_document_or_frame_id_fails_without_new_snapshot(self):
        for field, value in [('backendDOMNodeId', 9999), ('frameId', 'unowned-frame')]:
            with self.subTest(field=field):
                self.setUp()
                self.cdp.frame_nodes['child-a'][0][field] = value
                with self.assertRaisesRegex(web.WebError, 'frame_observation_unverified'):
                    self.observed()
                self.assertNotIn('refs', self.state['tabs']['owned'])
                self.assertFalse(self.state['tabs']['owned'].get('snapshots'))

    def test_frame_navigation_during_capture_does_not_produce_empty_delta(self):
        first = self.observed()
        self.cdp.navigate_during_frame_ax = True
        with self.assertRaisesRegex(web.WebError, 'stale_frame_ref'):
            self.observed(since=first['snapshot'])
        self.assertNotIn('refs', self.state['tabs']['owned'])
        self.assertEqual(len(self.state['tabs']['owned']['snapshots']), 1)

    def test_frame_click_uses_transformed_root_quads_and_scrolled_document_hit_test(self):
        observed = self.observed()
        button = next(i for i in observed['items'] if i['role'] == 'button')
        result = self.operation('click', frame=self.frame, ref=button['ref'])
        self.assertEqual(result['effect'], 'dispatched')
        hit = next(p for m, p in self.cdp.calls if m == 'DOM.getNodeForLocation')
        self.assertEqual(hit, {'x': 463, 'y': 1380})
        presses = [p for m, p in self.input_calls() if m == 'Input.dispatchMouseEvent' and p['type'] == 'mousePressed']
        self.assertEqual([(p['x'], p['y']) for p in presses], [(340, 480)])
        self.assertNotIn('refs', self.state['tabs']['owned'])

    def test_frame_click_accepts_verified_descendant_but_refuses_parent_overlay_and_other_nodes(self):
        for frame_id, backend, contained, success in [('child-a', 105, True, True),
                                                     ('frame', 90, True, False),
                                                     ('child-b', 90, True, False),
                                                     ('child-a', 106, False, False)]:
            with self.subTest(frame_id=frame_id, backend=backend):
                self.setUp()
                observed = self.observed()
                button = next(i for i in observed['items'] if i['role'] == 'button')
                self.cdp.hit = {'frameId': frame_id, 'backendNodeId': backend}
                self.cdp.hit_contained = contained
                if success:
                    self.operation('click', frame=self.frame, ref=button['ref'])
                    self.assertTrue(self.input_calls())
                else:
                    with self.assertRaisesRegex(web.WebError, 'target_obscured'):
                        self.operation('click', frame=self.frame, ref=button['ref'])
                    self.assertEqual(self.input_calls(), [])

    def test_pinch_zoom_and_failed_hit_test_are_refused_without_guessing_coordinates(self):
        observed = self.observed()
        button = next(i for i in observed['items'] if i['role'] == 'button')
        self.cdp.viewport['scale'] = 2
        with self.assertRaisesRegex(web.WebError, 'frame_geometry_unavailable'):
            self.operation('click', frame=self.frame, ref=button['ref'])
        self.assertEqual(self.input_calls(), [])
        self.cdp.viewport['scale'] = 1
        self.cdp.hit_error = True
        observed = self.observed()
        button = next(i for i in observed['items'] if i['role'] == 'button')
        with self.assertRaisesRegex(web.WebError, 'cdp_error'):
            self.operation('click', frame=self.frame, ref=button['ref'])
        self.assertEqual(self.input_calls(), [])

    def test_navigation_during_fill_or_click_preflight_stops_before_input(self):
        for action in ('fill', 'click'):
            with self.subTest(action=action):
                self.setUp()
                observed = self.observed()
                role = 'textbox' if action == 'fill' else 'button'
                node = next(i for i in observed['items'] if i['role'] == role)
                if action == 'fill':
                    self.cdp.navigate_during_resolve = True
                else:
                    self.cdp.navigate_during_hit = True
                with self.assertRaisesRegex(web.WebError, 'stale_frame_ref'):
                    self.operation(action, frame=self.frame, ref=node['ref'], text='new')
                self.assertEqual(self.input_calls(), [])

    def test_unsupported_frame_actions_never_fall_back_to_main_document(self):
        self.cdp.calls = []
        for action in ('navigate', 'close', 'upload', 'scroll', 'key', 'screenshot', 'popups'):
            with self.subTest(action=action), self.assertRaisesRegex(web.WebError, 'frame_action_unsupported'):
                self.operation(action, frame=self.frame)
        self.assertEqual(self.cdp.calls, [])

    def test_duplicate_frames_and_discovery_race_do_not_persist_partial_refs(self):
        self.cdp.tree['childFrames'].append(copy.deepcopy(self.cdp.tree['childFrames'][0]))
        with self.assertRaisesRegex(web.WebError, 'frame_tree_ambiguous'):
            self.operation('frames')
        self.assertEqual(self.state['tabs']['owned']['frame_refs'], {})
        self.cdp.tree['childFrames'].pop()
        self.cdp.navigate_during_description = True
        with self.assertRaisesRegex(web.WebError, 'frame_tree_changed'):
            self.operation('frames')
        self.assertEqual(self.state['tabs']['owned']['frame_refs'], {})

    def test_frame_routes_keep_human_control_before_browser_inspection(self):
        for action in ('frames', 'observe', 'fill', 'click'):
            with self.subTest(action=action), patch.object(web, 'request'), \
                    patch.object(web, 'control', side_effect=RuntimeError('human-control')), \
                    patch.object(web, 'bench_browser_snapshot') as inspect:
                with self.assertRaisesRegex(RuntimeError, 'human-control'):
                    web.run({'bench': 'demo', 'mission': 'test', 'action': action, 'frame': self.frame})
                inspect.assert_not_called()


class FakeCDP:
    def __init__(self, navigation_error=False):
        self.calls = []
        self.navigation_error = navigation_error
        self.nodes = fixture_nodes()
        self.tree = {'frame': {'id': 'frame', 'loaderId': 'document', 'url': 'about:blank'}}
        self.navigate_during_ax = False
        self.targets = [page('owned'), page('foreign')]

    def call(self, method, params=None, session=None):
        self.calls.append((method, params))
        if method == 'Target.getTargets':
            return {'targetInfos': copy.deepcopy(self.targets)}
        if method == 'Target.createTarget':
            return {'targetId': 'new'}
        if method == 'Target.attachToTarget':
            return {'sessionId': 'session'}
        if method == 'Page.navigate':
            return {'errorText': 'failed'} if self.navigation_error else {}
        if method == 'Page.getFrameTree':
            return {'frameTree': copy.deepcopy(self.tree)}
        if method == 'Accessibility.getFullAXTree':
            if self.navigate_during_ax:
                self.tree['frame']['loaderId'] += '-raced'
            return {'nodes': copy.deepcopy(self.nodes)}
        return {}


class UploadCDP(FakeCDP):
    def __init__(self):
        super().__init__()
        self.nodes = [ax(1, 'RootWebArea', 'Upload fixture', [7]), ax(7, 'button', 'Arquivo local')]
        self.input_info = {'connected': True, 'tag': 'INPUT', 'type': 'file',
                           'multiple': False, 'disabled': False, 'directory': False}
        self.selected = []
        self.selected_override = None
        self.readback_flags = {}
        self.dispatch_error = self.readback_error = self.navigate_during_check = False
        self.before_dispatch = lambda: None

    def call(self, method, params=None, session=None):
        result = super().call(method, params, session)
        if method == 'DOM.resolveNode':
            return {'object': {'objectId': 'file-input-object'}}
        if method == 'Runtime.callFunctionOn':
            if 'tag:this.tagName' in params['functionDeclaration']:
                if self.navigate_during_check:
                    self.tree['frame']['loaderId'] = 'new-document'
                value = self.input_info
            else:
                if self.readback_error:
                    raise RuntimeError('context destroyed by onchange')
                value = {'connected': True, 'file_input': True, **self.readback_flags,
                         'files': self.selected if self.selected_override is None else self.selected_override}
            return {'result': {'value': copy.deepcopy(value)}}
        if method == 'DOM.setFileInputFiles':
            self.before_dispatch()
            if self.dispatch_error:
                raise RuntimeError('transport lost after dispatch')
            self.selected = [{'name': Path(path).name, 'size': Path(path).stat().st_size} for path in params['files']]
        return result


class FrameCDP(FakeCDP):
    def __init__(self):
        super().__init__()
        self.tree['childFrames'] = [
            {'frame': {'id': 'child-a', 'loaderId': 'a-doc', 'url': 'https://example.org/a'},
             'childFrames': [{'frame': {'id': 'grandchild', 'loaderId': 'grand-doc', 'url': 'https://example.org/grand'}}]},
            {'frame': {'id': 'child-b', 'loaderId': 'b-doc', 'url': 'https://example.org/b'}}]
        self.documents = {'child-a': [20, 100], 'child-b': [21, 200], 'grandchild': [22, 300]}
        self.frame_nodes = {}
        self.values = {}
        for frame_id, (_, document) in self.documents.items():
            self.values[document + 4] = 'texto do quadro çãé 🚀 ' * 105
            self.frame_nodes[frame_id] = [
                {**ax(document, 'RootWebArea', frame_id, [document + 1, document + 2]), 'frameId': frame_id},
                ax(document + 1, 'region', 'Região filha', [document + 4]),
                ax(document + 4, 'textbox', 'Campo filho', value=self.values[document + 4]),
                ax(document + 2, 'button', 'Salvar filho')]
        self.nodes[0]['name']['value'] = 'Root fixture'
        self.unavailable = set()
        self.viewport = {'pageX': 123, 'pageY': 900, 'scale': 1}
        self.quads = [[330, 450, 370, 470, 350, 510, 310, 490]]
        self.hit = {'frameId': 'child-a', 'backendNodeId': 102}
        self.hit_contained = True
        self.hit_error = self.navigate_during_frame_ax = self.navigate_during_resolve = False
        self.navigate_during_hit = self.navigate_during_description = False
        self.focused = None

    def call(self, method, params=None, session=None):
        result = super().call(method, params, session)
        if method == 'DOM.getFrameOwner':
            return {'backendNodeId': self.documents[params['frameId']][0]}
        if method == 'DOM.describeNode':
            frame_id, (_, document) = next((k, v) for k, v in self.documents.items() if v[0] == params['backendNodeId'])
            node = {'backendNodeId': params['backendNodeId'], 'frameId': frame_id,
                    'attributes': ['title', 'Quadro ' + frame_id]}
            if frame_id not in self.unavailable:
                node['contentDocument'] = {'nodeType': 9, 'backendNodeId': document}
            if self.navigate_during_description:
                self.tree['frame']['loaderId'] += '-new'
            return {'node': node}
        if method == 'Accessibility.getFullAXTree' and params and params.get('frameId'):
            if self.navigate_during_frame_ax:
                self.tree['childFrames'][0]['frame']['loaderId'] = 'navigated'
            return {'nodes': copy.deepcopy(self.frame_nodes[params['frameId']])}
        if method == 'DOM.resolveNode':
            if self.navigate_during_resolve:
                self.tree['childFrames'][0]['frame']['loaderId'] = 'navigated'
            return {'object': {'objectId': 'node:' + str(params['backendNodeId'])}}
        if method == 'Runtime.callFunctionOn':
            function = params['functionDeclaration']
            if 'function(s)' in function:
                backend = int(params['objectId'].split(':')[1])
                value = self.values[backend] == params['arguments'][0]['value']
            elif 'function(e)' in function:
                value = self.hit_contained
            else:
                value = True
            return {'result': {'value': value}}
        if method == 'DOM.focus':
            self.focused = params['backendNodeId']
        if method == 'Input.insertText':
            self.values[self.focused] = params['text']
            for nodes in self.frame_nodes.values():
                for node in nodes:
                    if node.get('backendDOMNodeId') == self.focused:
                        node['value'] = {'value': params['text']}
        if method == 'DOM.getContentQuads':
            return {'quads': self.quads}
        if method == 'Page.getLayoutMetrics':
            return {'cssVisualViewport': self.viewport}
        if method == 'DOM.getNodeForLocation':
            if self.hit_error:
                raise web.WebError('cdp_error: hit test unavailable')
            if self.navigate_during_hit:
                self.tree['childFrames'][0]['frame']['loaderId'] = 'navigated'
            return self.hit
        return result


if __name__ == '__main__':
    unittest.main()
