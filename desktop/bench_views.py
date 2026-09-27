#!/usr/bin/env python3
"""Host-side viewer supervisor. Never injects input or accesses clipboard."""
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socketserver
import subprocess
import time

from bench_control import RUNTIME, rpc

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'hypr' / 'agent-bench.lua'
REGISTRY = RUNTIME / 'views.json'
SLOTS = RUNTIME / 'view-workspaces.tsv'
AGENT_WORKSPACES = (6, 7, 8, 9, 10, 11)
HERE = ('--here', 'aqui')
LEAVE_SECONDS = 1.2
VIEWER_TITLE = re.compile(r'^Bancada dos agentes — ([a-z0-9][a-z0-9-]{0,39}) - TigerVNC$')
entries = {}
processes = {}
departed_since = {}
stopping = False

def pick_slot(exclude_name=None):
    reserved = {e['workspace'] for n, e in entries.items() if n != exclude_name}
    occupied = {w['workspace']['id'] for w in clients()}
    visible = {m['activeWorkspace']['id'] for m in json.loads(hypr('-j', 'monitors'))}
    for n in AGENT_WORKSPACES:
        if n not in reserved | occupied | visible:
            return n
    used = {n: 0 for n in AGENT_WORKSPACES}
    for n, e in entries.items():
        if n != exclude_name and e['workspace'] in used:
            used[e['workspace']] += 1
    return min(AGENT_WORKSPACES, key=lambda n: used[n])

def hypr(*args):
    result = subprocess.run(['hyprctl', *args], capture_output=True, text=True, timeout=5, check=True)
    if args and args[0] == 'eval' and result.stdout.strip() != 'ok':
        raise RuntimeError('Hyprland recusou o posicionamento: ' + result.stdout.strip())
    return result.stdout

def clients():
    return json.loads(hypr('-j', 'clients'))

def monitors():
    return json.loads(hypr('-j', 'monitors'))

def focused_workspace():
    for monitor in monitors():
        if monitor.get('focused'):
            return monitor.get('activeWorkspace', {}).get('id')
    return None

def focused_bench_name():
    try:
        active = json.loads(hypr('-j', 'activewindow'))
    except (OSError, ValueError, subprocess.CalledProcessError):
        active = {}
    if not isinstance(active, dict):
        active = {}
    title = active.get('title') or ''
    match = VIEWER_TITLE.fullmatch(title)
    if active.get('class') == 'Vncviewer' and match:
        name = match.group(1)
        if name in entries:
            return name
    workspace = focused_workspace()
    matches = [name for name, entry in entries.items() if entry.get('workspace') == workspace]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError('Várias bancadas neste workspace. Clique na janela da que você quer e aperte Super+Alt+A.')
    raise RuntimeError('Nenhuma bancada neste workspace. Super+6 … Super+0 abre a faixa 6–10; o painel cobre o 11.')

def save():
    for path, content in ((REGISTRY, json.dumps(entries, indent=2) + '\n'),
                          (SLOTS, ''.join(f'{name}\t{entry["workspace"]}\n' for name, entry in entries.items()))):
        temp = path.with_suffix(path.suffix + '.tmp')
        temp.write_text(content)
        temp.replace(path)

BAR = RUNTIME / 'bar.json'
_last_bar = None


def bar_state(now=None):
    """What the Omarchy bar shows per bench: who drives it and whether it is working."""
    from bench_ops import STATE, VAULT, EPHEMERAL_MARK
    now = time.time() if now is None else now
    benches = {}
    for name, entry in entries.items():
        runtime = RUNTIME / name
        try:
            record = json.loads((runtime / 'owner.json').read_text())
            owner = record.get('last_actor') or record.get('owner') or 'unknown'
        except (OSError, ValueError, AttributeError):
            owner = 'unknown'
        try:
            idle = max(0, int(now - float((runtime / 'last_activity').read_text().strip())))
        except (OSError, ValueError):
            idle = None
        if idle is not None and idle >= 60:
            idle -= idle % 10
        profile = STATE / name / 'chromium'
        if name == VAULT:
            kind = 'cofre'
        elif profile.is_dir() and not (profile / EPHEMERAL_MARK).exists():
            kind = 'persistente'  # an old copy of the seed
        else:
            kind = 'descartavel'
        benches[name] = {'workspace': entry.get('workspace'), 'owner': owner,
                         'active': idle is not None and idle < 60, 'idle_seconds': idle,
                         'human': (runtime / 'human-control').exists(), 'kind': kind}
    return benches


