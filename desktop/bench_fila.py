"""Fila de trabalho pesado nas bancadas: render 3D/WebGL, vídeo, captura longa.

Só VAGAS bancadas seguram vaga pesada ao mesmo tempo, na ordem de chegada.
As demais seguem funcionando com CPU limitada pelo drop-in 10-cota.conf do
agent-bench@.service; quem ganha a vaga tem o limite tirado em runtime.
Vaga vence sem renovação (TTL) ou quando a bancada some, e a próxima entra.
"""
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from bench_ops import RUNTIME, valid

FILE = Path.home() / '.local/state/agent-bench/fila.json'
VAGAS = int(os.environ.get('AGENT_BENCH_VAGAS_PESADAS', '1'))
TTL = int(os.environ.get('AGENT_BENCH_VAGA_TTL', str(20 * 60)))
ESPERA_TTL = 90


@contextmanager
def _estado():
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FILE.with_suffix('.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try: st = json.loads(FILE.read_text())
        except (OSError, ValueError): st = {}
        st.setdefault('vagas', []); st.setdefault('espera', [])
        yield st
        FILE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


def _viva(bench):
    return (RUNTIME / bench / 'control.sock').exists()


def _cota(bench, pesada):
    # Vazio desfaz o limite; o drop-in runtime 50-CPUQuota.conf vence o 10-cota.conf.
    subprocess.run(['systemctl', '--user', 'set-property', '--runtime', f'agent-bench@{bench}.service',
                    'CPUQuota=' if pesada else 'CPUQuota=150%'], capture_output=True)


def _limpar(st, agora):
    for v in list(st['vagas']):
        if agora - v['renovado'] > TTL or not _viva(v['bench']):
            st['vagas'].remove(v); _cota(v['bench'], False)
    st['espera'] = [e for e in st['espera'] if agora - e['visto'] <= ESPERA_TTL and _viva(e['bench'])]


def entrar(bench, motivo='', timeout=None):
    """Bloqueia até a bancada ganhar vaga pesada. Chamar de novo renova a vaga."""
    bench, inicio, ultima = valid(bench), time.time(), None
    while True:
        agora = time.time()
        with _estado() as st:
            _limpar(st, agora)
            atual = next((v for v in st['vagas'] if v['bench'] == bench), None)
            if atual:
                atual['renovado'] = agora
                if motivo: atual['motivo'] = motivo
                _cota(bench, True)
                return {'status': 'com vaga', **atual, 'expira_em_s': TTL}
            fila = [e['bench'] for e in st['espera']]
            if len(st['vagas']) < VAGAS and (not fila or fila[0] == bench):
                st['espera'] = [e for e in st['espera'] if e['bench'] != bench]
                vaga = {'bench': bench, 'motivo': motivo, 'desde': agora, 'renovado': agora}
                st['vagas'].append(vaga); _cota(bench, True)
                return {'status': 'com vaga', **vaga, 'expira_em_s': TTL}
            item = next((e for e in st['espera'] if e['bench'] == bench), None)
            if item: item['visto'] = agora
            else: st['espera'].append({'bench': bench, 'motivo': motivo, 'desde': agora, 'visto': agora})
            posicao = [e['bench'] for e in st['espera']].index(bench) + 1
            ocupada = ', '.join(f"{v['bench']} ({v['motivo'] or 'sem motivo'})" for v in st['vagas'])
        if posicao != ultima:
            print(f'agent-bench fila: {bench} esperando, posição {posicao}; vaga com {ocupada}', file=sys.stderr, flush=True)
            ultima = posicao
        if timeout is not None and agora - inicio >= timeout:
            return {'status': 'esperando', 'posicao': posicao, 'vaga_com': ocupada}
        time.sleep(5)


def sair(bench):
    bench = valid(bench)
    with _estado() as st:
        tinha = any(v['bench'] == bench for v in st['vagas'])
        st['vagas'] = [v for v in st['vagas'] if v['bench'] != bench]
        st['espera'] = [e for e in st['espera'] if e['bench'] != bench]
    _cota(bench, False)
    return {'status': 'liberada' if tinha else 'não tinha vaga', 'bench': bench}


def status():
    with _estado() as st:
        _limpar(st, time.time())
        return {'vagas_pesadas': VAGAS, 'ttl_s': TTL, 'vagas': st['vagas'], 'espera': st['espera']}


def rodar(bench, argv, motivo=''):
    """Segura a vaga só enquanto o comando roda."""
    motivo = motivo or ' '.join(argv)[:80]
    entrar(bench, motivo)
    try:
        proc = subprocess.Popen(argv)
        while True:
            try: return proc.wait(timeout=60)
            except subprocess.TimeoutExpired: entrar(bench, motivo)
    finally: sair(bench)
