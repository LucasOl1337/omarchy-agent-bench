"""Clipboard subprocess failures must not become successful MCP results."""
from contextlib import contextmanager
import json
import unittest
from unittest.mock import patch

import bench_hub_mcp as hub


class HubClipboardTests(unittest.TestCase):
    def call(self, tool, response):
        events = []

        @contextmanager
        def gate(bench):
            self.assertEqual('fixture', bench)
            events.append('enter')
            try:
                yield
            finally:
                events.append('exit')

        text = 'Ação própria 日本語 🙂'
        with patch.object(hub, 'ensure', return_value={}) as ensure, \
             patch.object(hub, 'infer_owner', return_value='fixture-owner'), \
             patch.object(hub, 'control', side_effect=gate), \
             patch.object(hub, 'request', return_value=response) as request, \
             patch.object(hub, 'launch_cua', side_effect=AssertionError('no driver')), \
             patch.object(hub, 'load_cua_tools', side_effect=AssertionError('no catalog')):
            reply = hub.Hub([tool]).handle({
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': tool, 'arguments': {'bench': 'fixture', 'text': text}}})
        ensure.assert_called_once_with('fixture', owner='fixture-owner')
        expected = {'action': 'clipboard-get' if tool.endswith('_get') else 'clipboard-set'}
        if tool.endswith('_set'):
            expected['input'] = text
        request.assert_called_once_with('fixture', expected)
        self.assertEqual([] if tool.endswith('_get') else ['enter', 'exit'], events)
        return reply['result']

    def test_read_preserves_empty_and_unicode_text_on_zero_exit(self):
        for text in ('', 'Ação própria 日本語 🙂'):
            with self.subTest(text=text):
                result = self.call('bench_clipboard_get', {'returncode': 0, 'stdout': text})
                self.assertFalse(result.get('isError'))
                self.assertEqual({'text': text}, json.loads(result['content'][0]['text']))

    def test_write_success_preserves_shape_and_single_dispatch(self):
        result = self.call('bench_clipboard_set', {'returncode': 0, 'stdout': ''})
        self.assertFalse(result.get('isError'))
        self.assertEqual({'ok': True}, json.loads(result['content'][0]['text']))

    def test_nonzero_exit_is_error_without_returning_stale_clipboard(self):
        for tool in ('bench_clipboard_get', 'bench_clipboard_set'):
            for code in (1, -15):
                with self.subTest(tool=tool, code=code):
                    result = self.call(tool, {'returncode': code, 'stdout': 'private stale value',
                                              'stderr': 'fixture failure'})
                    self.assertTrue(result.get('isError'))
                    self.assertIn('CLIPBOARD_FAILED', result['content'][0]['text'])
                    self.assertNotIn('private stale value', str(result))

    def test_missing_or_invalid_exit_status_is_not_success(self):
        for tool in ('bench_clipboard_get', 'bench_clipboard_set'):
            for response in (None, [], {}, {'returncode': None}, {'returncode': False},
                             {'returncode': '0'}, {'returncode': 0.0}):
                with self.subTest(tool=tool, response=response):
                    result = self.call(tool, response)
                    self.assertTrue(result.get('isError'))
                    self.assertIn('CLIPBOARD_RESULT_INVALID', result['content'][0]['text'])

    def test_invalid_read_output_does_not_become_empty_clipboard(self):
        for response in ({'returncode': 0}, {'returncode': 0, 'stdout': None},
                         {'returncode': 0, 'stdout': []}):
            with self.subTest(response=response):
                result = self.call('bench_clipboard_get', response)
                self.assertTrue(result.get('isError'))
                self.assertIn('CLIPBOARD_RESULT_INVALID', result['content'][0]['text'])


if __name__ == '__main__':
    unittest.main()
