import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import bench_catalog as catalog


def document():
    return {'version': 'fixture-1', 'tools': [{
        'name': 'fixture_action', 'description': 'Descrição Unicode: ação.',
        'input_schema': {'type': 'object', 'additionalProperties': False,
                         'properties': {'pid': {'type': 'integer', 'format': 'uint32'},
                                        'mode': {'enum': ['click', None]}},
                         'required': ['pid']},
        'read_only': False, 'destructive': True, 'idempotent': False,
    }]}


class CatalogNormalization(unittest.TestCase):
    def test_preserves_schema_description_and_false_annotations(self):
        source = document()
        original = copy.deepcopy(source)
        tool = catalog.normalize_tools(source)[0]
        self.assertEqual(tool['inputSchema'], original['tools'][0]['input_schema'])
        self.assertEqual(tool['description'], original['tools'][0]['description'])
        self.assertEqual(tool['annotations'], {'readOnlyHint': False,
                         'destructiveHint': True, 'idempotentHint': False})
        self.assertEqual(set(tool), {'name', 'description', 'inputSchema', 'annotations'})
        tool['inputSchema']['properties']['pid']['type'] = 'string'
        self.assertEqual(source, original)

    def test_missing_annotations_remain_absent(self):
        source = document()
        for name in catalog.ANNOTATIONS:
            del source['tools'][0][name]
        self.assertNotIn('annotations', catalog.normalize_tools(source)[0])
        source['tools'][0]['read_only'] = True
        self.assertEqual(catalog.normalize_tools(source)[0]['annotations'], {'readOnlyHint': True})

    def test_filtering_and_bench_injection_remain_callsite_responsibility(self):
        source = document()
        source['tools'][0]['name'] = 'replay_trajectory'
        tools = catalog.normalize_tools(source)
        self.assertEqual(tools[0]['name'], 'replay_trajectory')
        self.assertNotIn('bench', tools[0]['inputSchema']['properties'])

    def test_rejects_invalid_envelope(self):
        for source in (None, [], {}, {'tools': {}}, {'tools': []}, {'tools': [None]}):
            with self.subTest(source=source), self.assertRaises(catalog.CatalogError) as caught:
                catalog.normalize_tools(source)
            self.assertEqual(caught.exception.code, 'catalog_invalid')

    def test_rejects_duplicate_tool_names(self):
        source = document()
        source['tools'].append(copy.deepcopy(source['tools'][0]))
        with self.assertRaises(catalog.CatalogError):
            catalog.normalize_tools(source)

    def test_rejects_bad_required_fields_and_non_boolean_annotations(self):
        for field, value in [('name', ''), ('name', 1), ('description', None),
                             ('input_schema', None), ('input_schema', {'type': 'array'}),
                             ('read_only', 'false'), ('destructive', 0), ('idempotent', None)]:
            source = document()
            source['tools'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(catalog.CatalogError):
                catalog.normalize_tools(source)
        for field in ('name', 'description', 'input_schema'):
            source = document()
            del source['tools'][0][field]
            with self.subTest(missing=field), self.assertRaises(catalog.CatalogError):
                catalog.normalize_tools(source)


class CatalogCommand(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bench-catalog-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / 'cua-driver'
        self.calls = self.root / 'calls.jsonl'
        self.pid = self.root / 'pid'
        finder = patch.object(catalog.shutil, 'which', return_value=str(self.binary))
        self.finder = finder.start()
        self.addCleanup(finder.stop)

    def fake(self, body):
        self.binary.write_text(
            f'#!{sys.executable}\n'
            'import json, os, pathlib, sys, time\n'
            f'with pathlib.Path({str(self.calls)!r}).open("a") as f:\n'
            '    f.write(json.dumps(sys.argv[1:]) + "\\n")\n'
            'if sys.argv[1:] != ["dump-docs", "--type", "mcp"]:\n'
            '    sys.exit(99)\n' + body, encoding='utf-8')
        self.binary.chmod(0o700)

    def assert_code(self, code, **kwargs):
        with self.assertRaises(catalog.CatalogError) as caught:
            catalog.load_cua_tools(**kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_only_static_command_is_called_and_stdin_is_closed(self):
        self.fake('assert sys.stdin.read() == ""\n' + f'print({json.dumps(document())!r})\n')
        self.assertEqual(catalog.load_cua_tools(), catalog.normalize_tools(document()))
        self.finder.assert_called_once_with('cua-driver')
        self.assertEqual(self.calls.read_text().splitlines(), ['["dump-docs", "--type", "mcp"]'])

    def test_binary_changes_are_observed_without_stale_cache(self):
        self.fake(f'print({json.dumps(document())!r})\n')
        self.assertEqual(catalog.load_cua_tools()[0]['name'], 'fixture_action')
        replacement = document()
        replacement['tools'][0]['name'] = 'replacement_action'
        self.fake(f'print({json.dumps(replacement)!r})\n')
        self.assertEqual(catalog.load_cua_tools()[0]['name'], 'replacement_action')
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_missing_binary_fails_without_subprocess_or_fallback(self):
        self.finder.return_value = None
        with patch.object(catalog.subprocess, 'run') as run:
            self.assert_code('catalog_unavailable')
        run.assert_not_called()
        self.assertFalse(self.calls.exists())

    def test_missing_or_non_executable_resolved_binary_fails(self):
        self.assert_code('catalog_unavailable')
        self.fake('print("{}")\n')
        self.binary.chmod(0o600)
        self.assert_code('catalog_unavailable')
        self.assertFalse(self.calls.exists())

    def test_nonzero_exit_is_explicit_without_echoing_stderr_or_retry(self):
        self.fake('sys.stderr.write("fixture-secret-value")\nsys.exit(17)\n')
        error = self.assert_code('catalog_failed')
        self.assertIn('17', str(error))
        self.assertNotIn('fixture-secret-value', str(error))
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_invalid_output_is_explicit_without_retry(self):
        for body in ('print("not-json")\n', 'print("{}")\n',
                     'sys.stdout.buffer.write(b"\\xff")\n'):
            with self.subTest(body=body):
                self.fake(body)
                self.assert_code('catalog_invalid')
        self.assertEqual(len(self.calls.read_text().splitlines()), 3)

    def test_timeout_kills_and_reaps_dump_process_without_retry(self):
        self.fake(f'pathlib.Path({str(self.pid)!r}).write_text(str(os.getpid()))\n'
                  'time.sleep(30)\n')
        start = time.monotonic()
        self.assert_code('catalog_timeout', timeout=0.3)
        self.assertLess(time.monotonic() - start, 2)
        pid = int(self.pid.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_invalid_deadlines_fail_before_executable_discovery(self):
        for timeout in (0, -1, float('inf'), float('nan'), True, '5', None):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                catalog.load_cua_tools(timeout=timeout)
        self.finder.assert_not_called()

    def test_module_import_does_not_discover_or_start_processes(self):
        with patch.object(catalog.subprocess, 'run') as run:
            importlib.reload(catalog)
        run.assert_not_called()
        self.finder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
