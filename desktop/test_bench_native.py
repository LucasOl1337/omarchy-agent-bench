import io
import json
from copy import deepcopy
from contextlib import contextmanager, nullcontext
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import bench_mcp
import bench_native as native
import bench_ops as ops
from bench_hub_mcp import Hub


CONFIG_FIELDS = {'capture_mode': 'vision', 'max_image_dimension': 800,
                 'experimental_pip': True, 'experimental_pip_geometry': '320x200+24+24'}
CONFIG_REQUESTS = [*({'key': key, 'value': value} for key, value in CONFIG_FIELDS.items()),
                   *({key: value} for key, value in CONFIG_FIELDS.items()),
                   dict(CONFIG_FIELDS), {'key': 'max_image_dimension', 'value': 800, 'experimental_pip': True},
                   {}, {'key': 'future_setting', 'value': True}]
LAUNCH_REQUESTS = [{'name': 'Fixture editor'}, {'bundle_id': 'fixture.desktop'},
                   {'launch_path': '/tmp/fixture editor --title "two words" $(do-not-run)'},
                   {'launch_path': 'https://example.invalid/'}, {}, {'pid': 200}]
DELIVERY_TOOLS = ('click', 'double_click', 'right_click', 'drag',
                  'type_text', 'press_key', 'hotkey', 'scroll')
EXPLICIT_DELIVERY_MODES = ('foreground', 'background', None, 'invalid', '', False, [], {})


