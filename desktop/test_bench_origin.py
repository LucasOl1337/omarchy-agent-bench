import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bench_ops as ops


class OriginTests(unittest.TestCase):
    """Name the harness, project and launcher behind a bench client."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / 'proc'
        for key, value in [('PROC', self.proc), ('STATE', self.root / 'state'), ('RUNTIME', self.root / 'runtime')]:
            patcher = patch.object(ops, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake(self, pid, parent, args, cwd):
        folder = self.proc / str(pid)
        folder.mkdir(parents=True)
        (folder / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in args) + b'\0')
        (folder / 'stat').write_text(f'{pid} (x) S {parent} 0 0')
        os.symlink(cwd, folder / 'cwd')

    def test_mcp_under_codex_in_herdr(self):
        self.fake(900, 1, ['/usr/bin/herdr', 'server'], '/home/u')
        self.fake(901, 900, ['/usr/bin/bash'], '/home/u/nexsales')
        self.fake(902, 901, ['/opt/codex/bin/codex'], '/home/u/nexsales')
        self.fake(903, 902, ['python3', '/home/u/.agents/bin/agent-bench-mcp'], '/home/u/nexsales')
        origin = ops.describe_origin(903)
        self.assertEqual((origin['client'], origin['harness'], origin['harness_pid'], origin['project'], origin['via']),
                         ('agent-bench-mcp', 'codex', 902, '/home/u/nexsales', 'herdr'))

    def test_shell_inside_app_folder_is_not_dailywork(self):
        self.fake(800, 1, ['/usr/bin/kitty'], '/home/u')
        self.fake(801, 800, ['/opt/claude/claude'], '/home/u/DailyWork')
        self.fake(802, 801, ['/usr/bin/bash', '-c', 'agent-bench exec x'], '/home/u/DailyWork/Daily Work app')
        self.fake(803, 802, ['python3', '/home/u/.agents/bin/agent-bench', 'exec'], '/home/u/DailyWork/Daily Work app')
        origin = ops.describe_origin(803)
        self.assertEqual((origin['client'], origin['harness'], origin['via']), ('agent-bench', 'claude', 'terminal'))

    def test_detached_skill_run_is_dailywork(self):
        run = '/home/u/LucasOL/Private/skills/execucoes/sk-20260924-133317-ce86'
        self.fake(700, 1, ['/usr/bin/sh', f'{run}/rodar.sh'], run)
        self.fake(701, 700, ['/opt/claude/claude', '-p'], run)
        self.fake(702, 701, ['python3', '/home/u/.agents/bin/agent-bench', 'cdp'], run)
        origin = ops.describe_origin(702)
        self.assertEqual((origin['harness'], origin['via'], origin['skill_run']), ('claude', 'dailywork', 'sk-20260924-133317-ce86'))

    def test_packaged_dailywork_is_the_harness(self):
        self.fake(500, 1, ['/opt/DailyWork/Daily Work app', '--background'], '/home/u')
        self.fake(501, 500, ['/opt/DailyWork/Daily Work app', '/app/electron/candidaturas/worker-nativo.cjs'], '/home/u/DailyWork')
        origin = ops.describe_origin(501)
        self.assertEqual((origin['harness'], origin['harness_pid'], origin['via']), ('dailywork', 501, 'dailywork'))

    def test_gone_process_has_no_origin(self):
        self.assertIsNone(ops.describe_origin(4242))

    def test_record_keeps_one_row_per_harness_newest_first(self):
        self.fake(600, 1, ['/opt/codex/bin/codex'], '/home/u/a')
        self.fake(601, 600, ['python3', '/home/u/.agents/bin/agent-bench'], '/home/u/a')
        self.fake(610, 1, ['/opt/claude/claude'], '/home/u/b')
        self.fake(611, 610, ['python3', '/home/u/.agents/bin/agent-bench'], '/home/u/b')
        ops.record_origin('teste', 601, 'exec')
        ops.record_origin('teste', 611, 'screenshot')
        ops.record_origin('teste', 601, 'launch')
        runtime, _ = ops.paths('teste')
        items = json.loads((runtime / 'origins.json').read_text())
        self.assertEqual([(i['harness'], i['count'], i['last_action']) for i in items],
                         [('codex', 2, 'launch'), ('claude', 1, 'screenshot')])


if __name__ == '__main__':
    unittest.main()
