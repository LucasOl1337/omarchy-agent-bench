import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bench_ops as ops
import bench_views as views


class BarState(unittest.TestCase):
    def test_owner_activity_and_kind(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime, state = root / 'run', root / 'state'
            for name in ('rascunho', 'cofre', 'antiga'):
                (runtime / name).mkdir(parents=True)
            (runtime / 'rascunho/owner.json').write_text(json.dumps({'owner': 'claude', 'last_actor': 'codex'}))
            (runtime / 'rascunho/last_activity').write_text('1000\n')
            (runtime / 'cofre/last_activity').write_text('700\n')
            (runtime / 'cofre/human-control').write_text('')
            (state / 'rascunho/chromium').mkdir(parents=True)
            (state / 'antiga/chromium/Default').mkdir(parents=True)
            (state / 'rascunho/chromium' / ops.EPHEMERAL_MARK).write_text('')
            entries = {'rascunho': {'workspace': 7}, 'cofre': {'workspace': 8}, 'antiga': {'workspace': 9}}
            with patch.object(views, 'RUNTIME', runtime), patch.object(ops, 'STATE', state), \
                 patch.dict(views.entries, entries, clear=True):
                bar = views.bar_state(now=1030)
        self.assertEqual(bar['rascunho'], {'workspace': 7, 'owner': 'codex', 'active': True,
                                           'idle_seconds': 30, 'human': False, 'kind': 'descartavel'})
        self.assertEqual(bar['cofre']['kind'], 'cofre')
        self.assertTrue(bar['cofre']['human'])
        self.assertEqual(bar['cofre']['idle_seconds'], 330)
        self.assertFalse(bar['cofre']['active'])
        self.assertEqual(bar['antiga'], {'workspace': 9, 'owner': 'unknown', 'active': False,
                                         'idle_seconds': None, 'human': False, 'kind': 'persistente'})


if __name__ == '__main__':
    unittest.main()
