import io
import json
import os
import subprocess
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
                              # VRAM e jogo do humano reais da máquina não entram no teste (com a GPU cheia ele falhava).
                              ('_vram_livre', lambda: None), ('_jogando', lambda: []),
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
        with patch.object(fila.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run, \
                patch.object(fila, 'COTA_FORA_DA_FILA', '300%'), patch.object(fila, 'systemd_env', lambda: {}):
            self.cota_real('a', False)
        self.assertIn('CPUQuota=300%', run.call_args[0][0])


class RuntimeDoUsuarioTests(unittest.TestCase):
    """O jcode exporta XDG_RUNTIME_DIR=/run/user/UID/jcode-...: a fila precisa achar a marca do jogo
    e o bus do systemd em /run/user/UID mesmo assim, e não pode fingir vaga quando a cota não sai (21/10/2026)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.user = root / 'run/user/1000'
        (self.user / 'humano-jogando').mkdir(parents=True)
        self.jcode = self.user / 'jcode-dino-20261021'
        self.jcode.mkdir()
        self.env = {}
        for target, value in [('FILE', root / 'fila.json'), ('_viva', lambda bench: True),
                              ('_vram_livre', lambda: 12000), ('VAGAS', 2),
                              ('JOGANDO', self.user / 'humano-jogando'),
                              ('systemd_env', lambda: {'XDG_RUNTIME_DIR': str(self.user),
                                                       'DBUS_SESSION_BUS_ADDRESS': f'unix:path={self.user}/bus'})]:
            patcher = patch.object(fila, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def marca(self, pasta):
        (pasta / 'dino-unreal').write_text(f'{os.getpid()}\n')

    def test_game_mark_in_user_runtime_is_seen_from_jcode_xdg(self):
        self.marca(self.user / 'humano-jogando')
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(self.jcode)}):
            self.assertEqual(['dino-unreal'], fila._jogando())

    def test_game_mark_with_normal_xdg(self):
        self.marca(self.user / 'humano-jogando')
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(self.user)}):
            self.assertEqual(['dino-unreal'], fila._jogando())

    def test_old_launcher_mark_in_caller_xdg_still_counts(self):
        (self.jcode / 'humano-jogando').mkdir()
        self.marca(self.jcode / 'humano-jogando')
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(self.jcode)}):
            self.assertEqual(['dino-unreal'], fila._jogando())

    def test_systemctl_gets_the_user_bus_not_the_caller_xdg(self):
        ok = subprocess.CompletedProcess([], 0, '', '')
        with patch.dict(os.environ, {'XDG_RUNTIME_DIR': str(self.jcode)}), \
                patch.object(fila.subprocess, 'run', return_value=ok) as run:
            self.assertEqual('com vaga', fila.entrar('a', 'unreal', timeout=0)['status'])
        env = run.call_args.kwargs['env']
        self.assertEqual(str(self.user), env['XDG_RUNTIME_DIR'])
        self.assertEqual(f'unix:path={self.user}/bus', env['DBUS_SESSION_BUS_ADDRESS'])
        self.assertIn('CPUQuota=', run.call_args.args[0])

    def test_failed_quota_removal_refuses_the_slot(self):
        falha = subprocess.CompletedProcess([], 1, '', 'Failed to connect to user scope bus')
        with patch.object(fila.subprocess, 'run', return_value=falha):
            with self.assertRaisesRegex(fila.CotaErro, 'Failed to connect to user scope bus'):
                fila.entrar('a', 'unreal', timeout=0)
        self.assertEqual([], json.loads(fila.FILE.read_text())['vagas'])

    def test_failed_quota_restore_warns_but_releases(self):
        ok = subprocess.CompletedProcess([], 0, '', '')
        falha = subprocess.CompletedProcess([], 1, '', 'Failed to connect to user scope bus')
        with patch.object(fila.subprocess, 'run', return_value=ok):
            fila.entrar('a', 'unreal', timeout=0)
        with patch.object(fila.subprocess, 'run', return_value=falha), patch('sys.stderr', new=io.StringIO()) as err:
            self.assertEqual('liberada', fila.sair('a')['status'])
        self.assertIn('sem teto de CPU', err.getvalue())


if __name__ == '__main__':
    unittest.main()
