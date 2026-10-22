"""Fila de trabalho pesado nas bancadas: render 3D/WebGL, vídeo, captura longa.

Até VAGAS bancadas seguram vaga pesada ao mesmo tempo, na ordem de chegada;
vaga além da primeira só entra com VRAM_LIVRE_MIN de VRAM sobrando.
As demais seguem funcionando com CPU limitada pelo drop-in 10-cota.conf do
agent-bench@.service; quem ganha a vaga tem o limite tirado em runtime.
Vaga vence sem renovação (TTL) ou quando a bancada some, e a próxima entra.
Jogo do humano aberto (marca com PID vivo em /run/user/UID/humano-jogando) segura
vaga nova: duas Unreal na mesma GPU esgotaram a VRAM e travaram o Dino dele em 16/10.
O runtime vem do UID, não do $XDG_RUNTIME_DIR do chamador: o jcode exporta uma subpasta
(/run/user/1000/jcode-...), onde não há marca nem bus do systemd (achado de 21/10/2026).
"""
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from bench_ops import RUNTIME, USER_RUNTIME_ROOT, systemd_env, valid

FILE = Path.home() / '.local/state/agent-bench/fila.json'
VAGAS = int(os.environ.get('AGENT_BENCH_VAGAS_PESADAS', '2'))
# Vaga extra só entra se sobrar VRAM: duas Unreal + o jogo do humano esgotaram 16 GB em 16/10.
VRAM_LIVRE_MIN = int(os.environ.get('AGENT_BENCH_VRAM_LIVRE_MIN', '9000'))
SUBIDA_S = int(os.environ.get('AGENT_BENCH_VAGA_SUBIDA_S', '150'))
TTL = int(os.environ.get('AGENT_BENCH_VAGA_TTL', str(20 * 60)))
ESPERA_TTL = 90
JOGANDO = USER_RUNTIME_ROOT / str(os.getuid()) / 'humano-jogando'


def _pastas_jogando():
    # Lançador antigo aberto de dentro de um harness grava no XDG dele: olha lá também.
    pastas = [JOGANDO]
    xdg = os.environ.get('XDG_RUNTIME_DIR')
    if xdg and Path(xdg) / 'humano-jogando' != JOGANDO: pastas.append(Path(xdg) / 'humano-jogando')
    return pastas


@contextmanager
def _estado():
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FILE.with_suffix('.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try: st = json.loads(FILE.read_text())
        except (OSError, ValueError): st = {}
        st.setdefault('vagas', []); st.setdefault('espera', [])
        try: yield st
        except CotaErro:
            FILE.write_text(json.dumps(st, indent=2, ensure_ascii=False)); raise
        FILE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


def _viva(bench):
    return (RUNTIME / bench / 'control.sock').exists()


# Mesmo valor do drop-in contrib/systemd/agent-bench@.service.d/10-cota.conf (3 núcleos, Lucas 20/10/2026).
# Ao sair da fila o runtime 50-CPUQuota.conf vence o 10-cota.conf, então o teto precisa ser reposto aqui.
COTA_FORA_DA_FILA = os.environ.get('AGENT_BENCH_COTA_FORA_DA_FILA', '300%')


class CotaErro(RuntimeError):
    pass


def _cota(bench, pesada):
    """Tira (pesada) ou repõe o teto de CPU da bancada. Falha levanta CotaErro: vaga sem tirar a cota
    deixa a Unreal em 3 núcleos e estraga a medição (throttling em 61% dos períodos em 21/10)."""
    # Vazio desfaz o limite; o drop-in runtime 50-CPUQuota.conf vence o 10-cota.conf.
    valor = 'CPUQuota=' if pesada else f'CPUQuota={COTA_FORA_DA_FILA}'
    try:
        r = subprocess.run(['systemctl', '--user', 'set-property', '--runtime', f'agent-bench@{bench}.service', valor],
                           capture_output=True, text=True, env=systemd_env(), timeout=30)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise CotaErro(f'agent-bench fila: não consegui aplicar {valor} em {bench}: {exc}') from exc
    if r.returncode:
        raise CotaErro(f'agent-bench fila: systemctl {valor} em {bench} falhou (rc {r.returncode}): '
                       f'{(r.stderr or r.stdout).strip()}')


def _repor_cota(bench):
    # Repor o teto não pode derrubar sair/limpar: avisa e segue.
    try: _cota(bench, False)
    except CotaErro as exc: print(f'{exc}; bancada pode ficar sem teto de CPU', file=sys.stderr, flush=True)


def _jogando():
    jogos = []
    for marca in sorted(m for pasta in _pastas_jogando() for m in pasta.glob('*')):
        try: pid = int(marca.read_text().split()[0])
        except (OSError, ValueError, IndexError): continue
        if Path(f'/proc/{pid}').exists() and marca.name not in jogos: jogos.append(marca.name)
    return jogos


def _vram_livre():
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, timeout=5).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _cabe_mais(st):
    if len(st['vagas']) >= VAGAS: return False
    # A VRAM da vaga recém-dada só aparece quando a Unreal dela carrega: espera ela subir.
    if st['vagas'] and time.time() - max(v['desde'] for v in st['vagas']) < SUBIDA_S: return False
    # Vale até pra primeira vaga: Unreal lançada fora da fila também ocupa VRAM.
    livre = _vram_livre()
    return livre is None or livre >= VRAM_LIVRE_MIN


