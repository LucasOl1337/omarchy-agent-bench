"""Shared bench operations for the CLI, MCP hub, reaper and tests."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import time
import uuid
from urllib.parse import quote, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from bench_control import RUNTIME, views, control

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / 'desktop/sessions'
IDLE_SECONDS = int(os.environ.get('AGENT_BENCH_IDLE_SECONDS', str(25 * 60)))
# A bench that burned more CPU than this in the window is still working (a game,
# a render, a long script) even without agent commands: about 3% of one core.
IDLE_CPU_SECONDS = float(os.environ.get('AGENT_BENCH_IDLE_CPU_SECONDS', '20'))
IDLE_CPU_WINDOW = 10 * 60
GC_DAYS = int(os.environ.get('AGENT_BENCH_GC_DAYS', '30'))
# Lucas, 23/09/2026: nothing stays open just because it is fixed; only what is in use.
NEVER_REAP = frozenset()
# The only logged-in profile. Everything else gets a throwaway one.
VAULT = 'cofre'
EPHEMERAL_MARK = '.efemero'
CDP_PORT_BASE = 19000
BLANK_URLS = ('about:blank', 'chrome://newtab/', 'chrome://new-tab-page/')
MCP_BIN = BASE / 'bin/agent-bench-mcp'
PROC = Path('/proc')
CGROUP = Path('/sys/fs/cgroup')
USER_RUNTIME_ROOT = Path('/run/user')


def systemd_env():
    """Find the local UID's manager bus without changing the bench's own D-Bus.

    Harnesses may omit login variables or inherit the isolated Xvnc bus. Only
    systemd clients receive this environment; never put it into os.environ.
    """
    uid = os.getuid()
    runtime = USER_RUNTIME_ROOT / str(uid)
    bus = runtime / 'bus'
    try:
        directory = runtime.lstat()
        endpoint = bus.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != uid
                or directory.st_mode & 0o022 or not stat.S_ISSOCK(endpoint.st_mode)
                or endpoint.st_uid != uid):
            raise ValueError('diretório ou socket não pertence ao usuário local')
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'USER_BUS_INDISPONIVEL: socket local validado ausente em {bus}') from exc
    env = os.environ.copy()
    env.update(XDG_RUNTIME_DIR=str(runtime), DBUS_SESSION_BUS_ADDRESS=f'unix:path={bus}')
    return env


def bench_service(name):
    return f'agent-bench@{valid(name)}.service'


def stop(name):
    return subprocess.run(['systemctl', '--user', 'stop', bench_service(name)],
                          env=systemd_env(), check=True)


def launch_cua(name, env):
    """Pipe MCP through a private service stopped whenever its bench stops.

    A process spawned by the harness is not in the bench's cgroup. A transient
    service with BindsTo + After also handles stops from the CLI, viewer and
    idle reaper, even if they run in another harness. The UUID prevents one
    pool from stopping another pool's driver for the same bench.
    """
    from bench_cua_env import validate_cua_env
    validate_cua_env(name, env)
    service = bench_service(name)
    unit = f'agent-bench-cua-{name}-{uuid.uuid4().hex}.service'
    executable = shutil.which('cua-driver', path=env.get('PATH'))
    if not executable:
        raise RuntimeError('cua-driver não encontrado no PATH')
    # Do not inherit the user manager's human DISPLAY/bus or send harness
    # credentials to the unit. Carry only the bench desktop and tool context.
    keys = ('HOME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'AGENT_BENCH_NAME',
            'DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR',
            'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME',
            'XDG_SESSION_TYPE', 'XDG_CURRENT_DESKTOP', 'GDK_BACKEND',
            'QT_QPA_PLATFORM', 'GTK_USE_PORTAL', 'LIBGL_ALWAYS_SOFTWARE')
    command = ['systemd-run', '--user', '--quiet', '--pipe', '--wait', '--collect',
               '--service-type=exec', '--expand-environment=no', '--unit=' + unit,
               '--property=BindsTo=' + service, '--property=After=' + service,
               '--property=TimeoutStopSec=3s', '--property=KillMode=control-group',
               '--', '/usr/bin/env', '-i',
               *(f'{key}={env[key]}' for key in keys if key in env),
               '/usr/bin/python3', '-I', '-S', str(BASE / 'desktop/bench_uinput_guard.py'),
               str(Path(executable).resolve()), 'mcp', '--direct', '--no-overlay']
    proc = subprocess.Popen(command, env=systemd_env(), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, bufsize=1,
                            start_new_session=True)
    proc.bench_cua_unit = unit
    return proc


def stop_cua(proc):
    """Stop only the unique unit this pool started, then reap its pipe client."""
    # A dead systemd-run client is not proof its service stopped. Conversely,
    # BindsTo may already have stopped and collected the unit (exit status 5).
    result = subprocess.run(['systemctl', '--user', 'stop', proc.bench_cua_unit],
                            env=systemd_env(), capture_output=True, text=True, timeout=10)
    if result.returncode not in (0, 5):
        raise RuntimeError(f'CUA_STOP_FALHOU: {result.stderr.strip()}')
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # The unit is stopped already; this is solely our systemd-run client.
        proc.kill()
        proc.wait(timeout=5)
    for stream in (proc.stdin, proc.stdout):
        if stream:
            stream.close()


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
    return owner_from_ancestors() or 'unknown'


HARNESSES = ('claude', 'codex', 'grok', 'hermes', 'jcode', 'opencode', 'gemini', 'cursor')


def owner_from_ancestors(pid=None):
    """Name the harness that runs this command by walking its parent processes.

    Codex, Grok and jcode set no marker variable, so their benches showed "unknown".
    """
    pid = os.getppid() if pid is None else pid
    for _ in range(40):
        if pid <= 1:
            return None
        try:
            args = [os.fsdecode(a) for a in (PROC / str(pid) / 'cmdline').read_bytes().split(b'\0') if a]
            parent = int((PROC / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
        names = [Path(a).name.lower() for a in args[:2]]
        if names and names[0].startswith('electron') and any('Daily Work app' in a for a in args):
            return 'dailywork'
        for harness in HARNESSES:
            if any(n == harness or n.startswith(harness + '-') or n.startswith(harness + '.') for n in names):
                return harness
        pid = parent
    return None


# Programs that start or host a harness. Named so the human sees where a bench's
# driver came from: a Maestri canvas, a herdr pane, the DailyWork app, Hermes.
LAUNCHERS = (('maestri-app', 'maestri'), ('maestri', 'maestri'), ('herdr', 'herdr'), ('hermes', 'hermes'),
             ('tmux', 'tmux'), ('zellij', 'zellij'), ('sshd', 'ssh'), ('ghostty', 'terminal'),
             ('alacritty', 'terminal'), ('kitty', 'terminal'), ('foot', 'terminal'), ('xterm', 'terminal'),
             ('code', 'vscode'), ('cursor', 'cursor'))
SKILL_RUN = re.compile(r'skills/execucoes/(sk-[A-Za-z0-9-]+)')


def _proc(pid):
    args = [os.fsdecode(a) for a in (PROC / str(pid) / 'cmdline').read_bytes().split(b'\0') if a]
    parent = int((PROC / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[1])
    try:
        cwd = os.readlink(PROC / str(pid) / 'cwd')
    except OSError:
        cwd = None
    return args, parent, cwd


def describe_origin(pid):
    """Who is behind a client process: harness, its project folder and launcher.

    Reads /proc only while the client is still connected, so short CLI calls are
    resolved before they exit. Returns None when the process is already gone.
    """
    chain = []
    for _ in range(40):
        if pid <= 1:
            break
        try:
            args, parent, cwd = _proc(pid)
        except (OSError, ValueError, IndexError):
            break
        chain.append((pid, [Path(a).name.lower() for a in args[:2]], ' '.join(args[:6]), cwd))
        pid = parent
    if not chain:
        return None
    first = chain[0][1]
    out = {'client_pid': chain[0][0],
           'client': ('agent-bench-mcp' if 'agent-bench-mcp' in first else
                      'agent-bench' if 'agent-bench' in first else first[0] if first else None),
           'harness': None, 'harness_pid': None, 'project': None, 'via': None, 'skill_run': None}
    above = len(chain)
    for i, (p, names, text, cwd) in enumerate(chain):
        if names and (names[0].startswith('electron') or names[0] in ('daily work app', 'dailywork')) and ('Daily Work app' in text or names[0] != 'electron'):
            out.update(harness='dailywork', harness_pid=p, project=cwd)
        else:
            for harness in HARNESSES:
                if any(n == harness or n.startswith(harness + '-') or n.startswith(harness + '.') for n in names):
                    out.update(harness=harness, harness_pid=p, project=cwd)
                    break
        if out['harness']:
            above = i + 1
            break
    for p, names, text, cwd in chain[above:]:
        label = next((l for launcher, l in LAUNCHERS if names and (names[0] == launcher or names[0].startswith(launcher + '-'))), None)
        if label:
            out['via'] = label
            break
    # Only the harness and what started it count: a shell cd'd into the app is not DailyWork.
    top = chain[max(0, above - 1):]
    everything = ' '.join(text + ' ' + (cwd or '') for _, _, text, cwd in top)
    run = SKILL_RUN.search(everything)
    if run:
        out['skill_run'] = run.group(1)
    started_by_app = out['harness'] == 'dailywork' or any(names and names[0].startswith('electron') and 'Daily Work app' in text for _, names, text, _ in top)
    # DailyWork starts CLIs detached: they end under systemd, so the trace is their folders.
    detached_in_app = out['via'] is None and 'Daily Work app' in (out['project'] or '')
    if run or started_by_app or detached_in_app or 'dailywork-grok-scratch' in everything:
        out['via'] = 'dailywork'
    return out


def record_origin(name, pid, action, keep=12):
    """Remember the recent distinct clients of a bench, newest first, in its runtime dir."""
    origin = describe_origin(pid) if pid else None
    if not origin:
        return None
    runtime, _ = paths(name)
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = runtime / 'origins.json'
    with (runtime / 'origins.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            items = json.loads(target.read_text())
            items = items if isinstance(items, list) else []
        except (OSError, ValueError):
            items = []
        key = (origin['harness'], origin['harness_pid'] or origin['client_pid'])
        now = time.time()
        previous = next((i for i in items if (i.get('harness'), i.get('harness_pid') or i.get('client_pid')) == key), None)
        entry = {**origin, 'first_at': (previous or {}).get('first_at', now), 'last_at': now,
                 'last_action': action, 'count': (previous or {}).get('count', 0) + 1}
        items = [entry] + [i for i in items if i is not previous][:keep - 1]
        temp = runtime / ('origins.' + uuid.uuid4().hex + '.tmp')
        with temp.open('x') as output:
            os.chmod(temp, 0o600)
            output.write(json.dumps(items, indent=2) + '\n')
        temp.replace(target)
    return entry


def claim_owner(name, owner=None):
    """Record actors, without transferring ownership or granting a task lease."""
    owner = owner or infer_owner()
    runtime, state = paths(name)
    for folder in (runtime, state):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The persistent record is authoritative from v2 onwards; runtime is only a
    # compatibility mirror. Serialize first actors and publish whole JSON files.
    with (state / 'owner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        payload = load_owner(name)
        now = time.time()
        if not payload:
            payload = {'owner': owner, 'claimed_at': now, 'owner_origin': 'first_observed_actor'}
        elif payload.get('metadata_version') != 2:
            # The old value meant last writer. Do not invent the original creator.
            payload['owner_origin'] = 'legacy_label'
        payload.update(metadata_version=2, last_actor=owner, last_seen_at=now)
        for folder in (state, runtime):
            temp = folder / ('owner.' + uuid.uuid4().hex + '.tmp')
            try:
                with temp.open('x') as output:
                    os.chmod(temp, 0o600)
                    output.write(json.dumps(payload, indent=2) + '\n')
                temp.replace(folder / 'owner.json')
            finally:
                temp.unlink(missing_ok=True)
        return payload


def load_owner(name):
    def read(candidate):
        try:
            value = json.loads(candidate.read_text())
            if isinstance(value, dict) and isinstance(value.get('owner'), str) and value['owner']:
                return value
        except (OSError, ValueError):
            pass
        return {}
    runtime, state = paths(name)
    persistent = read(state / 'owner.json')
    if persistent.get('metadata_version') == 2:
        return persistent
    return read(runtime / 'owner.json') or persistent


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


def chromium_argv(state, extra=None, *, port):
    urls = extra if extra else ['about:blank']
    # Renderer cap: each renderer is ~15-20 threads and a mission with a hundred
    # tabs pushed a bench past its TasksMax on 18/09/2026. Past the cap Chromium
    # shares renderers between tabs instead of forking more.
    # Stopping a bench kills Xvnc under Chromium; without this flag every next
    # launch opens the "Restore pages?" bubble over the agent's work.
    # Port 0 is how ChromeDriver launches, so Chromium sets navigator.webdriver for
    # it; a fixed port does not. --disable-gpu removed WebGL entirely. Together they
    # kept Cloudflare on "Verify you are human"; without them the managed challenge
    # passed with a CDP client attached (bench test, 23/09/2026).
    base = ['chromium', '--ozone-platform=x11', '--ozone-platform-hint=x11', '--no-first-run',
            '--no-default-browser-check', f'--remote-debugging-port={int(port)}',
            '--renderer-process-limit=24', '--hide-crash-restore-bubble']
    # Every process owns a distinct directory and uses its bench's isolated D-Bus.
    return [*base, '--password-store=basic', '--profile-directory=Default',
            '--user-data-dir=' + str(Path(state) / 'chromium'), *urls]


def _opener():
    return build_opener(ProxyHandler({}))


def _main_chromium_pids(profile):
    """Enumerate main processes naming this exact profile; never infer ownership from argv alone."""
    profile_arg = ('--user-data-dir=' + str(profile)).encode()
    result = []
    try:
        entries = list(PROC.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            args = _process_args(entry)
            if (Path(os.fsdecode(args[0])).name == 'chromium'
                    and profile_arg in args and not any(arg.startswith(b'--type=') for arg in args)):
                result.append(int(entry.name))
        except (OSError, IndexError, ValueError):
            continue
    return result


def _process_args(entry):
    """Chromium rewrites its main process title into one space-joined string, so
    /proc/PID/cmdline has no NUL separators there; its children keep them."""
    raw = (entry / 'cmdline').read_bytes().rstrip(b'\0')
    args = raw.split(b'\0')
    if len(args) == 1 and b' --' in raw:
        args = raw.split(b' ')
    return args


def _listener_inodes(port):
    """Read TCP LISTEN inodes for 127.0.0.1:port from procfs, not an arbitrary CDP response."""
    result = set()
    for table in ('tcp', 'tcp6'):
        try:
            rows = (PROC / 'net' / table).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            try:
                address, raw_port = fields[1].split(':')
                if (int(raw_port, 16) == port and fields[3] == '0A'
                        and address in ('0100007F', '00000000000000000000000001000000')):
                    result.add(fields[9])
            except (IndexError, ValueError):
                continue
    return result


def _cdp_owner_matches(profile, pid, port):
    pids = _main_chromium_pids(profile)
    if pids != [pid]:
        return False
    inodes = _listener_inodes(port)
    if not inodes:
        return False
    targets = {f'socket:[{inode}]' for inode in inodes}
    try:
        for fd in (PROC / str(pid) / 'fd').iterdir():
            try:
                if os.readlink(fd) in targets:
                    return True
            except OSError:
                continue
        return False
    except OSError:
        return False


def cdp_snapshot(state, include_pages=True):
    endpoint = Path(state) / 'chromium/DevToolsActivePort'
    info = {'status': 'fechado', 'endpoint_file': str(endpoint)}
    if not endpoint.exists():
        return info
    profile = Path(state) / 'chromium'
    try:
        owner = profile_process(profile)
        if owner is None and _main_chromium_pids(profile):
            return {**info, 'status': 'bloqueado', 'error': 'CHROMIUM_PERFIL_AMBIGUO: processo sem dono validado.'}
        if owner and len(_main_chromium_pids(profile)) != 1:
            return {**info, 'status': 'bloqueado', 'error': 'CHROMIUM_PERFIL_AMBIGUO: múltiplos processos principais no mesmo perfil.'}
    except RuntimeError:
        return {**info, 'status': 'bloqueado', 'error': 'CHROMIUM_PERFIL_AMBIGUO: dono do perfil não verificável.'}
    try:
        lines = endpoint.read_text().splitlines()
        port = int(lines[0])
        if not 1 <= port <= 65535:
            raise ValueError('porta inválida')
        if owner and not _cdp_owner_matches(profile, owner['pid'], port):
            return {**info, 'status': 'bloqueado', 'error': 'CHROMIUM_PERFIL_AMBIGUO: porta CDP não pertence ao dono do perfil.'}
        version = json.load(_opener().open(f'http://127.0.0.1:{port}/json/version', timeout=2))
        if not lines[1].startswith('/devtools/browser/') or urlparse(version.get('webSocketDebuggerUrl', '')).path != lines[1]:
            raise ValueError('endpoint pertence a outro processo')
        tabs = json.load(_opener().open(f'http://127.0.0.1:{port}/json/list', timeout=2)) if include_pages else []
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


def _pages_busy(snap):
    if snap.get('status') != 'conectado':
        return False
    for page in snap.get('pages') or []:
        url = (page.get('url') or '').split('#', 1)[0].rstrip('/')
        if url and url not in BLANK_URLS:
            return True
    return False


def chromium_busy(name):
    try:
        snap = bench_browser_snapshot(name)
        if snap.get('status') == 'fechado' and not snap.get('pid'):
            return False
        if snap.get('status') != 'conectado':
            return True
        return _pages_busy(snap)
    except RuntimeError:
        return True


def profile_process(profile):
    """Resolve o dono pelo lock desse perfil; ausência de --user-data-dir não prova identidade."""
    lock = Path(profile) / 'SingletonLock'
    if not os.path.lexists(lock):
        return None
    try:
        hostname, pid = os.readlink(lock).rsplit('-', 1)
        if hostname != socket.gethostname() or not pid.isdigit():
            raise ValueError('lock de outro host ou PID inválido')
        proc = PROC / pid
        if not proc.exists():
            # After logout/reboot Chromium may leave its lock behind. Do not
            # delete it: Chromium handles its own stale local lock at startup.
            return None
        cmd = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').split()
        if not cmd or Path(os.fsdecode(cmd[0])).name != 'chromium' or any(a.startswith(b'--type=') for a in cmd):
            raise ValueError('PID do lock não é o processo principal do Chromium')
        match = re.search(r'agent-bench@([a-z0-9-]+)\.service', (proc / 'cgroup').read_text())
        return {'pid': int(pid), 'bench': match.group(1) if match else None}
    except (OSError, ValueError) as exc:
        raise RuntimeError('CHROMIUM_LOCALIZACAO_INDETERMINADA: não foi possível validar o dono do perfil. Preserve o lock e o navegador existente.') from exc


def process_location(proc):
    """Bancada onde o processo vive, ou ``desktop`` quando não há cgroup de bancada."""
    if not proc:
        return None
    return proc.get('bench') or 'desktop'


def require_bench_profile(state):
    """Require an already prepared profile; never copy or create browser identity."""
    state = Path(state)
    profile = state / 'chromium'
    if not profile.is_dir() or not (profile / 'Default').is_dir():
        raise RuntimeError('CHROMIUM_PERFIL_AUSENTE: prepare o perfil Default dentro da bancada; '
                           'agent-bench não cria perfil vazio nem copia cookies de outro navegador.')
    return profile


def provide_bench_profile(state):
    """Give a CDP bench its browser profile without copying anyone's identity.

    Logged-in accounts live only in the vault, whose profile is never copied. Any
    other bench gets an empty profile marked ephemeral, deleted when it stops.
    Existing profiles, including the old seeded copies, are used as they are.
    """
    state = Path(state)
    profile = state / 'chromium'
    if not profile.is_dir():
        profile.mkdir(parents=True, mode=0o700)
        if state.name == VAULT:
            (state / '.keep').write_text('cofre de contas do Lucas\n')
        else:
            (profile / EPHEMERAL_MARK).write_text('perfil descartável: apagado quando a bancada para\n')
    return profile


def cdp_port(info):
    """One fixed CDP port per live bench, derived from its unique X display."""
    display = str(info.get('display') or '')
    if not re.fullmatch(r':\d+', display):
        raise RuntimeError('CDP_PORTA_INDEFINIDA: bancada sem display conhecido.')
    return CDP_PORT_BASE + int(display[1:])


def publish_endpoint(state, port):
    """A fixed port makes Chromium skip DevToolsActivePort; write it for the existing readers."""
    profile = Path(state) / 'chromium'
    owner = profile_process(profile)
    if not owner or not _cdp_owner_matches(profile, owner['pid'], port):
        return False
    version = json.load(_opener().open(f'http://127.0.0.1:{port}/json/version', timeout=2))
    path = urlparse(version.get('webSocketDebuggerUrl', '')).path
    if not path.startswith('/devtools/browser/'):
        return False
    temp = profile / ('.DevToolsActivePort.' + uuid.uuid4().hex)
    temp.write_text(f'{port}\n{path}\n')
    temp.replace(profile / 'DevToolsActivePort')
    return True


def bench_browser_snapshot(name, include_pages=True):
    _, state = paths(name)
    profile = state / 'chromium'
    meta = {'profile': 'bancada', 'user_data_dir': str(profile),
            'prepared': (profile / 'Default').is_dir()}
    try:
        proc = profile_process(profile)
    except RuntimeError as exc:
        return {**meta, 'status': 'bloqueado', 'error': str(exc)}
    location = process_location(proc)
    meta.update(lives_in=location, pid=proc['pid'] if proc else None)
    if proc is None:
        return {**meta, 'status': 'fechado'}
    if location != name:
        return {**meta, 'status': 'bloqueado', 'error':
                f'CHROMIUM_FORA_DA_BANCADA: perfil pertence a {location}; solicitado {name}.'}
    if len(_main_chromium_pids(profile)) != 1:
        return {**meta, 'status': 'bloqueado', 'error':
                'CHROMIUM_PERFIL_AMBIGUO: múltiplos processos principais no mesmo perfil.'}
    return {**cdp_snapshot(state, include_pages=include_pages), **meta}


def open_personal_tab(snap, url):
    target = f"http://127.0.0.1:{snap['port']}/json/new?{quote(url, safe=':/?&=%#+@~')}"
    return json.load(_opener().open(Request(target, method='PUT'), timeout=5))


@contextmanager
def browser_launch_lock(state):
    # control() holds input.lock shared, so concurrent ensures all saw "fechado"
    # and each launched Chromium on the same profile (23/09/2026: three at once,
    # CDP file pointing at a process that did not own the profile lock).
    Path(state).mkdir(parents=True, exist_ok=True, mode=0o700)
    with (Path(state) / 'browser.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def open_browser(name, info, urls=None, isolated=False):
    """Open the persistent Chromium that was explicitly prepared for this bench."""
    state = info['state']
    with control(name), browser_launch_lock(state):
        if isolated:
            raise RuntimeError('CHROMIUM_PERFIL_AUSENTE: --isolado não cria perfil vazio; prepare o perfil da bancada.')
        provide_bench_profile(state)
        snap = bench_browser_snapshot(name)
        if snap.get('status') == 'bloqueado':
            raise RuntimeError(snap['error'])
        if snap.get('status') != 'conectado' and snap.get('pid'):
            # Fixed-port browser whose endpoint file was never written or was lost.
            try:
                if publish_endpoint(state, cdp_port(info)):
                    snap = bench_browser_snapshot(name)
            except (OSError, ValueError, RuntimeError):
                pass
        if snap.get('status') != 'conectado':
            if snap.get('pid'):
                raise RuntimeError('Chromium da bancada está aberto sem CDP. Preserve a sessão.')
            port = cdp_port(info)
            if _listener_inodes(port):
                raise RuntimeError(f'CDP_PORTA_OCUPADA: a porta {port} desta bancada já está em uso.')
            (Path(state) / 'chromium/DevToolsActivePort').unlink(missing_ok=True)
            request(name, {'action': 'launch', 'argv': chromium_argv(state, port=port), 'cwd': str(Path.cwd())})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    publish_endpoint(state, port)
                except (OSError, ValueError, RuntimeError):
                    pass
                snap = bench_browser_snapshot(name)
                if snap.get('status') == 'bloqueado':
                    raise RuntimeError(snap['error'])
                if snap.get('status') == 'conectado':
                    break
                time.sleep(.1)
        if snap.get('status') != 'conectado':
            raise RuntimeError('Chromium não expôs CDP dentro da bancada em 30 s.')
        for url in urls or []:
            open_personal_tab(snap, url)
        return bench_browser_snapshot(name)


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
    # Record the requester before boot's fallback actor (systemd) can replace it.
    # This records a request, not proof the service started or a claim of exclusivity.
    claim_owner(name, owner)
    try:
        info = request(name, {'action': 'status'}, timeout=1)
    except (OSError, ValueError, RuntimeError):
        info = None
    if info is None:
        subprocess.run(['systemctl', '--user', 'start', bench_service(name)],
                       env=systemd_env(), check=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                info = request(name, {'action': 'status'}, timeout=1)
                break
            except (OSError, ValueError):
                time.sleep(.1)
        else:
            raise RuntimeError(f'Bancada não iniciou. Verifique journalctl --user -u agent-bench@{name}.service')
    touch_activity(name)
    return info


def ensure(name, owner=None, browser=False, url=None, isolated=False):
    info = start(name, owner=owner)
    view = views('ensure', name)
    state = info.get('state', paths(name)[1])
    if browser or url:
        cdp = open_browser(name, info, [url] if url else None, isolated=isolated)
    else:
        cdp = bench_browser_snapshot(name)
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
                               capture_output=True, text=True, env=systemd_env())
        if probe.returncode not in (0, 3, 4):
            kept.append({'name': name, 'reason': 'estado do serviço indisponível'})
            continue
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


def _bench_cgroup(pid, name):
    """Resolve only the verified bench's cgroup-v2 subtree, never a neighbor."""
    text = (PROC / str(pid) / 'cgroup').read_text()
    lines = [line[3:] for line in text.splitlines() if line.startswith('0::')]
    if len(lines) != 1 or not lines[0].startswith('/'):
        raise ValueError('cgroup v2 ausente')
    parts = Path(lines[0]).parts[1:]
    service = bench_service(name)
    if '..' in parts or service not in parts:
        raise ValueError('processo fora da bancada')
    return CGROUP.joinpath(*parts[:parts.index(service) + 1])