def publish_bar():
    global _last_bar
    benches = bar_state()
    if benches == _last_bar:
        return
    temp = BAR.with_suffix('.json.tmp')
    temp.write_text(json.dumps({'updated': time.time(), 'benches': benches}, ensure_ascii=False) + '\n')
    temp.replace(BAR)
    _last_bar = benches


def rules():
    hypr('eval', 'dofile(' + json.dumps(str(CONFIG)) + ')')

def live(name):
    return rpc(RUNTIME / name / 'control.sock', {'action': 'status'}, timeout=2)

def window(name):
    entry = entries[name]
    title = f'Bancada dos agentes — {name} - TigerVNC'
    return next((w for w in clients() if w['pid'] == entry.get('viewer_pid')
                 and w['class'] == 'Vncviewer' and w['title'] == title), None)

def ensure(name, reopen=True):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', name):
        raise ValueError('Nome de bancada inválido')
    info = live(name)
    if name not in entries:
        entries[name] = {'workspace': pick_slot(name), 'viewer_pid': None, 'launched': False}
        save()
        rules()
    entry = entries[name]
    if entry['workspace'] not in AGENT_WORKSPACES:
        entry['workspace'] = pick_slot(name)
        save()
        rules()
    proc = processes.get(name)
    if proc and proc.poll() is not None:
        code = proc.returncode
        processes.pop(name)
        entry['viewer_pid'] = None
        entry['last_exit_code'] = code
        # A normal close stays closed. X11 loss/crashes must recover after a delay.
        if code != 0:
            entry['launched'] = False
            entry['retry_at'] = time.monotonic() + 3
        save()
    if not reopen and time.monotonic() < entry.get('retry_at', 0):
        return {**entry, 'name': name}
    if name not in processes and (reopen or not entry['launched']):
        # Rules are installed before mapping; no focus-then-move workaround.
        rules()
        logfile = (RUNTIME / name / 'viewer.log').open('a')
        try:
            proc = subprocess.Popen(['vncviewer', '-ViewOnly=0', '-Shared',
                '-AcceptClipboard=0', '-SendClipboard=0', '-SendPrimary=0', '-SetPrimary=0',
                '-FullscreenSystemKeys=0', info['viewer_socket']], stdin=subprocess.DEVNULL,
                stdout=logfile, stderr=logfile)
        finally:
            logfile.close()
        processes[name] = proc
        entry.update(viewer_pid=proc.pid, launched=True)
        save()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError('Viewer encerrou; consulte viewer.log')
            if window(name): break
            time.sleep(.05)
        else:
            proc.terminate()
            raise RuntimeError('Viewer não apareceu como janela identificável')
    return {**entry, 'name': name, 'mode': 'humano' if (RUNTIME / name / 'human-control').exists() else 'agente'}

def dock(name):
    result = ensure(name)
    w = window(name)
    if w and w['workspace']['id'] != result['workspace']:
        # Revalidate PID, class and title inside the compositor before moving.
        address = json.dumps('address:' + w['address'])
        title = json.dumps(w['title'], ensure_ascii=False)
        hypr('eval', f'local w=hl.get_window({address}); '
             f'if w and w.pid=={w["pid"]} and w.class=="Vncviewer" and w.title=={title} then '
             f'hl.dispatch(hl.dsp.window.move({{window=w,workspace="{result["workspace"]}",follow=false}})) end')
        actual = window(name)
        if actual and actual['workspace']['id'] != result['workspace']:
            raise RuntimeError('A janela da bancada não retornou ao workspace reservado')
    return result

def handoff(name, human):
    info = live(name)
    if info.get('protocol_version', 1) < 2:
        raise RuntimeError('Esta bancada foi iniciada antes da atualização. O proprietário deve encerrá-la e iniciá-la quando puder para habilitar colaboração.')
    runtime = RUNTIME / name
    with (runtime / 'input.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Há uma operação em andamento nesta bancada. Tente assumir/devolver novamente ao terminar.')
        hold = runtime / 'human-control'
        if human:
            ensure(name)
            held_before = hold.exists()
            hold.touch(mode=0o600)
            try:
                rpc(runtime / 'control.sock', {'action': 'human-input', 'enabled': True})
            except Exception as exc:
                # Roll back only this attempt's pause when the server confirms
                # input is still disabled. Unknown state or an older pause stays.
                state = None
                try:
                    state = live(name).get('human_input')
                except Exception:
                    pass
                if (not held_before and isinstance(state, dict)
                        and state.get('confirmed') is True and state.get('enabled') is False):
                    hold.unlink(missing_ok=True)
                raise RuntimeError(f'A bancada {name} não liberou mouse/teclado: {exc}. '
                                   + tasks_hint(info)) from exc
        else:
            rpc(runtime / 'control.sock', {'action': 'human-input', 'enabled': False})
            hold.unlink(missing_ok=True)
            dock(name)
    return {**entries[name], 'name': name, 'mode': 'humano' if human else 'agente'}

