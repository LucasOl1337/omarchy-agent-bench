import io
import json
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import bench_mcp as bridge


class DedicatedBridgeTimeouts(unittest.TestCase):
    FAKE = '''import json, sys, time
mode, log = sys.argv[1:]
if mode == 'blocked_input':
 time.sleep(30)
if mode == 'split_budget':
 time.sleep(.14)
for line in sys.stdin:
 msg=json.loads(line)
 with open(log,'a') as out:
  out.write(json.dumps(msg)+'\\n')
 method=msg['method']
 if method == 'initialize' and mode in ('silent_init','partial_init'):
  if mode == 'partial_init':
   print('{"jsonrpc":',end='',flush=True)
  continue
 if 'id' not in msg:
  continue
 if method == 'tools/call':
  if mode == 'silent_call':
   continue
  if mode == 'eof_call':
   sys.exit(0)
  if mode == 'partial_call':
   print('{"jsonrpc":',end='',flush=True)
   continue
  if mode == 'malformed_call':
   print('invalid-json',flush=True)
   continue
  if mode in ('flood_call','wrong_id_flood'):
   while True:
    notice={'jsonrpc':'2.0','method':'notifications/progress'} if mode == 'flood_call' else {'jsonrpc':'2.0','id':'not-the-request','result':{}}
    print(json.dumps(notice),flush=True)
  if mode == 'split_budget':
   time.sleep(.14)
 result={'ok':True,'text':'ação'}
 if method == 'initialize':
  result={'protocolVersion':msg['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'fake','version':'1'}}
 if method == 'tools/list':
  result={'tools':[{'name':'click','inputSchema':{'type':'object'}},{'name':'page','inputSchema':{'type':'object'}}]}
 print(json.dumps({'jsonrpc':'2.0','method':'notifications/progress'}),flush=True)
 print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result},ensure_ascii=False),flush=True)
 if mode == 'block_after_initialize' and method == 'initialize':
  time.sleep(30)
'''

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.processes = []
        self.stopped = []
        self.events = []
        self.held = False
        self.mode = 'healthy'
        self.addCleanup(self.cleanup)

    def launch(self, name, env):
        log = self.root / f'{len(self.processes)}.jsonl'
        proc = subprocess.Popen([sys.executable, '-u', '-c', self.FAKE, self.mode, str(log)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        proc.fixture_log = log
        self.processes.append(proc)
        return proc

    @staticmethod
    def terminate(proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
        proc.stdin.close()
        proc.stdout.close()

    def stop(self, proc):
        self.assertIn(proc, self.processes)
        self.assertFalse(self.held, 'cleanup must not hold the input gate')
        self.events.append('stop-own-driver')
        self.stopped.append(proc)
        self.terminate(proc)

    def cleanup(self):
        for proc in self.processes:
            self.terminate(proc)

    @contextmanager
    def control(self, name):
        self.assertFalse(self.held)
        self.held = True
        self.events.append('gate-enter')
        try:
            yield
        finally:
            self.held = False
            self.events.append('gate-release')

    @staticmethod
    def message(tool='get_screen_size', request_id=1, **arguments):
        return {'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
                'params': {'name': tool, 'arguments': arguments}}

    @staticmethod
    def initialize():
        return {'jsonrpc': '2.0', 'id': 'init-client', 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'fixture-client', 'version': '1'}}}

    def run_bridge(self, messages, timeout=.16, broken_output=False):
        test = self
        class Input(io.StringIO):
            def __next__(self):
                test.assertFalse(test.held, 'input gate held while waiting for client input')
                return super().__next__()
        class Output(io.StringIO):
            def write(self, value):
                test.assertFalse(test.held, 'input gate held while writing to client')
                if broken_output:
                    raise BrokenPipeError('fixture client disconnected')
                return super().write(value)
        output, stderr = Output(), io.StringIO()
        start = time.monotonic()
        with patch.object(bridge, 'launch_cua', side_effect=self.launch), \
             patch.object(bridge, 'stop_cua', side_effect=self.stop), \
             patch.object(bridge, 'control', side_effect=self.control), \
             patch.object(bridge.signal, 'signal'), \
             patch.object(bridge.sys, 'stdin', Input(''.join(json.dumps(msg) + '\n' for msg in messages))), \
             patch.object(bridge.sys, 'stdout', output), patch.object(bridge.sys, 'stderr', stderr):
            status = bridge.run('fixture', {}, init_timeout=timeout, call_timeout=timeout)
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertFalse(self.held)
        return status, [json.loads(line) for line in output.getvalue().splitlines()], stderr.getvalue()

    def logged(self, proc=None):
        log = (proc or self.processes[-1]).fixture_log
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_handshake_and_client_notification_are_forwarded_without_synthetic_initialize(self):
        messages = [self.initialize(), {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                    {'jsonrpc': '2.0', 'id': 'catalog', 'method': 'tools/list'}, self.message('click')]
        status, replies, stderr = self.run_bridge(messages)
        self.assertEqual(0, status)
        self.assertEqual('', stderr)
        self.assertEqual([*messages[:-1], self.message('click', delivery_mode='foreground')], self.logged())
        self.assertEqual(['init-client', 'catalog', 1], [reply['id'] for reply in replies])
        self.assertEqual('2024-11-05', replies[0]['result']['protocolVersion'])
        self.assertEqual(['click'], [tool['name'] for tool in replies[1]['result']['tools']])
        self.assertEqual('ação', replies[2]['result']['text'])
        self.assertEqual(['gate-enter', 'gate-release', 'stop-own-driver'], self.events)

    def test_silent_and_partial_initialize_stop_before_any_queued_task_action(self):
        for mode in ('silent_init', 'partial_init'):
            with self.subTest(mode=mode):
                self.mode = mode
                status, replies, _ = self.run_bridge([self.initialize(), self.message('click')])
                self.assertEqual(1, status)
                self.assertEqual('init-client', replies[0]['id'])
                self.assertIn('CUA_INIT_TIMEOUT', replies[0]['error']['message'])
                self.assertEqual([self.initialize()], self.logged())
        self.assertNotIn('gate-enter', self.events)

    def test_silence_partial_line_flood_and_eof_are_bounded_reads(self):
        for mode in ('silent_call', 'partial_call', 'flood_call', 'wrong_id_flood', 'eof_call', 'malformed_call'):
            with self.subTest(mode=mode):
                self.mode = mode
                status, replies, _ = self.run_bridge([self.message()])
                code = 'CUA_TRANSPORTE_FALHOU' if mode in ('eof_call', 'malformed_call') else 'CUA_TIMEOUT'
                self.assertEqual(1, status)
                self.assertIn(code, replies[0]['result']['content'][0]['text'])
                self.assertEqual(1, len(self.logged()))
        self.assertNotIn('gate-enter', self.events)

    def test_mutation_failure_releases_gate_before_cleanup_and_never_replays(self):
        for mode in ('silent_call', 'partial_call', 'flood_call', 'eof_call', 'malformed_call'):
            with self.subTest(mode=mode):
                self.mode = mode
                self.events.clear()
                before = len(self.processes)
                status, replies, _ = self.run_bridge([self.message('click'), self.message('type_text', text='queued')])
                self.assertEqual(1, status)
                self.assertIn('CUA_RESULTADO_INCERTO', replies[0]['result']['content'][0]['text'])
                self.assertEqual(1, len(replies))
                self.assertEqual(1, len(self.logged()))
                self.assertEqual(before + 1, len(self.processes))
                self.assertEqual(['gate-enter', 'gate-release', 'stop-own-driver'], self.events)

    def test_full_stdin_pipe_cannot_block_initialize_or_mutation_forever(self):
        huge = 'x' * (1024 * 1024)
        initialize = self.initialize()
        initialize['params']['clientInfo']['extra'] = huge
        for message in (initialize, self.message('type_text', text=huge)):
            with self.subTest(method=message['method']):
                self.mode = 'blocked_input'
                status, replies, _ = self.run_bridge([message])
                self.assertEqual(1, status)
                expected = 'CUA_INIT_TIMEOUT' if message['method'] == 'initialize' else 'CUA_RESULTADO_INCERTO'
                self.assertIn(expected, json.dumps(replies))
                self.assertEqual([], self.logged())

    def test_send_and_receive_share_one_absolute_deadline(self):
        self.mode = 'split_budget'
        status, replies, _ = self.run_bridge([self.message('type_text', text='x' * (1024 * 1024))], timeout=.2)
        self.assertEqual(1, status)
        self.assertIn('CUA_RESULTADO_INCERTO', json.dumps(replies))
        self.assertEqual(1, len(self.logged()))

    def test_blocked_notification_send_is_bounded_and_has_no_reply_or_gate(self):
        self.mode = 'block_after_initialize'
        notice = {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {'extra': 'x' * (1024 * 1024)}}
        status, replies, stderr = self.run_bridge([self.initialize(), notice])
        self.assertEqual(1, status)
        self.assertEqual(['init-client'], [reply['id'] for reply in replies])
        self.assertIn('CUA_TIMEOUT', stderr)
        self.assertNotIn('gate-enter', self.events)

    def test_tool_notification_is_not_forwarded_without_a_reply_identity(self):
        notification = self.message('click')
        del notification['id']
        status, replies, stderr = self.run_bridge([notification, self.message()])
        self.assertEqual(0, status)
        self.assertEqual([self.message()], self.logged())
        self.assertEqual(1, len(replies))
        self.assertIn('NATIVE_REQUEST_INVALID', stderr)
        self.assertNotIn('gate-enter', self.events)

    def test_recovery_requires_a_new_bridge_and_cleanup_preserves_neighbor(self):
        neighbor = self.launch('neighbor', {})
        self.mode = 'silent_call'
        first_status, first_replies, _ = self.run_bridge([self.message('click')])
        self.assertEqual(1, first_status)
        self.assertIn('CUA_RESULTADO_INCERTO', json.dumps(first_replies))
        self.assertNotIn(neighbor, self.stopped)
        self.assertIsNone(neighbor.poll())

        self.assertEqual(2, len(self.processes))
        self.mode = 'healthy'
        status, replies, _ = self.run_bridge([self.initialize(), self.message()])
        self.assertEqual(0, status)
        self.assertEqual(3, len(self.processes))
        self.assertTrue(replies[-1]['result']['ok'])
        self.assertNotIn(neighbor, self.stopped)
        self.assertIsNone(neighbor.poll())

    def test_disconnected_client_cannot_retain_gate_or_skip_own_cleanup(self):
        with self.assertRaises(BrokenPipeError):
            self.run_bridge([self.message('click')], broken_output=True)
        self.assertEqual(['gate-enter', 'gate-release', 'stop-own-driver'], self.events)
        self.assertIsNotNone(self.processes[-1].poll())


class DedicatedBridgeEnvelope(unittest.TestCase):
    VALID = {'jsonrpc': '2.0', 'id': 'valid-after', 'method': 'tools/call',
             'params': {'name': 'get_screen_size', 'arguments': {}}}

    def run_fake(self, lines):
        proc, channel = MagicMock(), MagicMock()
        sent, events = [], []

        def send(message, deadline):
            self.assertNotIn('stop', events, 'invalid input stopped the existing driver')
            proc.stdin.close.assert_not_called()
            sent.append(deepcopy(message))
            events.append('send')

        def receive(request_id, deadline):
            return {'jsonrpc': '2.0', 'id': request_id, 'result': {'content': []}}

        channel.send.side_effect = send
        channel.receive.side_effect = receive
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(bridge, 'launch_cua', return_value=proc) as launch, \
             patch.object(bridge, 'stop_cua', side_effect=lambda _: events.append('stop')) as stop, \
             patch.object(bridge, 'CuaChannel', return_value=channel) as transport, \
             patch.object(bridge.signal, 'signal'), \
             patch.object(bridge, 'control', return_value=nullcontext()), \
             patch.object(bridge.sys, 'stdin', io.StringIO('\n'.join(lines) + '\n')), \
             patch.object(bridge.sys, 'stdout', stdout), patch.object(bridge.sys, 'stderr', stderr), \
             patch('subprocess.Popen', side_effect=AssertionError('unexpected process spawn')) as spawn:
            status = bridge.run('fixture', {})
        self.assertEqual(0, status)
        launch.assert_called_once_with('fixture', {})
        transport.assert_called_once_with(proc)
        stop.assert_called_once_with(proc)
        proc.stdin.close.assert_called_once()
        spawn.assert_not_called()
        self.assertEqual(['send'] * len(sent) + ['stop'], events)
        return sent, [json.loads(line) for line in stdout.getvalue().splitlines()], stderr.getvalue()

    def test_malformed_params_and_tool_names_do_not_stop_next_valid_call(self):
        messages = [{'id': 'bad', 'method': method, 'params': value}
                    for method in ('initialize', 'tools/list', 'tools/call')
                    for value in (None, [], '', 'invalid', ['invalid'], False, 0)]
        messages += [{'id': 'bad', 'method': 'tools/call', 'params': {'name': value}}
                     for value in (None, [], {}, ['click'], {'name': 'click'}, False, 1)]
        messages.append({'id': 'bad', 'method': 'tools/call', 'params': {}})
        for message in messages:
            with self.subTest(message=message):
                sent, replies, _ = self.run_fake([json.dumps(message), json.dumps(self.VALID)])
                self.assertEqual([self.VALID], sent)
                self.assertEqual(['bad', 'valid-after'], [reply['id'] for reply in replies])
                self.assertIn('NATIVE_REQUEST_INVALID', json.dumps(replies[0]))
                self.assertNotIn('RESULTADO_INCERTO', json.dumps(replies[0]))
                if message['method'] == 'tools/call':
                    self.assertTrue(replies[0]['result']['isError'])
                else:
                    self.assertIn('error', replies[0])

    def test_invalid_notifications_and_calls_without_id_never_reach_channel(self):
        messages = [{'method': 'tools/call', 'params': value}
                    for value in (None, [], 'invalid', {'name': []}, {'name': 'click'})]
        messages += [{'method': 'notifications/initialized', 'params': value}
                     for value in (None, [], 'invalid')]
        for message in messages:
            with self.subTest(message=message):
                sent, replies, stderr = self.run_fake([json.dumps(message), json.dumps(self.VALID)])
                self.assertEqual([self.VALID], sent)
                self.assertEqual(['valid-after'], [reply['id'] for reply in replies])
                self.assertIn('NATIVE_REQUEST_INVALID', stderr)

    def test_arguments_and_delivery_contract_survives_same_fake_connection(self):
        messages = [{'id': 0, 'method': 'tools/call', 'params': {'name': 'click', 'arguments': {}}}]
        messages += [{'id': i, 'method': 'tools/call', 'params': {
            'name': 'click', 'arguments': {'delivery_mode': value}}}
            for i, value in enumerate(('background', None, 'invalid'), start=1)]
        messages += [{'id': 4, 'method': 'tools/call', 'params': {'name': 'click', 'arguments': None}},
                     {'id': 5, 'method': 'tools/call', 'params': {'name': 'click'}}, self.VALID]
        expected = deepcopy(messages)
        expected[0]['params']['arguments']['delivery_mode'] = 'foreground'
        sent, replies, stderr = self.run_fake([json.dumps(message) for message in messages])
        self.assertEqual(expected, sent)
        self.assertEqual([message['id'] for message in messages], [reply['id'] for reply in replies])
        self.assertEqual('', stderr)
        self.assertTrue(all(not reply['result'].get('isError') for reply in replies))


if __name__ == '__main__':
    unittest.main()