def _limpar(st, agora):
    for v in list(st['vagas']):
        if agora - v['renovado'] > TTL or not _viva(v['bench']):
            st['vagas'].remove(v); _repor_cota(v['bench'])
    st['espera'] = [e for e in st['espera'] if agora - e['visto'] <= ESPERA_TTL and _viva(e['bench'])]


def _com_cota(st, bench, resposta):
    """Só devolve a vaga com a cota tirada; se o systemctl falhar, solta a vaga e propaga o erro."""
    try: _cota(bench, True)
    except CotaErro:
        st['vagas'] = [v for v in st['vagas'] if v['bench'] != bench]
        st['espera'] = [e for e in st['espera'] if e['bench'] != bench]
        raise
    return resposta


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
                return _com_cota(st, bench, {'status': 'com vaga', **atual, 'expira_em_s': TTL})
            fila = [e['bench'] for e in st['espera']]
            jogos = _jogando()
            if not jogos and (not fila or fila[0] == bench) and _cabe_mais(st):
                st['espera'] = [e for e in st['espera'] if e['bench'] != bench]
                vaga = {'bench': bench, 'motivo': motivo, 'desde': agora, 'renovado': agora}
                st['vagas'].append(vaga)
                return _com_cota(st, bench, {'status': 'com vaga', **vaga, 'expira_em_s': TTL})
            item = next((e for e in st['espera'] if e['bench'] == bench), None)
            if item: item['visto'] = agora
            else: st['espera'].append({'bench': bench, 'motivo': motivo, 'desde': agora, 'visto': agora})
            posicao = [e['bench'] for e in st['espera']].index(bench) + 1
            ocupada = ', '.join([f'humano jogando {j}' for j in jogos]
                                + [f"{v['bench']} ({v['motivo'] or 'sem motivo'})" for v in st['vagas']])
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
    _repor_cota(bench)
    return {'status': 'liberada' if tinha else 'não tinha vaga', 'bench': bench}


def promover(st, agora):
    """Dá vaga ao primeiro da espera enquanto couber. Serve também a quem
    ficou esperando com código antigo: entrar() devolve vaga a quem já está em st['vagas']."""
    dados = []
    while st['espera'] and not _jogando() and _cabe_mais(st):
        e = st['espera'].pop(0)
        vaga = {'bench': e['bench'], 'motivo': e['motivo'], 'desde': agora, 'renovado': agora}
        try: _cota(e['bench'], True)
        except CotaErro as exc:
            # Volta pra frente da fila: a própria bancada vai ver o erro ao chamar entrar().
            st['espera'].insert(0, e); print(exc, file=sys.stderr, flush=True); break
        st['vagas'].append(vaga); dados.append(e['bench'])
        break  # uma por chamada: a VRAM só aparece depois que a Unreal nova sobe
    return dados


def status():
    with _estado() as st:
        _limpar(st, time.time())
        promover(st, time.time())
        return {'vagas_pesadas': VAGAS, 'vram_livre_mib': _vram_livre(), 'vram_livre_min': VRAM_LIVRE_MIN,
                'ttl_s': TTL, 'humano_jogando': _jogando(),
                'vagas': st['vagas'], 'espera': st['espera']}


def rodar(bench, argv, motivo=''):
    """Segura a vaga só enquanto o comando roda."""
    motivo = motivo or ' '.join(argv)[:80]
    entrar(bench, motivo)
    try:
        proc = subprocess.Popen(argv)
        while True:
            try: return proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                # Falha na renovação não abandona o comando já rodando: avisa e tenta de novo no próximo minuto.
                try: entrar(bench, motivo)
                except CotaErro as exc: print(exc, file=sys.stderr, flush=True)
    finally: sair(bench)
