import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bench_ops as ops


class OwnerMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for key, value in [('STATE', self.root / 'state'), ('RUNTIME', self.root / 'runtime')]:
            patcher = patch.object(ops, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_second_actor_does_not_replace_original_label(self):
        first = ops.claim_owner('test', 'agent-a')
        second = ops.claim_owner('test', 'agent-b')
        self.assertEqual(second['owner'], 'agent-a')
        self.assertEqual(second['claimed_at'], first['claimed_at'])
        self.assertEqual(second['last_actor'], 'agent-b')
        self.assertEqual(second['owner_origin'], 'first_observed_actor')
        self.assertNotIn('created_by', second)
        self.assertEqual(ops.load_owner('test'), second)

    def test_legacy_label_is_preserved_without_fabricated_creator(self):
        runtime, state = ops.paths('test')
        runtime.mkdir(parents=True)
        (runtime / 'owner.json').write_text(json.dumps({'owner': 'legacy-last-writer', 'claimed_at': 123}))
        new = ops.claim_owner('test', 'next-reader')
        self.assertEqual(new['owner'], 'legacy-last-writer')
        self.assertEqual(new['owner_origin'], 'legacy_label')
        self.assertEqual(new['claimed_at'], 123)
        self.assertEqual(new['last_actor'], 'next-reader')

    def test_runtime_loss_or_old_mirror_does_not_change_stable_label(self):
        ops.claim_owner('test', 'first')
        runtime, state = ops.paths('test')
        (runtime / 'owner.json').write_text('{"owner":"old-client"}')
        self.assertEqual(ops.load_owner('test')['owner'], 'first')
        (runtime / 'owner.json').unlink()
        self.assertEqual(ops.claim_owner('test', 'after-reboot')['owner'], 'first')
        self.assertEqual(json.loads((runtime / 'owner.json').read_text()), ops.load_owner('test'))

    def test_two_first_actors_keep_one_stable_label_and_complete_json(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda actor: ops.claim_owner('test', actor), ['one', 'two']))
        self.assertEqual(len({result['owner'] for result in results}), 1)
        current = ops.load_owner('test')
        self.assertIn(current['owner'], ('one', 'two'))
        for folder in ops.paths('test'):
            path = folder / 'owner.json'
            self.assertEqual(json.loads(path.read_text()), current)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(folder.glob('owner.*.tmp')), [])

    def test_running_start_records_last_actor_without_transfer_or_restart(self):
        ops.claim_owner('test', 'first')
        with patch.object(ops, 'request', return_value={'name': 'test'}), patch.object(ops.subprocess, 'run') as run:
            self.assertEqual(ops.start('test', 'reader'), {'name': 'test'})
            run.assert_not_called()
        self.assertEqual(ops.load_owner('test')['owner'], 'first')
        self.assertEqual(ops.load_owner('test')['last_actor'], 'reader')

    def test_new_start_records_requester_before_boot_fallback(self):
        def start_unit(*args, **kwargs):
            self.assertEqual(ops.load_owner('test')['owner'], 'requester')
        with patch.object(ops, 'request', side_effect=[OSError(), {'name': 'test'}]), \
             patch.object(ops, 'systemd_env', return_value={}), \
             patch.object(ops.subprocess, 'run', side_effect=start_unit):
            ops.start('test', 'requester')

    def test_invalid_json_shape_is_not_an_owner(self):
        runtime, state = ops.paths('test')
        state.mkdir(parents=True)
        for value in ('[]', 'null', '{"owner":3}', '{'):
            (state / 'owner.json').write_text(value)
            self.assertEqual(ops.load_owner('test'), {})


if __name__ == '__main__':
    unittest.main()