def _cgroup_pids(root):
    def unreadable(error):
        raise error
    pids = set()
    for directory, _, files in os.walk(root, onerror=unreadable):
        if 'cgroup.procs' not in files:
            raise ValueError('inventário de processos incompleto')
        for line in (Path(directory) / 'cgroup.procs').read_text().splitlines():
            if not line.isdecimal() or int(line) <= 0:
                raise ValueError('PID inválido no cgroup')
            pids.add(int(line))
    if not pids:
        raise ValueError('cgroup vazio ou indisponível')
    return pids


def _native_process_record(pid, name, root):
    proc = PROC / str(pid)
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    argv = [os.fsdecode(arg) for arg in (proc / 'cmdline').read_bytes().split(b'\0') if arg]
    executable = os.readlink(proc / 'exe')
    identity = (proc / 'exe').stat()
    if (not argv or _bench_cgroup(pid, name) != root
            or not executable.startswith('/') or executable.endswith(' (deleted)')
            or not stat.S_ISREG(identity.st_mode)):
        raise ValueError('identidade de processo incerta')
    return {'argv': argv, 'parent': int(fields[1]), 'starttime': int(fields[19]),
            'executable': Path(executable).name,
            'exe_identity': (identity.st_dev, identity.st_ino)}


def native_work_snapshot(name):
    """Conservatively retain native work, including minimized or windowless jobs.

    This inspects processes, not window titles or a claim about unsaved buffers.
    Anything beyond the exact bench infrastructure/verified Chromium tree keeps
    the session alive. Read failures and races are uncertainty, never emptiness.
    """
    try:
        info = request(name, {'action': 'status'}, timeout=2)
        server = int(info['server_pid'])
        if info.get('name') != name:
            raise ValueError('status de outra bancada')
        root = _bench_cgroup(server, name)
        pids = _cgroup_pids(root)
        if server not in pids:
            raise ValueError('Xvnc não pertence ao inventário')
        records = {pid: _native_process_record(pid, name, root) for pid in pids}
        if records[server]['executable'] != 'Xvnc':
            raise ValueError('servidor não é Xvnc')

        _, state = paths(name)
        browser = profile_process(state / 'chromium')
        if browser and browser.get('bench') != name:
            raise ValueError('perfil de Chromium fora da bancada')
        browser_pids = {browser['pid']} if browser else set()
        if browser_pids and not browser_pids <= pids:
            raise ValueError('Chromium mudou durante o inventário')
        if browser and records[browser['pid']]['executable'] != 'chromium':
            raise ValueError('executável do perfil não é Chromium')
        # Children may appear before their parent in a cgroup.procs listing.
        while True:
            children = {pid for pid, row in records.items() if row['parent'] in browser_pids}
            if children <= browser_pids:
                break
            browser_pids |= children

        busy = []
        for pid, row in records.items():
            argv = row['argv']
            command = row['executable']
            central = (command.startswith('python') and argv[1:] ==
                       [str(BASE / 'desktop/welcome.py'), name, str(state)])
            controller = (command.startswith('python') and argv[1:] ==
                          [str(BASE / 'bin/agent-bench'), '_serve', name])
            bus_runner = (command == 'dbus-run-session' and argv[1:] ==
                          ['--', str(BASE / 'bin/agent-bench'), '_serve', name])
            bus = command == 'dbus-daemon' and '--session' in argv and '--nofork' in argv
            wm = command == 'openbox' and argv[1:] == ['--config-file', str(BASE / 'desktop/openbox.xml')]
            browser_process = (pid in browser_pids and
                               row['exe_identity'] == records[browser['pid']]['exe_identity'])
            if not (pid == server or browser_process or central or controller or bus_runner or bus or wm):
                # Only executable names are returned; command arguments may
                # contain document paths, private task content or credentials.
                busy.append({'pid': pid, 'executable': command})
        if _cgroup_pids(root) != pids:
            raise ValueError('inventário mudou durante a leitura')
        if any(_native_process_record(pid, name, root) != row for pid, row in records.items()):
            raise ValueError('identidade mudou durante a leitura')
        if busy:
            return {'status': 'busy', 'reason': 'aplicativo ou processo nativo presente', 'processes': busy}
        return {'status': 'idle', 'reason': 'somente infraestrutura reconhecida', 'processes': []}
    except (OSError, RuntimeError, ValueError, KeyError, IndexError, TypeError):
        return {'status': 'unknown', 'reason': 'inventário nativo não confirmado', 'processes': []}


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
    seen = last_activity(name)
    now = time.time() if now is None else now
    if seen is None or now - seen < IDLE_SECONDS:
        return False
    # Leftover tabs and apps no longer pin a bench: only a connected CDP client
    # or real CPU work counts as use once the agent stopped sending commands.
    if cdp_client_connected(name):
        return False
    return not bench_cpu_busy(name)


