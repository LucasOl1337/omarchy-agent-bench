"""Shared bench operations for the CLI, MCP hub, reaper and tests."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.request import ProxyHandler, build_opener

from bench_control import RUNTIME, views

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / 'desktop/sessions'
IDLE_SECONDS = int(os.environ.get('AGENT_BENCH_IDLE_SECONDS', str(3 * 60 * 60)))
GC_DAYS = int(os.environ.get('AGENT_BENCH_GC_DAYS', '30'))
NEVER_REAP = frozenset({'padrao'})
BLANK_URLS = ('about:blank', 'chrome://newtab/', 'chrome://new-tab-page/')
MCP_BIN = BASE / 'bin/agent-bench-mcp'


def valid(name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', name):
        raise ValueError('Use um nome com letras minúsculas, números e hífen (até 40).')
    return name


def paths(name):
    return RUNTIME / valid(name), STATE / name


def request(name, payload, timeout=310):
    runtime, _ = paths(name)
    from bench_control import rpc
    return rpc(runtime / 'control.sock', payload, timeout=timeout)


def infer_owner():
    explicit = os.environ.get('AGENT_BENCH_OWNER', '').strip()
    if explicit:
        return explicit
    for key, label in (
        ('CURSOR_TRACE_ID', 'cursor'),
        ('CURSOR_SESSION', 'cursor'),
        ('CLAUDECODE', 'claude'),
        ('CLAUDE_CODE', 'claude'),
        ('CODEX_HOME', 'codex'),
        ('HERMES_HOME', 'hermes'),
        ('OPENCODE', 'opencode'),
        ('GEMINI_CLI', 'gemini'),
    ):
        if os.environ.get(key):
            return label
    return 'unknown'


def claim_owner(name, owner=None):
    owner = owner or infer_owner()
    payload = {'owner': owner, 'claimed_at': time.time()}
    runtime, state = paths(name)
    for folder in (runtime, state):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        (folder / 'owner.json').write_text(json.dumps(payload, indent=2) + '\n')
    return payload


def load_owner(name):
    for candidate in (RUNTIME / name / 'owner.json', STATE / name / 'owner.json'):
        try:
            return json.loads(candidate.read_text())
        except (OSError, ValueError):
            continue
    return {}


def touch_activity(name):
    runtime, _ = paths(name)
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    (runtime / 'last_activity').write_text(str(time.time()) + '\n')


def last_activity(name):
    stamp = RUNTIME / name / 'last_activity'
    try:
        return float(stamp.read_text().strip())
    except (OSError, ValueError):
        sock = RUNTIME / name / 'control.sock'
        try:
            return sock.stat().st_mtime
        except OSError:
            return None


def chromium_argv(state, extra=None):
    urls = extra if extra else ['about:blank']
    return ['chromium', '--ozone-platform=x11', '--no-first-run',
            '--no-default-browser-check', '--disable-gpu', '--remote-debugging-port=0',
            '--password-store=basic', '--user-data-dir=' + str(Path(state) / 'chromium'), *urls]


def _opener():
    return build_opener(ProxyHandler({}))


def cdp_snapshot(state):
    endpoint = Path(state) / 'chromium/DevToolsActivePort'
    info = {'status': 'fechado', 'endpoint_file': str(endpoint)}
    if not endpoint.exists():
        return info
    try:
        port = int(endpoint.read_text().splitlines()[0])
        if not 1 <= port <= 65535:
            raise ValueError('porta inválida')
        version = json.load(_opener().open(f'http://127.0.0.1:{port}/json/version', timeout=2))
        tabs = json.load(_opener().open(f'http://127.0.0.1:{port}/json/list', timeout=2))
    except (OSError, ValueError, IndexError):
        info['status'] = 'endpoint sem conexão; abra o navegador da bancada'
        return info
    pages = [t for t in tabs if t.get('type') in ('page', 'app')]
    info.update(status='conectado', port=port, version=version.get('Browser'),
                 webSocketDebuggerUrl=version.get('webSocketDebuggerUrl'),
                 browser_url=f'http://127.0.0.1:{port}',
                 pages=[{'url': t.get('url', ''), 'title': t.get('title', '')} for t in pages],
                 playwright=f"chromium.connectOverCDP('http://127.0.0.1:{port}')")
    return info


def chromium_busy(name):
    _, state = paths(name)
    snap = cdp_snapshot(state)
    if snap.get('status') != 'conectado':
        return False
    for page in snap.get('pages') or []:
        url = (page.get('url') or '').split('#', 1)[0].rstrip('/')
        if url and url not in BLANK_URLS:
            return True
    return False


def wait_cdp(state, timeout=15):
    deadline = time.monotonic() + timeout
    last = {'status': 'fechado'}
    while time.monotonic() < deadline:
        last = cdp_snapshot(state)
        if last.get('status') == 'conectado':
            return last
        time.sleep(.1)
    return last


def start(name, owner=None):
    valid(name)
    try:
        info = request(name, {'action': 'status'}, timeout=1)
    except (OSError, ValueError, RuntimeError):
        info = None
    if info is None:
        subprocess.run(['systemctl', '--user', 'start', f'agent-bench@{name}.service'], check=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                info = request(name, {'action': 'status'}, timeout=1)
                break
            except (OSError, ValueError):
                time.sleep(.1)
        else:
            raise RuntimeError(f'Bancada não iniciou. Verifique journalctl --user -u agent-bench@{name}.service')
    claim_owner(name, owner)
    touch_activity(name)
    return info


def ensure(name, owner=None, browser=False, url=None):
    info = start(name, owner=owner)
    view = views('ensure', name)
    cdp = cdp_snapshot(info.get('state', paths(name)[1]))
    if browser or url:
        if cdp.get('status') != 'conectado':
            argv = chromium_argv(info['state'], [url] if url else None)
            request(name, {'action': 'launch', 'argv': argv, 'cwd': str(Path.cwd())})
            cdp = wait_cdp(info['state'])
        elif url:
            request(name, {'action': 'exec', 'argv': ['chromium', '--ozone-platform=x11', url],
                            'cwd': str(Path.cwd())})
            cdp = wait_cdp(info['state'])
    owner_info = load_owner(name)
    result = {**info, **view, 'cdp': cdp, 'owner': owner_info.get('owner')}
    return result


def keep_session(name, reason='missão recorrente com login'):
    _, state = paths(name)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    (state / '.keep').write_text(reason.rstrip() + '\n')
    return str(state / '.keep')


def gc_sessions(days=None, apply=False):
    days = GC_DAYS if days is None else days
    cutoff = time.time() - days * 86400
    removed, kept = [], []
    if not STATE.exists():
        return {'removed': removed, 'kept': kept, 'apply': apply}
    for folder in sorted(p for p in STATE.iterdir() if p.is_dir()):
        name = folder.name
        try:
            valid(name)
        except ValueError:
            kept.append({'name': name, 'reason': 'nome inválido'})
            continue
        if (folder / '.keep').exists():
            kept.append({'name': name, 'reason': '.keep'})
            continue
        if (RUNTIME / name / 'control.sock').exists():
            kept.append({'name': name, 'reason': 'bancada ativa'})
            continue
        probe = subprocess.run(['systemctl', '--user', 'is-active', f'agent-bench@{name}.service'],
                               capture_output=True, text=True)
        if probe.stdout.strip() == 'active':
            kept.append({'name': name, 'reason': 'serviço ativo'})
            continue
        mtime = folder.stat().st_mtime
        for child in folder.rglob('*'):
            try:
                mtime = max(mtime, child.stat().st_mtime)
            except OSError:
                continue
        if mtime > cutoff:
            kept.append({'name': name, 'reason': f'usado há menos de {days}d'})
            continue
        if apply:
            subprocess.run(['rm', '-rf', str(folder)], check=True)
        removed.append({'name': name, 'reason': 'expirado'})
    return {'removed': removed, 'kept': kept, 'apply': apply, 'days': days}


def should_reap(name, now=None):
    if name in NEVER_REAP:
        return False
    if (RUNTIME / name / 'human-control').exists():
        return False
    lock = RUNTIME / name / 'input.lock'
    if lock.exists():
        import fcntl
        with lock.open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
            except BlockingIOError:
                return False
    if chromium_busy(name):
        return False
    seen = last_activity(name)
    now = time.time() if now is None else now
    if seen is None:
        return False
    return (now - seen) >= IDLE_SECONDS


def reap_idle():
    stopped = []
    for sock in RUNTIME.glob('*/control.sock'):
        name = sock.parent.name
        try:
            if not should_reap(name):
                continue
            subprocess.run(['systemctl', '--user', 'stop', f'agent-bench@{name}.service'], check=False)
            stopped.append(name)
        except (OSError, ValueError, RuntimeError) as exc:
            print('agent-bench reap:', name, exc, flush=True)
    return stopped