class NativeIsolation(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.proc = self.root / 'proc'
        self.cgroup = self.root / 'cgroup'
        self.own = '/user.slice/app.slice/agent-bench@fixture.service'
        for field, value in [('PROC', self.proc), ('CGROUP', self.cgroup)]:
            patcher = patch.object(ops, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(ops, 'request', return_value={'name': 'fixture', 'server_pid': 100})
        self.request = patcher.start()
        self.addCleanup(patcher.stop)
        self.add(100, self.own, 'Xvnc')
        self.add(200, self.own, 'own-app')
        self.add(201, self.own + '/children', 'child-app')
        self.add(900, '/user.slice/app.slice/human.scope', 'human-app')
        self.add(901, '/user.slice/app.slice/agent-bench@neighbor.service', 'neighbor-app')

    def add(self, pid, group, command='app', start=1234):
        proc = self.proc / str(pid)
        proc.mkdir(parents=True)
        (proc / 'cmdline').write_bytes(('/usr/bin/' + command).encode() + b'\0')
        (proc / 'cgroup').write_text('0::' + group + '\n')
        fields = ['S', '1', *(['0'] * 17), str(start)]
        (proc / 'stat').write_text(f'{pid} (app name) ' + ' '.join(fields) + '\n')
        cgroup = self.cgroup / group.lstrip('/')
        cgroup.mkdir(parents=True, exist_ok=True)
        with (cgroup / 'cgroup.procs').open('a') as out:
            out.write(str(pid) + '\n')

    @staticmethod
    def message(tool='kill_app', **arguments):
        return {'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                'params': {'name': tool, 'arguments': arguments}}

    def test_delivery_default_only_fills_missing_field_without_mutating_request(self):
        self.assertEqual(set(DELIVERY_TOOLS), native.FOREGROUND_DEFAULT_TOOLS)
        for tool in DELIVERY_TOOLS:
            request = self.message(tool, target={'kind': 'window', 'pid': 200, 'window_id': 3},
                                   text='unchanged', session='fixture')
            request['params']['_meta'] = {'trace': 'kept'}
            before = deepcopy(request)
            expected = deepcopy(request)
            expected['params']['arguments']['delivery_mode'] = 'foreground'
            with self.subTest(tool=tool):
                normalized = native.normalize_request(request)
                self.assertEqual(expected, normalized)
                self.assertEqual(before, request)
                self.assertIsNot(request, normalized)
                self.assertIsNot(request['params'], normalized['params'])
                self.assertIsNot(request['params']['arguments'], normalized['params']['arguments'])
                self.assertEqual(normalized, native.normalize_request(normalized))

    def test_explicit_delivery_modes_are_not_repaired_or_replaced(self):
        for tool in DELIVERY_TOOLS:
            for value in EXPLICIT_DELIVERY_MODES:
                request = self.message(tool, delivery_mode=value)
                before = deepcopy(request)
                with self.subTest(tool=tool, value=value):
                    self.assertEqual(before, native.normalize_request(request))
                    self.assertEqual(before, request)

    def test_delivery_normalization_preserves_other_tools_methods_and_invalid_payloads(self):
        messages = [self.message(tool) for tool in
                    ('get_window_state', 'set_value', 'move_cursor', 'mouse_down',
                     'browser_dialog', 'future_tool', 'replay_trajectory')]
        messages += [{'method': method, 'params': {'name': 'click', 'arguments': {}}}
                     for method in ('initialize', 'tools/list', 'notifications/initialized')]
        messages += [{'method': 'tools/call', 'params': value} for value in (None, [], 'invalid')]
        for tool in DELIVERY_TOOLS:
            messages.append({'method': 'tools/call', 'params': {'name': tool}})
            messages += [{'method': 'tools/call', 'params': {'name': tool, 'arguments': value}}
                         for value in (None, [], '', False, 'invalid', ['invalid'])]
        for message in messages:
            before = deepcopy(message)
            with self.subTest(message=message):
                self.assertEqual(before, native.normalize_request(message))
                self.assertEqual(before, message)

    def test_both_transports_forward_defaults_and_explicit_modes_once_without_mutation(self):
        for route in ('dedicated', 'hub'):
            hub = Hub()
            channel = MagicMock()
            channel.receive.return_value = {'jsonrpc': '2.0', 'id': 7, 'result': {'content': []}}
            entry = {'channel': channel, 'io': threading.Lock()}
            with patch('bench_hub_mcp.ensure', return_value={}), \
                 patch('bench_hub_mcp.control', return_value=nullcontext()), \
                 patch.object(hub.cua, 'driver', return_value=entry), \
                 patch.object(bench_mcp, 'control', return_value=nullcontext()):
                for tool in DELIVERY_TOOLS:
                    modes = [{}, *({'delivery_mode': value} for value in EXPLICIT_DELIVERY_MODES)]
                    for mode in modes:
                        arguments = {'pid': 200, 'window_id': 3, 'session': 'fixture', **mode}
                        expected = {**arguments} if mode else {**arguments, 'delivery_mode': 'foreground'}
                        if route == 'hub':
                            arguments['bench'] = 'fixture'
                        request = self.message(tool, **arguments)
                        before = deepcopy(request)
                        channel.reset_mock()
                        with self.subTest(route=route, tool=tool, mode=mode):
                            if route == 'hub':
                                reply = hub.handle(request)
                            else:
                                reply = bench_mcp._exchange('fixture', channel, request, 1)
                            self.assertFalse(reply['result'].get('isError'))
                            channel.send.assert_called_once()
                            sent = channel.send.call_args.args[0]
                            self.assertEqual(expected, sent['params']['arguments'])
                            self.assertEqual(tool, sent['params']['name'])
                            self.assertEqual(before, request)

    def test_both_transports_preserve_absent_and_null_arguments_and_other_tools(self):
        for route in ('dedicated', 'hub'):
            hub = Hub()
            channel = MagicMock()
            channel.receive.return_value = {'jsonrpc': '2.0', 'id': 7, 'result': {'content': []}}
            entry = {'channel': channel, 'io': threading.Lock()}
            with patch('bench_hub_mcp.ensure', return_value={}), \
                 patch('bench_hub_mcp.control', return_value=nullcontext()), \
                 patch.object(hub.cua, 'driver', return_value=entry), \
                 patch.object(bench_mcp, 'control', return_value=nullcontext()):
                messages = [self.message('get_window_state', pid=200, window_id=3),
                            self.message('set_value', pid=200, value='untouched')]
                for tool in DELIVERY_TOOLS:
                    messages += [{'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                                  'params': {'name': tool}},
                                 {'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                                  'params': {'name': tool, 'arguments': None}}]
                for request in messages:
                    expected = deepcopy(request)
                    if route == 'hub' and isinstance(request['params'].get('arguments'), dict):
                        request['params']['arguments']['bench'] = 'fixture'
                    before = deepcopy(request)
                    channel.reset_mock()
                    with self.subTest(route=route, request=request):
                        if route == 'hub':
                            reply = hub.handle(request)
                        else:
                            reply = bench_mcp._exchange('fixture', channel, request, 1)
                        self.assertFalse(reply['result'].get('isError'))
                        channel.send.assert_called_once()
                        self.assertEqual(expected, channel.send.call_args.args[0])
                        self.assertEqual(before, request)

    def test_both_catalogs_advertise_only_supported_delivery_defaults_without_mutation(self):
        field = {'type': 'string', 'enum': ['background', 'foreground'],
                 'default': 'background', 'description': 'Upstream background default.'}
        names = (*DELIVERY_TOOLS, 'get_window_state', 'future_tool', 'browser_dialog')
        catalog = [{'name': name, 'description': 'tool description kept', 'inputSchema': {
            'type': 'object', 'additionalProperties': False, 'required': ['target'],
            'properties': {'delivery_mode': deepcopy(field),
                           'target': {'type': 'object', 'properties': {'pid': {'type': 'integer'}}}}}}
                   for name in names]
        before = deepcopy(catalog)
        channel = MagicMock()
        upstream = {'id': 9, 'result': {'tools': catalog, 'nextCursor': 'next'}}
        channel.receive.return_value = upstream
        with patch('bench_hub_mcp.load_cua_tools', return_value=catalog):
            replies = [bench_mcp._exchange('fixture', channel,
                                          {'id': 9, 'method': 'tools/list'}, 1),
                       Hub().handle({'id': 9, 'method': 'tools/list'})]
        self.assertEqual('next', replies[0]['result']['nextCursor'])
        for route, reply in zip(('dedicated', 'hub'), replies):
            tools = {tool['name']: deepcopy(tool) for tool in reply['result']['tools']}
            self.assertNotIn('browser_dialog', tools)
            for original in before[:-1]:
                expected = deepcopy(original)
                if original['name'] in DELIVERY_TOOLS:
                    expected['inputSchema']['properties']['delivery_mode'].update(
                        default='foreground', description=native.DELIVERY_MODE_DESCRIPTION)
                actual = tools[original['name']]
                if route == 'hub':
                    actual['inputSchema']['properties'].pop('bench')
                with self.subTest(route=route, tool=original['name']):
                    self.assertEqual(expected, actual)
        self.assertEqual(before, catalog)
        self.assertEqual({'id': 9, 'result': {'tools': before, 'nextCursor': 'next'}}, upstream)
        self.assertEqual(native.available_tools(catalog),
                         native.available_tools(native.available_tools(catalog)))

    def test_own_and_descendant_targets_allowed_but_human_neighbor_and_unknown_rejected(self):
        for pid in (200, 201):
            self.assertIsNotNone(native.guard_request('fixture', self.message(pid=pid)))
        for pid in (900, 901, 999):
            with self.subTest(pid=pid), self.assertRaises(native.NativeIsolationError):
                native.guard_request('fixture', self.message(pid=pid))

    def test_special_or_ambiguous_pids_are_never_process_targets(self):
        for pid in (0, -1, True, 200.0, '200', None):
            with self.subTest(pid=pid), self.assertRaisesRegex(native.NativeIsolationError, 'PID_INVALID'):
                native.guard_request('fixture', self.message(pid=pid))
        with self.assertRaisesRegex(native.NativeIsolationError, 'PID_AMBIGUOUS'):
            native.guard_request('fixture', self.message('click', pid=200, target={'pid': 201}))
        for arguments in ({}, {'target': {'pid': 200}}):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(native.NativeIsolationError, 'PID_INVALID'):
                native.guard_request('fixture', self.message(**arguments))

    def test_nested_target_pid_is_checked_even_without_top_level_pid(self):
        with self.assertRaisesRegex(native.NativeIsolationError, 'PID_OUTSIDE'):
            native.guard_request('fixture', self.message('click', target={'kind': 'window', 'pid': 900, 'window_id': 1}))

    def test_unit_name_in_an_unrelated_tree_is_not_membership(self):
        self.add(800, '/foreign.slice/agent-bench@fixture.service', 'own-app')
        with self.assertRaisesRegex(native.NativeIsolationError, 'PID_OUTSIDE'):
            native.guard_request('fixture', self.message(pid=800))

    def test_fake_server_name_or_membership_fails_closed(self):
        (self.proc / '100/cmdline').write_bytes(b'/usr/bin/not-Xvnc\0')
        with self.assertRaisesRegex(native.NativeIsolationError, 'BENCH_UNCERTAIN'):
            native.guard_request('fixture', self.message(pid=200))
        (self.proc / '100/cmdline').write_bytes(b'/usr/bin/Xvnc\0')
        (self.cgroup / self.own.lstrip('/') / 'cgroup.procs').write_text('200\n')
        with self.assertRaisesRegex(native.NativeIsolationError, 'BENCH_UNCERTAIN'):
            native.guard_request('fixture', self.message(pid=200))

    def test_membership_and_pid_reuse_are_checked(self):
        guard = native.NativeGuard('fixture')
        (self.cgroup / self.own.lstrip('/') / 'cgroup.procs').write_text('100\n')
        with self.assertRaisesRegex(native.NativeIsolationError, 'PID_UNCERTAIN'):
            guard.require(200)
        (self.cgroup / self.own.lstrip('/') / 'cgroup.procs').write_text('100\n200\n')
        original = native._process
        calls = 0
        def reused(pid):
            nonlocal calls
            group, start = original(pid)
            if pid == 200:
                calls += 1
                start += calls
            return group, start
        with patch.object(native, '_process', side_effect=reused), self.assertRaisesRegex(native.NativeIsolationError, 'PID_UNCERTAIN'):
            guard.require(200)

    def test_cua_auxiliary_requires_actual_binding_and_control_group(self):
        unit = 'agent-bench-cua-fixture-' + 'a' * 32 + '.service'
        group = '/user.slice/app.slice/' + unit
        self.add(700, group, 'cua-driver')
        properties = {'Id': unit, 'LoadState': 'loaded', 'ActiveState': 'active',
                      'ControlGroup': group, 'BindsTo': 'agent-bench@fixture.service'}
        with patch.object(native, '_unit_properties', return_value=properties) as query:
            native.guard_request('fixture', self.message(pid=700))
            query.assert_called_once_with(unit)
        for changes in ({'BindsTo': 'agent-bench@neighbor.service'}, {'ControlGroup': '/'},
                        {'ActiveState': 'inactive'}, {'Id': 'spoof.service'}, {'LoadState': 'not-found'}):
            with self.subTest(changes=changes), \
                 patch.object(native, '_unit_properties', return_value={**properties, **changes}), \
                 self.assertRaises(native.NativeIsolationError):
                native.guard_request('fixture', self.message(pid=700))
        with patch.object(native, '_unit_properties', side_effect=TimeoutError()), \
             self.assertRaises(native.NativeIsolationError):
            native.guard_request('fixture', self.message(pid=700))
        with patch.object(native, '_unit_properties', side_effect=[properties, {**properties, 'BindsTo': ''}]) as query:
            guard = native.guard_request('fixture', self.message(pid=700))
            with self.assertRaisesRegex(native.NativeIsolationError, 'PID_OUTSIDE'):
                native.recheck_request(guard)
            self.assertEqual(2, query.call_count)

    def test_discovery_filters_structured_and_text_without_exposing_foreign_pids(self):
        payload = {'apps': [
            {'pid': 200, 'name': 'own', 'running': True, 'active': False},
            {'pid': 900, 'name': 'private-human-app', 'running': True, 'active': False},
            {'pid': 901, 'name': 'neighbor', 'running': True, 'active': False},
            {'pid': 0, 'name': 'installed-only', 'running': False, 'active': False}],
            'global_diagnostic': 'private-human-app'}
        reply = {'id': 7, 'result': {'structuredContent': payload,
                 'content': [{'type': 'text', 'text': 'raw global PID 900 private-human-app'}]}}
        result = native.filter_response('fixture', self.message('list_apps'), reply)
        self.assertEqual([200, 0], [row['pid'] for row in result['result']['structuredContent']['apps']])
        self.assertNotIn('private-human', json.dumps(result))
        self.assertNotIn('900', json.dumps(result))
        self.assertNotIn('global_diagnostic', json.dumps(result))

    def test_window_discovery_drops_unknown_pid_and_human_title(self):
        payload = {'windows': [{'pid': 200, 'window_id': 1, 'title': 'own'},
                               {'pid': None, 'window_id': 2, 'title': 'unknown'},
                               {'pid': 900, 'window_id': 3, 'title': 'human-private'}]}
        reply = {'id': 7, 'result': {'structuredContent': payload}}
        result = native.filter_response('fixture', self.message('list_windows'), reply)
        self.assertEqual([{'pid': 200, 'window_id': 1, 'title': 'own'}], result['result']['structuredContent']['windows'])
        self.assertNotIn('human-private', json.dumps(result))

    def test_unknown_discovery_format_and_legacy_global_tree_refuse_explicitly(self):
        with self.assertRaisesRegex(native.NativeIsolationError, 'DISCOVERY_UNCERTAIN'):
            native.filter_response('fixture', self.message('list_apps'), {'result': {'content': [{'type': 'text', 'text': 'host inventory'}]}})
        with self.assertRaisesRegex(native.NativeIsolationError, 'DISCOVERY_UNSUPPORTED'):
            native.guard_request('fixture', self.message('get_accessibility_tree'))

    def test_available_tools_preserves_supported_schema_and_removes_refused_names(self):
        supported = {'name': 'click', 'inputSchema': {'type': 'object'}}
        catalog = [supported, *({'name': name} for name in native.NATIVE_UNSUPPORTED_TOOLS)]
        self.assertEqual([supported], native.available_tools(catalog))
        self.assertEqual(len(native.NATIVE_UNSUPPORTED_TOOLS) + 1, len(catalog))
        for malformed in (None, {}, ['click'], [{'description': 'missing name'}]):
            with self.subTest(catalog=malformed), self.assertRaisesRegex(native.NativeIsolationError, 'TOOL_CATALOG_UNCERTAIN'):
                native.available_tools(malformed)

    def test_hub_unbound_browser_and_replay_never_reach_driver_even_with_own_pid(self):
        hub = Hub()
        with patch('bench_hub_mcp.ensure', return_value={}), \
             patch('bench_hub_mcp.control', return_value=nullcontext()), \
             patch.object(hub.cua, 'driver') as driver:
            for name in sorted(native.BROWSER_UNBOUND_TOOLS | {'replay_trajectory'}):
                for arguments in ({}, {'pid': 200}):
                    with self.subTest(tool=name, arguments=arguments):
                        reply = hub.handle(self.message(name, bench='fixture', **arguments))
                        self.assertTrue(reply['result']['isError'])
                        code = 'NATIVE_REPLAY_UNBOUND' if name == 'replay_trajectory' else 'NATIVE_BROWSER_UNBOUND'
                        self.assertIn(code, reply['result']['content'][0]['text'])
            driver.assert_not_called()

    def test_set_config_rejects_all_shapes_before_process_or_config_inspection(self):
        with patch.object(native, 'NativeGuard') as guard:
            for arguments in CONFIG_REQUESTS:
                for pid in ({}, {'pid': 200}):
                    with self.subTest(arguments=arguments, pid=pid), \
                         self.assertRaisesRegex(native.NativeIsolationError, 'NATIVE_CONFIG_GLOBAL.*max_dimension'):
                        native.guard_request('fixture', self.message('set_config', **arguments, **pid))
            guard.assert_not_called()

    def test_install_extension_rejects_preview_and_confirm_before_process_inspection(self):
        with patch.object(native, 'NativeGuard') as guard:
            for arguments in ({}, {'confirm': True}, {'pid': 200, 'confirm': True}):
                with self.subTest(arguments=arguments), \
                     self.assertRaisesRegex(native.NativeIsolationError, 'NATIVE_EXTENSION_GLOBAL'):
                    native.guard_request('fixture', self.message('install_extension', **arguments))
            guard.assert_not_called()

    def test_hub_launch_app_never_reaches_driver_or_server_but_bench_launch_does(self):
        hub = Hub()
        with patch('bench_hub_mcp.ensure', return_value={}), \
             patch('bench_hub_mcp.control', return_value=nullcontext()), \
             patch('bench_hub_mcp.request', return_value={'pid': 201}) as request, \
             patch.object(hub.cua, 'driver') as driver, \
             patch.object(native, 'NativeGuard') as guard:
            for arguments in LAUNCH_REQUESTS:
                with self.subTest(arguments=arguments):
                    reply = hub.handle(self.message('launch_app', bench='fixture', **arguments))
                    self.assertTrue(reply['result']['isError'])
                    text = reply['result']['content'][0]['text']
                    self.assertIn('NATIVE_LAUNCH_TRANSIENT', text)
                    self.assertIn('bench_launch', text)
                    self.assertIn('agent-bench launch NOME -- APP ARG...', text)
                    self.assertIn('bench_browser', text)
            request.assert_not_called()
            driver.assert_not_called()
            guard.assert_not_called()
            argv = ['/tmp/fixture editor', '--title', 'two words', '$(do-not-run)']
            reply = hub.handle(self.message('bench_launch', bench='fixture', argv=argv))
            self.assertFalse(reply['result'].get('isError'))
            request.assert_called_once_with('fixture', {'action': 'launch', 'argv': argv, 'cwd': str(Path.cwd())})
            driver.assert_not_called()

    def test_dedicated_launch_app_never_reaches_fake_driver(self):
        log = self.root / 'fake-launch-driver.jsonl'
        fake = '''import json, sys
with open(sys.argv[1], 'w') as out:
 for line in sys.stdin:
  out.write(line); out.flush()
  msg=json.loads(line)
  print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'content':[]}}),flush=True)
'''
        proc = subprocess.Popen([sys.executable, '-u', '-c', fake, str(log)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        messages = [self.message('launch_app', **arguments) for arguments in LAUNCH_REQUESTS]
        messages.append(self.message('get_config'))
        for index, message in enumerate(messages):
            message['id'] = index
        output = io.StringIO()
        try:
            with patch.object(bench_mcp, 'launch_cua', return_value=proc), \
                 patch.object(bench_mcp, 'stop_cua'), patch.object(bench_mcp.signal, 'signal'), \
                 patch.object(bench_mcp, 'control') as control, \
                 patch.object(bench_mcp.sys, 'stdin', io.StringIO(''.join(json.dumps(msg) + '\n' for msg in messages))), \
                 patch.object(bench_mcp.sys, 'stdout', output):
                self.assertEqual(0, bench_mcp.run('fixture', {}))
                control.assert_not_called()
        finally:
            proc.wait(timeout=3)
            proc.stdout.close()
            proc.stdin.close()
        self.assertEqual([messages[-1]], [json.loads(line) for line in log.read_text().splitlines()])
        replies = list(map(json.loads, output.getvalue().splitlines()))
        self.assertEqual(len(messages), len(replies))
        for reply in replies[:-1]:
            self.assertTrue(reply['result']['isError'])
            self.assertIn('NATIVE_LAUNCH_TRANSIENT', reply['result']['content'][0]['text'])
        self.assertFalse(replies[-1]['result'].get('isError'))

    def test_hub_set_config_never_reaches_driver_and_per_call_cap_still_does(self):
        hub = Hub()
        channel = MagicMock()
        channel.receive.return_value = {'jsonrpc': '2.0', 'id': 7, 'result': {'content': []}}
        entry = {'channel': channel, 'io': threading.Lock()}
        with patch('bench_hub_mcp.ensure', return_value={}), \
             patch('bench_hub_mcp.control', return_value=nullcontext()), \
             patch.object(hub.cua, 'driver', return_value=entry) as driver:
            for arguments in CONFIG_REQUESTS:
                with self.subTest(arguments=arguments):
                    reply = hub.handle(self.message('set_config', bench='fixture', **arguments))
                    self.assertTrue(reply['result']['isError'])
                    self.assertIn('NATIVE_CONFIG_GLOBAL', reply['result']['content'][0]['text'])
            driver.assert_not_called()
            channel.send.assert_not_called()
            for name, arguments in [('get_config', {}),
                                    ('get_window_state', {'pid': 200, 'window_id': 1, 'max_dimension': 800})]:
                reply = hub.handle(self.message(name, bench='fixture', **arguments))
                self.assertFalse(reply['result'].get('isError'))
                self.assertEqual(arguments, channel.send.call_args.args[0]['params']['arguments'])

    def test_hub_catalog_hides_config_and_launch_but_preserves_supported_routes(self):
        catalog = [{'name': 'set_config', 'inputSchema': {'type': 'object'}},
                   {'name': 'launch_app', 'inputSchema': {'type': 'object'}},
                   {'name': 'get_config', 'inputSchema': {'type': 'object', 'properties': {}}},
                   {'name': 'get_window_state', 'inputSchema': {'type': 'object', 'properties': {
                       'pid': {'type': 'integer'}, 'max_dimension': {'type': 'integer', 'minimum': 1}}}}]
        with patch('bench_hub_mcp.load_cua_tools', return_value=catalog):
            reply = Hub().handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        tools = {tool['name']: tool for tool in reply['result']['tools']}
        self.assertNotIn('set_config', tools)
        self.assertNotIn('launch_app', tools)
        self.assertIn('bench_launch', tools)
        self.assertIn('bench_browser', tools)
        self.assertIn('get_config', tools)
        self.assertEqual({'type': 'integer', 'minimum': 1},
                         tools['get_window_state']['inputSchema']['properties']['max_dimension'])

    def test_dedicated_set_config_never_reaches_fake_driver_but_reads_and_cap_do(self):
        log = self.root / 'fake-config-driver.jsonl'
        fake = '''import json, sys
with open(sys.argv[1], 'w') as out:
 for line in sys.stdin:
  out.write(line); out.flush()
  msg=json.loads(line)
  print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'content':[]}}),flush=True)
'''
        proc = subprocess.Popen([sys.executable, '-u', '-c', fake, str(log)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        messages = [self.message('set_config', **args) for args in CONFIG_REQUESTS]
        messages.extend([self.message('get_config'),
                         self.message('get_window_state', pid=200, window_id=1, max_dimension=800)])
        for index, message in enumerate(messages):
            message['id'] = index
        output = io.StringIO()
        try:
            with patch.object(bench_mcp, 'launch_cua', return_value=proc), \
                 patch.object(bench_mcp, 'stop_cua'), patch.object(bench_mcp.signal, 'signal'), \
                 patch.object(bench_mcp, 'control', return_value=nullcontext()) as control, \
                 patch.object(bench_mcp.sys, 'stdin', io.StringIO(''.join(json.dumps(msg) + '\n' for msg in messages))), \
                 patch.object(bench_mcp.sys, 'stdout', output):
                self.assertEqual(0, bench_mcp.run('fixture', {}))
                control.assert_not_called()
        finally:
            proc.wait(timeout=3)
            proc.stdout.close()
            proc.stdin.close()
        forwarded = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(messages[-2:], forwarded)
        replies = list(map(json.loads, output.getvalue().splitlines()))
        self.assertEqual(len(messages), len(replies))
        for reply in replies[:-2]:
            self.assertTrue(reply['result']['isError'])
            self.assertIn('NATIVE_CONFIG_GLOBAL', reply['result']['content'][0]['text'])
        self.assertTrue(all(not reply['result'].get('isError') for reply in replies[-2:]))

    def test_tools_list_response_refuses_unknown_catalog_without_process_discovery(self):
        with patch.object(native, 'NativeGuard') as guard:
            request = {'method': 'tools/list'}
            error = {'error': {'message': 'backend unavailable'}}
            self.assertEqual(error, native.filter_response('fixture', request, error))
            for reply in ({'result': {}}, {'result': {'tools': 'unknown'}}):
                with self.subTest(reply=reply), self.assertRaisesRegex(native.NativeIsolationError, 'TOOL_CATALOG_UNCERTAIN'):
                    native.filter_response('fixture', request, reply)
            guard.assert_not_called()

    def test_hub_rejects_before_fake_driver_and_allows_only_verified_pid(self):
        hub = Hub()
        channel = MagicMock()
        channel.receive.return_value = {'jsonrpc': '2.0', 'id': 7, 'result': {'content': []}}
        entry = {'channel': channel, 'io': threading.Lock()}
        with patch('bench_hub_mcp.ensure', return_value={}), \
             patch('bench_hub_mcp.control', return_value=nullcontext()), \
             patch.object(hub.cua, 'driver', return_value=entry) as driver:
            request = self.message(pid=900, bench='fixture')
            reply = hub.handle(request)
            self.assertTrue(reply['result']['isError'])
            self.assertIn('PID_OUTSIDE', reply['result']['content'][0]['text'])
            driver.assert_not_called()
            channel.send.assert_not_called()
            hub.handle(self.message(pid=200, bench='fixture'))
            self.assertEqual(200, channel.send.call_args.args[0]['params']['arguments']['pid'])

    def test_hub_rechecks_original_pid_after_driver_start_or_io_wait(self):
        stat = self.proc / '200/stat'
        original = stat.read_text()
        for stage in ('driver', 'io'):
            with self.subTest(stage=stage):
                stat.write_text(original)
                hub = Hub()
                channel = MagicMock()
                def change_pid():
                    stat.write_text(original.replace('1234', '5678'))
                @contextmanager
                def io_gate():
                    if stage == 'io':
                        change_pid()
                    yield
                def driver(name):
                    if stage == 'driver':
                        change_pid()
                    return {'channel': channel, 'io': io_gate()}
                with patch('bench_hub_mcp.ensure', return_value={}), \
                     patch('bench_hub_mcp.control', return_value=nullcontext()), \
                     patch.object(hub.cua, 'driver', side_effect=driver):
                    reply = hub.handle(self.message(pid=200, bench='fixture'))
                    self.assertTrue(reply['result']['isError'])
                    self.assertIn('NATIVE_PID_CHANGED', reply['result']['content'][0]['text'])
                    self.assertNotIn('RESULTADO_INCERTO', reply['result']['content'][0]['text'])
                    channel.send.assert_not_called()

    def test_dedicated_bridge_rechecks_after_control_and_releases_gate_on_refusal(self):
        log = self.root / 'fake-late-driver.jsonl'
        fake = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.stdin.read())"
        proc = subprocess.Popen([sys.executable, '-c', fake, str(log)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        released = []
        @contextmanager
        def changed(name):
            path = self.proc / '200/stat'
            path.write_text(path.read_text().replace('1234', '5678'))
            try:
                yield
            finally:
                released.append(True)
        output = io.StringIO()
        try:
            with patch.object(bench_mcp, 'launch_cua', return_value=proc), \
                 patch.object(bench_mcp, 'stop_cua'), patch.object(bench_mcp.signal, 'signal'), \
                 patch.object(bench_mcp, 'control', side_effect=changed), \
                 patch.object(bench_mcp.sys, 'stdin', io.StringIO(json.dumps(self.message(pid=200)) + '\n')), \
                 patch.object(bench_mcp.sys, 'stdout', output):
                self.assertEqual(0, bench_mcp.run('fixture', {}))
        finally:
            proc.wait(timeout=3)
            proc.stdout.close()
            proc.stdin.close()
        self.assertEqual('', log.read_text())
        self.assertEqual([True], released)
        self.assertIn('NATIVE_PID_CHANGED', output.getvalue())

    def test_dedicated_bridge_never_forwards_foreign_pid_or_legacy_tree(self):
        log = self.root / 'fake-driver.jsonl'
        fake = '''import json, sys, time
with open(sys.argv[1], 'w') as out:
 for line in sys.stdin:
  out.write(line); out.flush()
  msg=json.loads(line)
  time.sleep(.03)
  payload={'apps':[{'pid':200,'name':'own','running':True,'active':False}, {'pid':900,'name':'human-private','running':True,'active':False}]}
  result={'content':[]} if msg['params']['name']!='list_apps' else {'structuredContent':payload,'content':[{'type':'text','text':'raw global human-private 900'}]}
  print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}),flush=True)
'''
        proc = subprocess.Popen([sys.executable, '-u', '-c', fake, str(log)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        messages = [self.message(pid=900), self.message('get_accessibility_tree'),
                    self.message('click', target={'kind': 'window', 'pid': 901, 'window_id': 1}),
                    self.message(pid=200), self.message('list_apps'), self.message('get_screen_size')]
        for index, message in enumerate(messages):
            message['id'] = index
        messages[-1]['id'] = 4  # Sequential requests may reuse an ID after its reply.
        output = io.StringIO()
        try:
            with patch.object(bench_mcp, 'launch_cua', return_value=proc), \
                 patch.object(bench_mcp, 'stop_cua'), patch.object(bench_mcp.signal, 'signal'), \
                 patch.object(bench_mcp, 'control', return_value=nullcontext()), \
                 patch.object(bench_mcp.sys, 'stdin', io.StringIO(''.join(json.dumps(msg) + '\n' for msg in messages))), \
                 patch.object(bench_mcp.sys, 'stdout', output):
                self.assertEqual(0, bench_mcp.run('fixture', {}))
        finally:
            proc.wait(timeout=3)
            proc.stdout.close()
            proc.stdin.close()
        forwarded = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(['kill_app', 'list_apps', 'get_screen_size'], [item['params']['name'] for item in forwarded])
        self.assertEqual(200, forwarded[0]['params']['arguments']['pid'])
        self.assertNotIn('human-private', output.getvalue())
        all_replies = list(map(json.loads, output.getvalue().splitlines()))
        reused = [item for item in all_replies if item['id'] == 4]
        self.assertEqual(2, len(reused))
        self.assertFalse(any(item['result'].get('isError') for item in reused))
        replies = {item['id']: item for item in all_replies}
        self.assertTrue(all(replies[index]['result']['isError'] for index in (0, 1, 2)))
        self.assertEqual([200], [item['pid'] for item in reused[0]['result']['structuredContent']['apps']])
        self.assertEqual([], reused[1]['result']['content'])

    def test_dedicated_bridge_hides_unsupported_catalog_and_refuses_old_clients(self):
        log = self.root / 'fake-catalog-driver.jsonl'
        names = sorted(native.NATIVE_UNSUPPORTED_TOOLS)
        catalog = [{'name': name, 'inputSchema': {'type': 'object'}} for name in [*names, 'list_apps', 'click']]
        fake = '''import json, sys
catalog=json.loads(sys.argv[2])
with open(sys.argv[1], 'w') as out:
 for line in sys.stdin:
  out.write(line); out.flush()
  msg=json.loads(line)
  print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'tools':catalog,'nextCursor':'next'}}),flush=True)
'''
        proc = subprocess.Popen([sys.executable, '-u', '-c', fake, str(log), json.dumps(catalog)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        messages = [self.message(name, pid=200) for name in names]
        for index, message in enumerate(messages):
            message['id'] = index
        messages.append({'jsonrpc': '2.0', 'id': 999, 'method': 'tools/list'})
        output = io.StringIO()
        try:
            with patch.object(bench_mcp, 'launch_cua', return_value=proc), \
                 patch.object(bench_mcp, 'stop_cua'), patch.object(bench_mcp.signal, 'signal'), \
                 patch.object(bench_mcp, 'control') as control, \
                 patch.object(bench_mcp.sys, 'stdin', io.StringIO(''.join(json.dumps(msg) + '\n' for msg in messages))), \
                 patch.object(bench_mcp.sys, 'stdout', output):
                self.assertEqual(0, bench_mcp.run('fixture', {}))
                control.assert_not_called()
        finally:
            proc.wait(timeout=3)
            proc.stdout.close()
            proc.stdin.close()
        self.assertEqual([messages[-1]], [json.loads(line) for line in log.read_text().splitlines()])
        replies = {item['id']: item for item in map(json.loads, output.getvalue().splitlines())}
        self.assertTrue(all(replies[index]['result']['isError'] for index in range(len(names))))
        self.assertEqual(['list_apps', 'click'], [tool['name'] for tool in replies[999]['result']['tools']])
        self.assertEqual('next', replies[999]['result']['nextCursor'])


if __name__ == '__main__':
    unittest.main()