_cpu_samples = {}
# The bench itself and its browser; everything else in the cgroup is native work.
# Names are /proc/PID/comm (15 chars), readable even for the setuid fusermount3
# whose unreadable exe made the old inventory "unknown" on every bench.
BENCH_INFRA = frozenset({'Xvnc', 'openbox', 'dbus-daemon', 'dbus-run-sessio', 'fusermount3',
                         'gvfsd', 'gvfsd-fuse', 'at-spi-bus-laun', 'at-spi2-registr',
                         'xdg-desktop-por', 'xdg-document-po', 'xdg-permission-',
                         'chromium', 'chrome', 'chrome_crashpad',
                         # Electron apps are Chromium too: an idle UI left open
                         # burned up to 30% CPU (DailyWork review benches, 23/09).
                         'electron'})


def _native_cpu_ticks(name):
    """CPU ticks per native process: leftover browser tabs burning CPU are not work."""
    info = request(name, {'action': 'status'}, timeout=2)
    root = _bench_cgroup(int(info['server_pid']), name)
    ticks = {}
    for pid in _cgroup_pids(root):
        proc = PROC / str(pid)
        try:
            comm = (proc / 'comm').read_text().strip()
            command = b' '.join(_process_args(proc))
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        except (OSError, IndexError):
            continue
        if comm in BENCH_INFRA or b'bin/agent-bench' in command or b'desktop/welcome.py' in command:
            continue
        ticks[pid] = int(fields[11]) + int(fields[12])
    return ticks


