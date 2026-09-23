"""Verify the existing bench endpoints before launching a native CUA process."""
import os
from pathlib import Path
import re
import socket
import stat
import struct
from urllib.parse import unquote_to_bytes

import bench_ops as ops

IDENTITY_KEYS = ('AGENT_BENCH_NAME', 'DISPLAY', 'XAUTHORITY',
                 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR')
X11_CONTEXT = {'XDG_SESSION_TYPE': 'x11', 'GDK_BACKEND': 'x11',
               'QT_QPA_PLATFORM': 'xcb', 'GTK_USE_PORTAL': '0'}


class CuaEnvironmentError(RuntimeError):
    pass


def _bus_path(address):
    # Only the unix:path[,...guid] format emitted by our dbus-run-session.
    # Never interpret autolaunch, executable transports or fallback lists.
    if not address.startswith('unix:') or ';' in address:
        raise ValueError('barramento deve ter um único endereço unix:path')
    values = {}
    for item in address[5:].split(','):
        key, raw = item.split('=', 1)
        if key not in ('path', 'guid') or key in values or not raw:
            raise ValueError('parâmetros de barramento não confirmados')
        if not re.fullmatch(r'(?:[-0-9A-Za-z_/.*]|%[0-9a-fA-F]{2})+', raw):
            raise ValueError('escape inválido no endereço do barramento')
        values[key] = os.fsdecode(unquote_to_bytes(raw))
    path = values.get('path', '')
    if (not path.startswith('/') or '\0' in path or Path(path).as_posix() != path
            or '..' in Path(path).parts):
        raise ValueError('caminho do barramento inválido')
    if 'guid' in values and not re.fullmatch(r'[0-9a-fA-F]{32}', values['guid']):
        raise ValueError('GUID do barramento inválido')
    return path


def _owned(path, kind):
    info = path.lstat()
    if (info.st_uid != os.getuid() or not kind(info.st_mode)
            or info.st_mode & 0o022):
        raise ValueError('caminho privado ausente, substituído ou com dono/permissões inesperados')


def _record(pid, name, root):
    if type(pid) is not int or pid <= 0 or (ops.PROC / str(pid)).stat().st_uid != os.getuid():
        raise ValueError('identidade do processo não confirmada')
    return ops._native_process_record(pid, name, root)


def validate_cua_env(name, env):
    """Read only: no start, viewer, recovery, D-Bus messages or global fallback."""
    try:
        ops.valid(name)
        for key in (*IDENTITY_KEYS, *X11_CONTEXT):
            value = env.get(key)
            if not isinstance(value, str) or not value.strip() or '\0' in value:
                raise ValueError(f'{key} ausente ou vazio')
        if env['AGENT_BENCH_NAME'] != name or any(env[key] != value for key, value in X11_CONTEXT.items()):
            raise ValueError('nome ou contexto X11 não corresponde à bancada')
        runtime, _ = ops.paths(name)
        if (env['XDG_RUNTIME_DIR'] != str(runtime / 'run')
                or env['XAUTHORITY'] != str(runtime / 'Xauthority')
                or not re.fullmatch(r':[0-9]+', env['DISPLAY'])):
            raise ValueError('display ou caminhos não correspondem ao contrato da bancada')
        bus_path = _bus_path(env['DBUS_SESSION_BUS_ADDRESS'])
        for directory in (runtime, runtime / 'run'):
            _owned(directory, stat.S_ISDIR)
        _owned(runtime / 'Xauthority', stat.S_ISREG)

        info = ops.request(name, {'action': 'status'}, timeout=2)
        if (info.get('name') != name or info.get('display') != env['DISPLAY']
                or any(info.get('env', {}).get(key) != env[key]
                       for key in (*IDENTITY_KEYS, *X11_CONTEXT))):
            raise ValueError('ambiente diverge do status atual da bancada')
        server = info['server_pid']
        if type(server) is not int or server <= 0:
            raise ValueError('PID do servidor inválido')
        root = ops._bench_cgroup(server, name)
        original = _record(server, name, root)
        argv = original['argv']
        if (original['executable'] not in ('Xvnc', 'Xtigervnc')
                or len(argv) < 2 or argv[1] != env['DISPLAY']
                or argv.count('-auth') != 1
                or argv[argv.index('-auth') + 1] != env['XAUTHORITY']
                or server not in ops._cgroup_pids(root)):
            raise ValueError('servidor Xvnc/display/autorização não confirmado')

        # No D-Bus authentication, method or activation: only kernel peer identity.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(1)
            peer.connect(bus_path)
            pid, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                raise ValueError('barramento pertence a outro usuário')
            bus = _record(pid, name, root)
            members = ops._cgroup_pids(root)
            if pid not in members or server not in members:
                raise ValueError('servidor ou barramento fora do cgroup da bancada')
            if _record(pid, name, root) != bus or _record(server, name, root) != original:
                raise ValueError('processo da bancada mudou durante a validação')
    except (OSError, RuntimeError, ValueError, KeyError, IndexError, TypeError, AttributeError, struct.error) as exc:
        raise CuaEnvironmentError(
            'CUA_ENV_UNVERIFIED: ambiente privado da bancada não confirmado; '
            f'CUA não será iniciado ({exc}).') from exc
