"""Human visit/close for benches. Runs in the CLI, not inside the supervisor."""
import json
import re
import subprocess

from bench_control import RUNTIME, views

VIEWER_TITLE = re.compile(r'^Bancada dos agentes — ([a-z0-9][a-z0-9-]{0,39}) - TigerVNC$')
AGENT_WORKSPACES = (6, 7, 8, 9, 10, 11)
HERE = ('--here', 'aqui')


def hypr(*args):
    result = subprocess.run(['hyprctl', *args], capture_output=True, text=True, timeout=5, check=True)
    if args and args[0] == 'eval' and result.stdout.strip() != 'ok':
        raise RuntimeError('Hyprland recusou: ' + result.stdout.strip())
    return result.stdout


def clients():
    return json.loads(hypr('-j', 'clients'))


def focused_workspace():
    for monitor in json.loads(hypr('-j', 'monitors')):
        if monitor.get('focused'):
            return monitor.get('activeWorkspace', {}).get('id')
    return None


def active_window():
    try:
        window = json.loads(hypr('-j', 'activewindow'))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return {}
    return window if isinstance(window, dict) else {}


def load_entries():
    path = RUNTIME / 'views.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return data if isinstance(data, dict) else {}


def viewer_name(window):
    if not window or window.get('class') != 'Vncviewer':
        return None
    match = VIEWER_TITLE.fullmatch(window.get('title') or '')
    return match.group(1) if match else None


def benches_on(workspace, entries):
    return [name for name, entry in entries.items() if entry.get('workspace') == workspace]


def human_windows_on(workspace, windows=None):
    found = []
    for client in windows if windows is not None else clients():
        if (client.get('workspace') or {}).get('id') != workspace:
            continue
        if viewer_name(client):
            continue
        found.append(client)
    return found


def focus_workspace(workspace):
    hypr('dispatch', 'hl.dsp.focus({ workspace = "%s" })' % workspace)


def focus_address(address):
    addr = json.dumps('address:' + address)
    hypr('eval', f'local w=hl.get_window({addr}); if w then hl.dispatch(hl.dsp.focus({{window=w}})) end')


def bench_label(name):
    try:
        owner = json.loads((RUNTIME / name / 'owner.json').read_text()).get('owner')
    except (OSError, ValueError):
        owner = None
    return f'{name} ({owner})' if owner and owner != 'unknown' else name


def notify(text):
    subprocess.run(['notify-send', '-a', 'Bancada', 'Bancada', text], check=False, timeout=5)


def resolve_visit(target, entries, window=None, workspace=None):
    target = str(target)
    name = None
    workspace = focused_workspace() if workspace is None and target in HERE else workspace
    window = active_window() if window is None and target in HERE else window
    if target in HERE:
        name = viewer_name(window)
        if not name:
            matches = benches_on(workspace, entries)
            name = matches[0] if len(matches) == 1 else None
        if name and name in entries:
            workspace = entries[name].get('workspace', workspace)
        return workspace, name
    if target.isdigit() and int(target) in AGENT_WORKSPACES:
        workspace = int(target)
        matches = benches_on(workspace, entries)
        return workspace, matches[0] if len(matches) == 1 else None
    if target not in entries:
        raise RuntimeError('Sem bancada ' + target + ' neste momento.')
    return entries[target]['workspace'], target


def visit(target):
    entries = load_entries()
    workspace, name = resolve_visit(target, entries)
    if workspace in AGENT_WORKSPACES:
        focus_workspace(workspace)
    elif name:
        focus_workspace(entries[name]['workspace'])
    else:
        return {'workspace': workspace, 'visited': False}
    shared = benches_on(workspace, entries) if not name else []
    if len(shared) > 1:
        # Two benches share this workspace: land on it and let the human pick,
        # instead of failing before the workspace switch.
        labels = ', '.join(bench_label(n) for n in shared)
        notify(f'Workspace {workspace} tem {len(shared)} bancadas: {labels}. '
               'Clique na que quer e use Super+Alt+A para assumir.')
        return {'workspace': workspace, 'visited': True, 'benches': shared}
    if name:
        for client in clients():
            if viewer_name(client) == name:
                try:
                    focus_address(client['address'])
                except subprocess.CalledProcessError:
                    pass
                break
        result = views('collaborate', name)
        return {**result, 'visited': True}
    return {'workspace': workspace, 'visited': True}


def close_decision(window, workspace, entries, windows):
    name = viewer_name(window)
    if name:
        return ('stop', name)
    if workspace in AGENT_WORKSPACES and not human_windows_on(workspace, windows):
        matches = benches_on(workspace, entries)
        if len(matches) > 1:
            raise RuntimeError('Várias bancadas neste workspace. Clique na janela da que você quer.')
        if len(matches) == 1:
            return ('stop', matches[0])
    return ('close', None)


def stop_bench(name):
    subprocess.run(['systemctl', '--user', 'stop', f'agent-bench@{name}.service'], check=False)
    return {'stopped': name}


def close_here():
    entries = load_entries()
    action, name = close_decision(active_window(), focused_workspace(), entries, clients())
    if action == 'stop':
        return stop_bench(name)
    hypr('dispatch', 'hl.dsp.window.close()')
    return {'closed': 'window'}