def sample_cpu(name, now=None):
    now = time.monotonic() if now is None else now
    ticks = _native_cpu_ticks(name)
    samples = [s for s in _cpu_samples.get(name, []) if now - s[0] <= IDLE_CPU_WINDOW + 180]
    samples.append((now, ticks))
    _cpu_samples[name] = samples


def bench_cpu_busy(name, now=None):
    """Busy unless the reaper watched a full window of little native CPU use."""
    now = time.monotonic() if now is None else now
    samples = _cpu_samples.get(name) or []
    old = [s for s in samples if now - s[0] >= IDLE_CPU_WINDOW]
    if not old:
        return True
    before, after = old[-1][1], samples[-1][1]
    used = sum(max(0, ticks - before.get(pid, 0)) for pid, ticks in after.items())
    return used / os.sysconf('SC_CLK_TCK') > IDLE_CPU_SECONDS


def cdp_client_connected(name):
    """A client holding a connection to the bench's CDP port is using the browser."""
    _, state = paths(name)
    try:
        port = int((state / 'chromium/DevToolsActivePort').read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    for table in ('tcp', 'tcp6'):
        try:
            rows = (PROC / 'net' / table).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            try:
                if int(fields[1].split(':')[1], 16) == port and fields[3] == '01':
                    return True
            except (IndexError, ValueError):
                continue
    return False


def close_browser(name, timeout=10):
    """Unit ExecStop: end Chromium before systemd kills Xvnc under it.

    A SIGTERM lets Chromium flush cookies and exit cleanly; dying with its X
    server lost recent cookies and marked every profile as crashed. Ephemeral
    profiles are deleted afterwards.
    """
    _, state = paths(name)
    profile = state / 'chromium'
    try:
        owner = profile_process(profile)
    except RuntimeError:
        owner = None
    if owner and owner.get('bench') == name:
        try:
            os.kill(owner['pid'], signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if (PROC / str(owner['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                    break
            except (OSError, IndexError):
                break
            time.sleep(.1)
    removed = False
    if (profile / EPHEMERAL_MARK).exists() and not _main_chromium_pids(profile):
        shutil.rmtree(profile, ignore_errors=True)
        removed = True
    return {'closed': bool(owner), 'profile_removed': removed}


def reap_idle():
    stopped = []
    for sock in RUNTIME.glob('*/control.sock'):
        name = sock.parent.name
        try:
            sample_cpu(name)
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            _cpu_samples.pop(name, None)
            print('agent-bench reap cpu:', name, exc, flush=True)
        try:
            if not should_reap(name):
                continue
            stop(name)
            stopped.append(name)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print('agent-bench reap:', name, exc, flush=True)
    return stopped
