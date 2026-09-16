"""Shared control gate; all input stays inside the named test display."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import socket

RUNTIME = Path('/run/user') / str(os.getuid()) / 'agent-bench'

def rpc(path, payload, timeout=15):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall(json.dumps(payload).encode() + b'\n')
        with sock.makefile('rb') as stream:
            reply = json.loads(stream.readline(4 * 1024 * 1024))
    if 'error' in reply:
        raise RuntimeError(reply['error'])
    return reply

def views(action, name):
    try:
        if action in ('collaborate', 'resume') and name not in ('--here', 'aqui'):
            info = rpc(RUNTIME / name / 'control.sock', {'action': 'status'}, timeout=2)
            if info.get('protocol_version', 1) < 2:
                raise RuntimeError('Esta bancada já estava em uso antes da atualização. O proprietário pode encerrá-la e iniciá-la ao terminar para habilitar colaboração.')
        return rpc(RUNTIME / 'views.sock', {'action': action, 'name': name})
    except OSError as exc:
        raise RuntimeError('Acompanhamento indisponível. Verifique agent-bench-views.service; sem entrada no desktop humano.') from exc

@contextmanager
def control(name):
    runtime = RUNTIME / name
    with (runtime / 'input.lock').open('a') as lock:
        # An open file description holds the lease until the command/MCP reply.
        fcntl.flock(lock, fcntl.LOCK_SH)
        if (runtime / 'human-control').exists():
            raise RuntimeError('O humano assumiu esta bancada. Aguarde ele devolver o controle; não execute resume por conta própria.')
        from bench_ops import touch_activity
        touch_activity(name)
        views('dock', name)
        yield
