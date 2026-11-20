"""Instalação com versão: o repositório é a fonte, ~/.agents guarda qual commit roda.

Decisão do Lucas (19/11/2026): ``agent-bench update`` copia um commit do clone para
~/.agents e grava a versão; recusa se o código instalado tiver remendo à mão (a não
ser com --force, que guarda o remendo antes); ``agent-bench versao`` mostra a
versão instalada e a de cada bancada aberta. Só arquivos rastreados de bin/ e
desktop/ (sem testes); sessões, perfis e arquivos locais não são tocados.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
MANIFEST = BASE / 'desktop' / 'VERSAO.json'
BACKUPS = BASE / '.versoes'
KEEP_BACKUPS = 5
REPO = Path(os.environ.get('AGENT_BENCH_REPO', Path.home() / 'Projects' / 'omarchy-agent-bench'))
TZ = ZoneInfo('America/Sao_Paulo')


def _git(repo, *args, data=False):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=True, timeout=60)
    return result.stdout if data else result.stdout.decode().strip()


def tracked(repo, commit):
    """{relpath: mode} for the files that get installed."""
    files = {}
    for line in _git(repo, 'ls-tree', '-r', commit, '--', 'bin', 'desktop').splitlines():
        meta, path = line.split('\t', 1)
        mode, kind, _ = meta.split()
        if kind == 'blob' and not path.startswith('desktop/test_'):
            files[path] = 0o755 if mode == '100755' else 0o644
    return files


def render(data):
    return data.replace(b'@PREFIX@', str(BASE).encode())


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_manifest():
    try:
        value = json.loads(MANIFEST.read_text())
        return value if isinstance(value, dict) and isinstance(value.get('files'), dict) else {}
    except (OSError, ValueError):
        return {}


def installed_version():
    return load_manifest().get('commit')


def hand_edits(manifest=None):
    """Installed files that no longer match what the last update wrote."""
    manifest = load_manifest() if manifest is None else manifest
    edited = []
    for path, digest in manifest.get('files', {}).items():
        try:
            current = sha((BASE / path).read_bytes())
        except OSError:
            edited.append({'path': path, 'state': 'apagado'})
            continue
        if current != digest:
            edited.append({'path': path, 'state': 'alterado'})
    return edited


def _backup(paths, stamp):
    folder = BACKUPS / stamp
    for path in paths:
        source = BASE / path
        if source.exists():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(source.read_bytes())
    old = sorted(p for p in BACKUPS.glob('*') if p.is_dir())
    for extra in old[:-KEEP_BACKUPS]:
        subprocess.run(['rm', '-rf', '--', str(extra)], check=False)
    return str(folder)


def update(ref='origin/main', repo=None, force=False, fetch=True):
    repo = Path(repo) if repo else REPO
    if fetch:
        subprocess.run(['git', '-C', str(repo), 'fetch', '-q', 'origin'], capture_output=True, timeout=120)
    commit = _git(repo, 'rev-parse', '--verify', ref + '^{commit}')
    manifest = load_manifest()
    edited = hand_edits(manifest)
    if edited and not force:
        raise RuntimeError('INSTALACAO_REMENDADA: arquivos instalados mudaram desde a última versão '
                           f'({", ".join(e["path"] for e in edited)}). Leve o remendo para o repositório; '
                           '--force guarda uma cópia e instala por cima.')
    files = tracked(repo, commit)
    contents = {path: render(_git(repo, 'show', f'{commit}:{path}', data=True)) for path in files}
    changed = [p for p, data in contents.items() if not (BASE / p).exists() or (BASE / p).read_bytes() != data]
    stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M%S')
    backup = _backup(changed, stamp) if changed else None
    for path in changed:
        target = BASE / path
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name('.' + target.name + '.novo')
        temp.write_bytes(contents[path])
        temp.chmod(files[path])
        temp.replace(target)
    record = {'commit': commit, 'ref': ref, 'repo': str(repo), 'at': time.time(),
              'quando': datetime.now(TZ).strftime('%d/%m/%Y %H:%M'),
              'files': {p: sha(data) for p, data in contents.items()}}
    temp = MANIFEST.with_name('.VERSAO.json.novo')
    temp.write_text(json.dumps(record, indent=1) + '\n')
    temp.replace(MANIFEST)
    return {'commit': commit[:10], 'previous': (manifest.get('commit') or '')[:10] or None,
            'changed': changed, 'backup': backup, 'forced_over': [e['path'] for e in edited]}


def report(benches):
    """Installed version, hand edits and which open benches run older code."""
    current = installed_version()
    stale = [b['name'] for b in benches if b.get('version') != current]
    return {'installed': (current or '')[:10] or None, 'quando': load_manifest().get('quando'),
            'hand_edits': hand_edits(),
            'benches': {b['name']: (b.get('version') or '')[:10] or None for b in benches},
            'stale': stale}
