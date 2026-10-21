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


class DuasVagasTests(unittest.TestCase):
    """Second slot only with free VRAM, after the previous slot had time to load, and never
    while a human game is marked; the promoter hands one slot per call."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.quota, self.now, self.vram, self.jogos = [], 1000.0, 12000, []
        self.cota_real = fila._cota
        for target, value in [('FILE', Path(self.temp.name) / 'fila.json'),
                              ('_viva', lambda bench: True),
                              ('_cota', lambda bench, pesada: self.quota.append((bench, pesada))),
                              ('_vram_livre', lambda: self.vram),
                              ('_jogando', lambda: list(self.jogos)),
                              ('VAGAS', 2), ('VRAM_LIVRE_MIN', 9000), ('SUBIDA_S', 150)]:
            patcher = patch.object(fila, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        clock = patch.object(fila.time, 'time', lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def espera(self, bench):
        with patch('sys.stderr'):
            return fila.entrar(bench, 'unreal', timeout=0)

    def test_second_slot_waits_for_the_first_unreal_to_load(self):
        self.assertEqual('com vaga', self.espera('a')['status'])
        self.assertEqual('esperando', self.espera('b')['status'])
        self.now += 151
        self.assertEqual('com vaga', self.espera('b')['status'])
        self.assertEqual(['a', 'b'], [v['bench'] for v in fila.status()['vagas']])

    def test_second_slot_needs_free_vram(self):
        self.espera('a'); self.now += 151
        self.vram = 4000
        self.assertEqual('esperando', self.espera('b')['status'])
        self.vram = 9000
        self.assertEqual('com vaga', self.espera('b')['status'])

    def test_unknown_vram_does_not_block(self):
        self.vram = None
        self.assertEqual('com vaga', self.espera('a')['status'])

    def test_human_game_blocks_any_new_slot(self):
        self.jogos = ['dino']
        got = self.espera('a')
        self.assertEqual('esperando', got['status'])
        self.assertIn('humano jogando dino', got['vaga_com'])
        self.jogos = []
        self.assertEqual('com vaga', self.espera('a')['status'])

    def test_status_promotes_one_waiter_per_call(self):
        self.espera('a'); self.now += 151
        self.vram = 4000
        self.espera('b'); self.espera('c')
        self.vram = 12000
        st = fila.status()
        self.assertEqual(['a', 'b'], [v['bench'] for v in st['vagas']])
        self.assertEqual(['c'], [e['bench'] for e in st['espera']])
        self.assertEqual(('b', True), self.quota[-1])

    def test_leaving_restores_the_three_core_cap(self):
        with patch.object(fila.subprocess, 'run') as run, patch.object(fila, 'COTA_FORA_DA_FILA', '300%'):
            self.cota_real('a', False)
        self.assertIn('CPUQuota=300%', run.call_args[0][0])


if __name__ == '__main__':
    unittest.main()
