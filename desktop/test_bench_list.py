"""Read-only bench discovery; no services, metadata writes or process launches."""
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bench_control
import bench_hub_mcp as hub
import bench_ops as ops


class BenchListTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.runtime, self.state = self.root / 'runtime', self.root / 'state'
        self.status = {'name': 'fixture', 'display': ':88', 'geometry': '1600x1000',
                       'control_mode': 'humano'}
        self.metadata = {'metadata_version': 2, 'owner': 'unknown', 'owner_origin': 'legacy_label',
                         'claimed_at': 100, 'last_actor': 'claude', 'last_seen_at': 200.5}
        self.add_bench('fixture', self.metadata)
        for module, key, value in ((ops, 'RUNTIME', self.runtime), (ops, 'STATE', self.state),
                                   (hub, 'RUNTIME', self.runtime)):
            monkey = patch.object(module, key, value)
            monkey.start()
            self.addCleanup(monkey.stop)
        self.request = self.mock(hub, 'request', return_value=self.status)
        self.rpc = self.mock(hub, 'rpc', return_value={'workspace': 9, 'viewer_pid': 123})
        self.forbidden = [self.mock(module, name, side_effect=AssertionError('unexpected ' + name))
                          for module, name in ((hub, 'ensure'), (ops, 'claim_owner'),
                                               (ops, 'touch_activity'), (hub, 'load_cua_tools'),
                                               (hub, 'launch_cua'), (bench_control, 'views'))]
        self.mock(ops.subprocess, 'Popen', side_effect=AssertionError('unexpected process spawn'))

    def mock(self, module, name, **kwargs):
        monkey = patch.object(module, name, **kwargs)
        result = monkey.start()
        self.addCleanup(monkey.stop)
        return result

    def add_bench(self, name, metadata=None):
        runtime, state = self.runtime / name, self.state / name
        runtime.mkdir(parents=True, exist_ok=True)
        state.mkdir(parents=True, exist_ok=True)
        (runtime / 'control.sock').touch()
        if metadata is not None:
            for folder in (runtime, state):
                (folder / 'owner.json').write_text(json.dumps(metadata))

    def snapshot(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob('*') if path.is_file()}

    def listed(self):
        before = self.snapshot()
        client = hub.Hub(['bench_list'])
        tools = client.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        self.assertEqual(['bench_list'], [tool['name'] for tool in tools['result']['tools']])
        reply = client.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                               'params': {'name': 'bench_list', 'arguments': {}}})
        self.assertFalse(reply['result'].get('isError'), reply)
        self.assertEqual(before, self.snapshot())
        for mock in self.forbidden:
            mock.assert_not_called()
        return json.loads(reply['result']['content'][0]['text'])

    def test_workspace_and_actor_context_are_additive_and_read_only(self):
        row, = self.listed()
        self.assertEqual({**self.status, 'owner': 'unknown', 'workspace': 9,
                          'metadata_version': 2, 'owner_origin': 'legacy_label',
                          'last_actor': 'claude', 'last_seen_at': 200.5}, row)
        self.request.assert_called_once_with('fixture', {'action': 'status'}, timeout=1)
        self.rpc.assert_called_once_with(self.runtime / 'views.sock',
                                        {'action': 'status', 'name': 'fixture'}, timeout=1)
        self.assertNotIn('viewer_pid', row)
        self.assertNotIn('claimed_at', row)

    def test_supervisor_unknown_json_or_workspace_type_does_not_drop_bench(self):
        for value in (None, [], True, False, 9, '9', {}, {'workspace': None},
                      {'workspace': True}, {'workspace': '9'}, {'workspace': 9.0},
                      {'workspace': []}, {'workspace': {}}, {'workspace': math.inf},
                      {'workspace': math.nan}):
            with self.subTest(value=value):
                self.rpc.return_value = value
                row, = self.listed()
                self.assertIsNone(row['workspace'])
                self.assertEqual('humano', row['control_mode'])

    def test_supervisor_errors_keep_bench_without_recovery(self):
        for error in (OSError('missing'), TimeoutError('late'), RuntimeError('rpc error'),
                      ValueError('bad JSON'), TypeError('unexpected JSON in RPC decoder')):
            with self.subTest(error=error):
                self.rpc.side_effect = error
                row, = self.listed()
                self.assertIsNone(row['workspace'])

    def test_integer_assignment_is_diagnostic_even_outside_reserved_range(self):
        self.rpc.return_value = {'workspace': 12}
        row, = self.listed()
        self.assertEqual(12, row['workspace'])

    def test_v2_persistent_metadata_wins_without_repairing_old_mirror(self):
        (self.runtime / 'fixture' / 'owner.json').write_text(json.dumps({'owner': 'old-client'}))
        row, = self.listed()
        self.assertEqual('unknown', row['owner'])
        self.assertEqual('claude', row['last_actor'])

    def test_legacy_metadata_does_not_invent_new_actor_fields(self):
        self.add_bench('fixture', {'owner': 'unknown', 'claimed_at': 100})
        row, = self.listed()
        self.assertEqual('unknown', row['owner'])
        for key in ('metadata_version', 'owner_origin', 'last_actor', 'last_seen_at'):
            self.assertIsNone(row[key])

    def test_absent_and_invalid_owner_records_stay_unknown_without_claim(self):
        paths = [folder / 'fixture' / 'owner.json' for folder in (self.runtime, self.state)]
        for value in ('absent', None, [], True, {'owner': 3}, {'owner': ''}):
            with self.subTest(value=value):
                for path in paths:
                    if value == 'absent':
                        path.unlink(missing_ok=True)
                    else:
                        path.write_text(json.dumps(value))
                row, = self.listed()
                for key in ('owner', 'metadata_version', 'owner_origin', 'last_actor', 'last_seen_at'):
                    self.assertIsNone(row[key])

    def test_invalid_actor_metadata_types_and_nonfinite_numbers_become_null(self):
        bad = {
            'metadata_version': (True, '2', 2.0, 1, 3, [], {}, math.inf, math.nan),
            'owner_origin': (True, 2, [], {}, '', 'invented_creator'),
            'last_actor': (True, 2, [], {}, '', '   ', math.nan),
            'last_seen_at': (True, '200', [], {}, -1, math.inf, -math.inf, math.nan),
        }
        for key, values in bad.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.add_bench('fixture', {**self.metadata, key: value})
                    row, = self.listed()
                    self.assertEqual('unknown', row['owner'])
                    self.assertIsNone(row[key])
                    json.dumps(row, allow_nan=False)

    def test_valid_actor_values_are_not_relabelled_or_rounded(self):
        for actor, origin, timestamp in [('unknown', 'legacy_label', 0),
                                         ('systemd', 'first_observed_actor', 200.25),
                                         ('custom harness', 'first_observed_actor', 10 ** 400)]:
            with self.subTest(actor=actor):
                self.add_bench('fixture', {**self.metadata, 'owner_origin': origin,
                                         'last_actor': actor, 'last_seen_at': timestamp})
                row, = self.listed()
                self.assertEqual(actor, row['last_actor'])
                self.assertEqual(origin, row['owner_origin'])
                self.assertEqual(timestamp, row['last_seen_at'])

    def test_unavailable_or_malformed_bench_status_is_omitted(self):
        self.add_bench('neighbor', self.metadata)
        good = {**self.status, 'name': 'neighbor'}
        for bad in (None, [], True, False, OSError('missing'), TypeError('malformed RPC')):
            def status(name, payload, timeout):
                if name == 'neighbor':
                    return good
                if isinstance(bad, Exception):
                    raise bad
                return bad
            with self.subTest(bad=bad):
                self.request.side_effect = status
                self.rpc.reset_mock()
                rows = self.listed()
                self.assertEqual(['neighbor'], [row['name'] for row in rows])
                self.rpc.assert_called_once_with(self.runtime / 'views.sock',
                                                {'action': 'status', 'name': 'neighbor'}, timeout=1)


if __name__ == '__main__':
    unittest.main()
