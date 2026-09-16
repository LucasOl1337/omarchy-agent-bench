"""Stdio MCP bridge with per-call workspace docking and human handoff gate."""
import json
import os
import signal
import subprocess
import sys
import threading

from bench_control import control

READ_ONLY = frozenset(('list_apps', 'list_windows', 'get_window_state', 'verify_state',
    'clipboard_read', 'get_screen_size', 'get_desktop_state', 'get_cursor_position',
    'get_agent_cursor_state', 'check_permissions', 'health_report', 'get_config',
    'get_accessibility_tree', 'get_browser_state', 'get_recording_state',
    'get_session', 'list_sessions', 'get_session_state'))

def run(name, env):
    proc = subprocess.Popen(['cua-driver', 'mcp', '--direct', '--no-overlay'], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
    pending = {}
    mutex = threading.RLock()
    output_lock = threading.Lock()
    def emit(line):
        with output_lock:
            sys.stdout.write(line)
            sys.stdout.flush()
    def release(key):
        with mutex:
            gate = pending.pop(key, None)
        if gate: gate.__exit__(None, None, None)
    def forward():
        try:
            for line in sys.stdin:
                msg = json.loads(line)
                key = json.dumps(msg.get('id'))
                mutate = msg.get('method') == 'tools/call' and msg.get('params', {}).get('name') not in READ_ONLY
                if mutate:
                    gate = control(name)
                    try:
                        gate.__enter__()
                    except Exception as exc:
                        emit(json.dumps({'jsonrpc': '2.0', 'id': msg.get('id'), 'result': {
                            'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}}) + '\n')
                        continue
                    with mutex: pending[key] = gate
                proc.stdin.write(line)
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            print('agent-bench mcp:', exc, file=sys.stderr)
        finally:
            try: proc.stdin.close()
            except OSError: pass
    def terminate(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    threading.Thread(target=forward, daemon=True).start()
    try:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
                if 'id' in msg and ('result' in msg or 'error' in msg):
                    release(json.dumps(msg['id']))
            except ValueError: pass
            emit(line)
        return proc.wait()
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try: proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        for key in list(pending): release(key)
