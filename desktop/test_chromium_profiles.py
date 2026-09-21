import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bench_ops as ops


class PersistentProfiles(unittest.TestCase):
    def test_existing_default_profile_is_reused_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'bench'
            profile = state / 'chromium'
            (profile / 'Default').mkdir(parents=True)
            marker = profile / 'Default/fixture-auth'
            marker.write_text('renewed-session')
            self.assertEqual(ops.require_bench_profile(state), profile)
            self.assertEqual(marker.read_text(), 'renewed-session')

    def test_missing_profile_is_never_created_or_copied(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'bench'
            with self.assertRaisesRegex(RuntimeError, 'PERFIL_AUSENTE'):
                ops.require_bench_profile(state)
            self.assertFalse((state / 'chromium').exists())

    def test_profile_without_default_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'bench'
            (state / 'chromium').mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, 'PERFIL_AUSENTE'):
                ops.require_bench_profile(state)

    def test_isolated_flag_does_not_create_an_empty_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'bench'
            with patch.object(ops, 'control') as control:
                with self.assertRaisesRegex(RuntimeError, 'perfil vazio'):
                    ops.open_browser('test-bench', {'state': str(state)}, isolated=True)
            control.assert_called_once_with('test-bench')
            self.assertFalse((state / 'chromium').exists())


if __name__ == '__main__':
    unittest.main()
