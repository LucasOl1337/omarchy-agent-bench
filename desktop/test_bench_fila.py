import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bench_fila as fila


class FilaTests(unittest.TestCase):
    """One heavy slot at a time, FIFO, released on TTL or when the bench is gone."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.alive = {'a', 'b'}
        self.quota = []
        self.now = 1000.0
        for target, value in [('FILE', Path(self.temp.name) / 'fila.json'),
                              ('_viva', lambda bench: bench in self.alive),
                              ('_cota', lambda bench, pesada: self.quota.append((bench, pesada))),
                              ('VAGAS', 1)]:
            patcher = patch.object(fila, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        clock = patch.object(fila.time, 'time', lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def test_first_bench_takes_the_slot_and_loses_the_quota(self):
        got = fila.entrar('a', 'render', timeout=0)
        self.assertEqual('com vaga', got['status'])
        self.assertEqual(('a', True), self.quota[-1])

    def test_second_bench_waits_in_line(self):
        fila.entrar('a', 'render', timeout=0)
        with patch('sys.stderr'):
            got = fila.entrar('b', 'video', timeout=0)
        self.assertEqual(('esperando', 1), (got['status'], got['posicao']))
        self.assertEqual(['b'], [e['bench'] for e in fila.status()['espera']])

    def test_leaving_hands_the_slot_to_the_next(self):
        fila.entrar('a', timeout=0)
        with patch('sys.stderr'):
            fila.entrar('b', timeout=0)
        self.assertEqual('liberada', fila.sair('a')['status'])
        self.assertEqual(('a', False), self.quota[-1])
        self.assertEqual('com vaga', fila.entrar('b', timeout=0)['status'])

    def test_expired_or_closed_slot_is_released(self):
        fila.entrar('a', timeout=0)
        self.now += fila.TTL + 1
        self.assertEqual([], fila.status()['vagas'])
        self.assertIn(('a', False), self.quota)
        fila.entrar('a', timeout=0)
        self.alive.discard('a')
        self.assertEqual([], fila.status()['vagas'])

    def test_state_file_is_json(self):
        fila.entrar('a', 'render', timeout=0)
        state = json.loads(fila.FILE.read_text())
        self.assertEqual('a', state['vagas'][0]['bench'])


if __name__ == '__main__':
    unittest.main()