def tasks_hint(info):
    info = info if isinstance(info, dict) else {}
    tasks = info.get('tasks')
    if isinstance(tasks, dict):
        current, maximum = tasks.get('current'), tasks.get('max')
        if (type(current) is int and type(maximum) is int and maximum > 0
                and current >= 0 and current * 10 >= maximum * 9):
            return (f'O cgroup da bancada está com {current}/{maximum} tarefas; '
                    'libere tarefas da bancada antes de tentar novamente.')
    name = info.get('name')
    name = name if isinstance(name, str) and name else 'NOME'
    return f'Veja journalctl --user -u agent-bench@{name}.service.'

def resolve(name):
    return focused_bench_name() if name in HERE else name

def release_departed(now=None):
    """Give the bench back when its reserved workspace is no longer on any monitor (Super+1, …)."""
    now = time.monotonic() if now is None else now
    try:
        visible = {monitor.get('activeWorkspace', {}).get('id') for monitor in monitors()}
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError):
        return []
    visible.discard(None)
    if not visible:
        return []
    released = []
    for name, entry in list(entries.items()):
        if not (RUNTIME / name / 'human-control').exists():
            departed_since.pop(name, None)
            continue
        if entry.get('workspace') in visible:
            departed_since.pop(name, None)
            continue
        departed_since.setdefault(name, now)
        if now - departed_since[name] < LEAVE_SECONDS:
            continue
        try:
            handoff(name, False)
            departed_since.pop(name, None)
            released.append(name)
            print('agent-bench-views auto-resume:', name, flush=True)
        except Exception as exc:
            print('agent-bench-views auto-resume:', name, exc, flush=True)
    return released

def scan():
    active = set()
    for path in sorted(RUNTIME.glob('*/control.sock'), key=lambda p: (p.parent.name != 'padrao', p.parent.name)):
        name = path.parent.name
        try:
            live(name)
        except (OSError, ValueError, RuntimeError):
            continue
        active.add(name)
        ensure(name, reopen=False)
    for name in set(entries) - active:
        proc = processes.pop(name, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        del entries[name]
        save()
        rules()

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            self.connection.settimeout(5)
            q = json.loads(self.rfile.readline(16384))
            action, name = q['action'], q['name']
            if action == 'ensure': result = ensure(name)
            elif action == 'dock':
                if (RUNTIME / name / 'human-control').exists():
                    raise RuntimeError('Bancada sob controle humano; aguarde a devolução.')
                result = dock(name)
            elif action == 'collaborate': result = handoff(resolve(name), True)
            elif action == 'resume': result = handoff(resolve(name), False)
            elif action == 'status': result = entries.get(name, {})
            else: raise ValueError('Ação desconhecida')
        except Exception as exc:
            result = {'error': str(exc)}
        try: self.wfile.write(json.dumps(result).encode() + b'\n')
        except BrokenPipeError: pass

def main():
    global stopping
    os.umask(0o077)
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    def stop(*_):
        global stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # Keep assignments across a supervisor restart. Its own viewers are in its cgroup.
    if REGISTRY.exists():
        for name, entry in json.loads(REGISTRY.read_text()).items():
            entries[name] = {'workspace': entry['workspace'], 'viewer_pid': None, 'launched': False}
    path = RUNTIME / 'views.sock'
    path.unlink(missing_ok=True)
    save()
    with socketserver.UnixStreamServer(str(path), Handler) as server:
        os.chmod(path, 0o600)
        server.timeout = 1
        next_scan = 0
        next_reap = 0
        while not stopping:
            if time.monotonic() >= next_scan:
                try:
                    scan()
                    release_departed()
                    publish_bar()
                except Exception as exc: print('agent-bench-views:', exc, flush=True)
                next_scan = time.monotonic() + 1
            if time.monotonic() >= next_reap:
                try:
                    from bench_ops import reap_idle
                    stopped = reap_idle()
                    if stopped:
                        print('agent-bench-views reap:', ','.join(stopped), flush=True)
                except Exception as exc:
                    print('agent-bench-views reap:', exc, flush=True)
                next_reap = time.monotonic() + 60
            server.handle_request()
    path.unlink(missing_ok=True)

if __name__ == '__main__':
    main()
