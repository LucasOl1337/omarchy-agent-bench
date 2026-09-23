import io
import json
from contextlib import nullcontext
from copy import deepcopy
import unittest
from unittest.mock import patch

import bench_hub_mcp as hub_module
from bench_catalog import CatalogError
from bench_native import NATIVE_UNSUPPORTED_TOOLS


class HubDiscoveryTests(unittest.TestCase):
    def test_discovery_filters_unbound_tools_without_starting_driver_or_bench(self):
        hub = hub_module.Hub()
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {}}
        catalog = [{'name': name, 'inputSchema': schema} for name in ['click', *NATIVE_UNSUPPORTED_TOOLS]]
        with patch.object(hub_module, 'load_cua_tools', return_value=catalog), \
             patch.object(hub_module, 'ensure') as ensure, patch.object(hub.cua, 'driver') as driver:
            reply = hub.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        names = {tool['name'] for tool in reply['result']['tools']}
        self.assertIn('click', names)
        self.assertIn('bench_web', names)
        self.assertFalse(names & NATIVE_UNSUPPORTED_TOOLS)
        click = next(tool for tool in reply['result']['tools'] if tool['name'] == 'click')
        self.assertIn('bench', click['inputSchema']['properties'])
        driver.assert_not_called()
        ensure.assert_not_called()

    def test_catalog_error_is_explicit_and_hub_remains_responsive(self):
        hub = hub_module.Hub()
        with patch.object(hub_module, 'load_cua_tools', side_effect=CatalogError('catalog_timeout', 'fixture')), \
             patch.object(hub.cua, 'driver') as driver:
            error = hub.handle({'jsonrpc': '2.0', 'id': 5, 'method': 'tools/list'})
        self.assertEqual(error['error']['code'], -32603)
        self.assertNotIn('result', error)
        driver.assert_not_called()
        reply = hub.handle({'jsonrpc': '2.0', 'id': 6, 'method': 'initialize'})
        self.assertEqual(reply['result']['serverInfo']['name'], 'agent-bench')

    def test_next_discovery_replaces_native_name_inventory(self):
        hub = hub_module.Hub()
        with patch.object(hub_module, 'load_cua_tools', side_effect=[[{'name': 'click'}], [{'name': 'scroll'}]]):
            hub.tools()
            hub.tools()
        self.assertEqual(hub.cua_names, {'scroll'})


class HubEnvelopeTests(unittest.TestCase):
    VALID = {'jsonrpc': '2.0', 'id': 'valid-after', 'method': 'tools/call',
             'params': {'name': 'get_screen_size', 'arguments': {'bench': 'fixture'}}}

    def run_with_following_call(self, first):
        hub = hub_module.Hub()
        entry = object()
        hub.cua.drivers['fixture'] = entry
        events = []

        def call(bench, message):
            self.assertNotIn('close', events, 'malformed input closed the existing pool')
            self.assertIs(entry, hub.cua.drivers['fixture'])
            self.assertEqual('fixture', bench)
            events.append('call')
            return hub_module.jsonrpc_result(message['id'], {'content': []})

        following = [{'jsonrpc': '2.0', 'method': 'notifications/initialized'}, self.VALID]
        stdin = io.StringIO(first + '\n' + ''.join(json.dumps(msg) + '\n' for msg in following))
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(hub_module, 'Hub', return_value=hub), \
             patch.object(hub_module.signal, 'signal'), \
             patch.object(hub_module.sys, 'stdin', stdin), \
             patch.object(hub_module.sys, 'stdout', stdout), \
             patch.object(hub_module.sys, 'stderr', stderr), \
             patch.object(hub_module, 'ensure', return_value={}) as ensure, \
             patch.object(hub_module, 'infer_owner', return_value='fixture-owner'), \
             patch.object(hub_module, 'control', return_value=nullcontext()), \
             patch.object(hub_module, 'handle_hub') as handle_hub, \
             patch.object(hub_module, 'load_cua_tools', return_value=[{'name': 'fixture'}]) as catalog, \
             patch('subprocess.Popen', side_effect=AssertionError('unexpected process spawn')) as spawn, \
             patch.object(hub.cua, 'driver') as driver, \
             patch.object(hub.cua, 'call', side_effect=call) as forwarded, \
             patch.object(hub.cua, 'close', side_effect=lambda: events.append('close')):
            hub_module.run()
        ensure.assert_called_once_with('fixture', owner='fixture-owner')
        driver.assert_not_called()
        handle_hub.assert_not_called()
        catalog.assert_not_called()
        spawn.assert_not_called()
        forwarded.assert_called_once_with('fixture', {
            'jsonrpc': '2.0', 'id': 'valid-after', 'method': 'tools/call',
            'params': {'name': 'get_screen_size', 'arguments': {}}})
        self.assertEqual(['call', 'close'], events)
        return [json.loads(line) for line in stdout.getvalue().splitlines()], stderr.getvalue()

    def test_stdio_malformed_json_envelopes_and_params_do_not_close_pool(self):
        cases = [('{invalid-json', -32700, None)]
        cases += [(json.dumps(value), -32600, None)
                  for value in (None, True, 123, 'scalar', [], ['array'])]
        cases += [(json.dumps({'id': 'bad', 'method': method, 'params': value}), -32602, 'bad')
                  for method in ('initialize', 'tools/call', 'tools/list')
                  for value in (None, [], '', False, 0, 'invalid', ['invalid'])]
        for line, code, request_id in cases:
            with self.subTest(line=line):
                replies, _ = self.run_with_following_call(line)
                self.assertEqual(2, len(replies))
                self.assertEqual(request_id, replies[0]['id'])
                self.assertEqual(code, replies[0]['error']['code'])
                self.assertEqual('valid-after', replies[1]['id'])
                self.assertNotIn('error', replies[1])

    def test_stdio_missing_call_id_and_invalid_notifications_log_without_dispatch(self):
        messages = [{'method': 'tools/call', 'params': {
            'name': name, 'arguments': {'bench': 'fixture'}}}
            for name in ('click', 'get_screen_size', 'bench_ensure', 'bench_launch', 'bench_browser')]
        messages += [{'method': method, 'params': value}
                     for method in ('initialize', 'tools/list', 'notifications/initialized')
                     for value in (None, [], 'invalid')]
        for message in messages:
            with self.subTest(message=message):
                replies, stderr = self.run_with_following_call(json.dumps(message))
                self.assertEqual(1, len(replies))
                self.assertEqual('valid-after', replies[0]['id'])
                self.assertIn('NATIVE_REQUEST_INVALID', stderr)
                if message['method'] == 'tools/call':
                    self.assertIn('exige id', stderr)


class HubToolSelectionTests(unittest.TestCase):
    CATALOG = [{'name': name, 'description': name, 'inputSchema': {
        'type': 'object', 'properties': {'value': {'type': ['string', 'null']}}}}
        for name in ('click', 'browser_dialog', 'scroll')]

    def setUp(self):
        blocker = patch('subprocess.Popen', side_effect=AssertionError('unexpected process spawn'))
        self.spawn = blocker.start()
        self.addCleanup(blocker.stop)
        self.addCleanup(self.spawn.assert_not_called)

    def test_omitted_selection_keeps_complete_catalog_and_lazy_discovery(self):
        with patch.object(hub_module, 'load_cua_tools', return_value=deepcopy(self.CATALOG)) as load:
            hub = hub_module.Hub()
            load.assert_not_called()
            actual = hub.tools()
            expected = [*hub_module.HUB_TOOLS,
                        *(hub_module.inject_bench_schema(tool)
                          for tool in (self.CATALOG[0], self.CATALOG[2]))]
            self.assertEqual(expected, actual)
            load.assert_called_once_with()

    def test_hub_only_needs_neither_static_dump_nor_installed_driver(self):
        with patch.object(hub_module, 'load_cua_tools', side_effect=AssertionError('no dump allowed')) as load, \
             patch('bench_catalog.shutil.which', side_effect=AssertionError('no binary lookup allowed')) as lookup, \
             patch.object(hub_module, 'ensure') as ensure:
            hub = hub_module.Hub(['bench_list', 'bench_web', 'bench_list'])
            reply = hub.handle({'id': 1, 'method': 'tools/list'})
            expected = [tool for tool in hub_module.HUB_TOOLS if tool['name'] in ('bench_list', 'bench_web')]
            self.assertEqual(expected, reply['result']['tools'])
            self.assertEqual(['bench_web', 'bench_list'], [tool['name'] for tool in expected])
            self.assertEqual(set(), hub.cua_names)
            load.assert_not_called()
            lookup.assert_not_called()
            ensure.assert_not_called()

    def test_mixed_selection_preserves_original_order_contracts_and_sources(self):
        catalog = deepcopy(self.CATALOG)
        before = deepcopy(catalog)
        with patch.object(hub_module, 'load_cua_tools', return_value=catalog) as load:
            hub = hub_module.Hub(['scroll', 'bench_list', 'click', 'click'])
            selected = hub.tools()
            self.assertEqual(['bench_list', 'click', 'scroll'], [tool['name'] for tool in selected])
            self.assertEqual(hub_module.inject_bench_schema(catalog[0]), selected[1])
            self.assertEqual(hub_module.inject_bench_schema(catalog[2]), selected[2])
            self.assertEqual({'click', 'scroll'}, hub.cua_names)
            self.assertEqual(2, load.call_count, 'native discovery must not acquire a new cache')
            self.assertEqual(before, catalog)

    def test_empty_unknown_or_refused_selection_is_configuration_error_before_pool(self):
        cases = ([], (), '', 'bench_list', [''], [' '], [None], [[]],
                 ['missing'], ['bench_list', 'missing'], ['browser_dialog'], ['set_config'])
        with patch.object(hub_module, 'load_cua_tools', return_value=self.CATALOG), \
             patch.object(hub_module, 'CuaPool') as pool, \
             patch.object(hub_module, 'ensure') as ensure:
            for tools in cases:
                with self.subTest(tools=tools), self.assertRaisesRegex(hub_module.ToolSelectionError, 'TOOLS_CONFIG_INVALID'):
                    hub_module.Hub(tools)
            pool.assert_not_called()
            ensure.assert_not_called()

    def test_native_catalog_failure_never_expands_or_partially_publishes_selection(self):
        with patch.object(hub_module, 'load_cua_tools', side_effect=CatalogError('catalog_unavailable', 'fixture')), \
             self.assertRaisesRegex(hub_module.ToolSelectionError, 'TOOLS_CONFIG_INVALID'):
            hub_module.Hub(['click'])
        with patch.object(hub_module, 'load_cua_tools', side_effect=[self.CATALOG, [self.CATALOG[2]]]):
            hub = hub_module.Hub(['bench_list', 'click'])
            reply = hub.handle({'id': 2, 'method': 'tools/list'})
            self.assertIn('error', reply)
            self.assertNotIn('result', reply)
            self.assertIn('catalog_selection_invalid', reply['error']['message'])

    def test_outside_selection_is_refused_before_every_dispatch_route_then_allowed_call_works(self):
        hub = hub_module.Hub(['bench_list'])
        original_pool = hub.cua
        with patch.object(hub_module, 'ensure') as ensure, \
             patch.object(hub_module, 'handle_hub', return_value=[]) as handle, \
             patch.object(hub_module, 'control') as control, \
             patch.object(hub.cua, 'driver') as driver, \
             patch.object(hub.cua, 'call') as call:
            for tool in ('bench_ensure', 'bench_web', 'bench_browser', 'click', 'set_config', 'unknown'):
                reply = hub.handle({'id': 3, 'method': 'tools/call',
                                    'params': {'name': tool, 'arguments': {'bench': 'fixture'}}})
                self.assertTrue(reply['result']['isError'])
                self.assertIn('TOOL_NOT_ALLOWED', reply['result']['content'][0]['text'])
            ensure.assert_not_called()
            handle.assert_not_called()
            control.assert_not_called()
            driver.assert_not_called()
            call.assert_not_called()
            reply = hub.handle({'id': 4, 'method': 'tools/call',
                                'params': {'name': 'bench_list', 'arguments': {}}})
            self.assertFalse(reply['result'].get('isError'))
            handle.assert_called_once_with('bench_list', {})
            self.assertIs(original_pool, hub.cua)

    def test_allowed_native_dispatch_preserves_arguments_and_does_not_replay(self):
        with patch.object(hub_module, 'load_cua_tools', return_value=self.CATALOG):
            hub = hub_module.Hub(['click'])
        messages = [{'id': 1, 'method': 'tools/call', 'params': {'name': 'click'}},
                    {'id': 2, 'method': 'tools/call', 'params': {'name': 'click', 'arguments': None}},
                    {'id': 3, 'method': 'tools/call', 'params': {'name': 'click', 'arguments': {}}},
                    {'id': 4, 'method': 'tools/call', 'params': {'name': 'click', 'arguments': {'delivery_mode': None}}}]
        before = deepcopy(messages)
        with patch.object(hub_module, 'ensure', return_value={}), \
             patch.object(hub_module, 'control', return_value=nullcontext()), \
             patch.object(hub.cua, 'call', return_value={'result': {'content': []}}) as call:
            for message in messages:
                hub.handle(message)
            self.assertEqual(len(messages), call.call_count)
            self.assertEqual([{'jsonrpc': '2.0', **message} for message in messages],
                             [invocation.args[1] for invocation in call.call_args_list])
        self.assertEqual(before, messages)

    def test_default_core_drops_tools_missing_without_cua_driver(self):
        with patch.object(hub_module, 'load_cua_tools', return_value=self.CATALOG), \
             patch.object(hub_module, 'CuaPool'):
            hub = hub_module.Hub(list(hub_module.CORE_TOOLS), optional=True)
            names = [tool['name'] for tool in hub.tools()]
        self.assertIn('bench_web', names)
        self.assertLessEqual(set(names), set(hub_module.CORE_TOOLS))

    def test_cli_parses_names_and_keeps_default_and_invalid_configuration_explicit(self):
        with patch.object(hub_module, 'run') as run:
            hub_module.main([])
            run.assert_called_once_with(list(hub_module.CORE_TOOLS), optional=True)
            run.reset_mock()
            hub_module.main(['--tools', 'all'])
            run.assert_called_once_with(None)
            run.reset_mock()
            hub_module.main(['--tools', 'bench_list', 'bench_web'])
            run.assert_called_once_with(['bench_list', 'bench_web'])
        with patch.object(hub_module, 'load_cua_tools', return_value=self.CATALOG), \
             patch.object(hub_module, 'CuaPool') as pool, \
             patch.object(hub_module.sys, 'stderr', io.StringIO()):
            for argv in (['--tools'], ['--tools', ''], ['--tools', 'unknown']):
                with self.subTest(argv=argv), self.assertRaises(SystemExit) as error:
                    hub_module.main(argv)
                self.assertEqual(2, error.exception.code)
            pool.assert_not_called()


if __name__ == '__main__':
    unittest.main()
