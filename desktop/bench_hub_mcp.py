"""Multiplexed MCP: ensure + CDP + CUA routed to a named bench."""
import json
import os
import signal
import subprocess
import sys
import threading

from bench_control import control
from bench_ops import (cdp_snapshot, ensure, gc_sessions, infer_owner, load_owner,
                       paths, request, wait_cdp)

READ_ONLY = frozenset(('list_apps', 'list_windows', 'get_window_state', 'verify_state',
    'clipboard_read', 'get_screen_size', 'get_desktop_state', 'get_cursor_position',
    'get_agent_cursor_state', 'check_permissions', 'health_report', 'get_config',
    'get_accessibility_tree', 'get_browser_state', 'get_recording_state',
    'get_session', 'list_sessions', 'get_session_state', 'bench_list', 'bench_doctor',
    'bench_cdp', 'bench_clipboard_get', 'bench_gc'))

HUB_TOOLS = [
    {'name': 'bench_ensure', 'description':
     'Garante a bancada agent-bench (sobe se preciso, devolve viewer, opcionalmente abre Chromium). Toda navegação visual nesta máquina usa uma bancada, workspaces 6–11.',
     'inputSchema': {'type': 'object', 'properties': {
         'bench': {'type': 'string', 'description': 'Nome da bancada (minúsculas, hífen).'},
         'browser': {'type': 'boolean', 'description': 'Abrir o Chromium persistente previamente preparado e exclusivo desta bancada.'},
         'url': {'type': 'string', 'description': 'URL para abrir numa aba própria.'},
         'isolated': {'type': 'boolean', 'description': 'Compatibilidade legada; perfis vazios não são criados automaticamente.'}},
         'required': ['bench']}},
    {'name': 'bench_list', 'description': 'Lista bancadas ativas, workspace, controle e dono.',
     'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'bench_doctor', 'description': 'Diagnóstico da bancada: display, workspace 6–11, CDP, controle.',
     'inputSchema': {'type': 'object', 'properties': {'bench': {'type': 'string'}}, 'required': ['bench']}},
    {'name': 'bench_cdp', 'description':
     'Endpoint CDP exclusivo do Chromium persistente desta bancada, com processo e diretório validados. Use Playwright connectOverCDP neste URL só em abas suas.',
     'inputSchema': {'type': 'object', 'properties': {
         'bench': {'type': 'string'}, 'url': {'type': 'string'}, 'isolated': {'type': 'boolean'}}, 'required': ['bench']}},
    {'name': 'bench_browser', 'description': 'Abre o Chromium persistente previamente preparado e exclusivo da bancada. Nunca reutiliza a origem ou outro display.',
     'inputSchema': {'type': 'object', 'properties': {
         'bench': {'type': 'string'}, 'url': {'type': 'string'}, 'isolated': {'type': 'boolean'}}, 'required': ['bench']}},
    {'name': 'bench_screenshot', 'description': 'Captura a tela da bancada para um PNG absoluto.',
     'inputSchema': {'type': 'object', 'properties': {
         'bench': {'type': 'string'}, 'path': {'type': 'string'}}, 'required': ['bench', 'path']}},
    {'name': 'bench_clipboard_get', 'description': 'Lê o clipboard isolado da bancada.',
     'inputSchema': {'type': 'object', 'properties': {'bench': {'type': 'string'}}, 'required': ['bench']}},
    {'name': 'bench_clipboard_set', 'description': 'Escreve no clipboard isolado da bancada.',
     'inputSchema': {'type': 'object', 'properties': {
         'bench': {'type': 'string'}, 'text': {'type': 'string'}}, 'required': ['bench', 'text']}},
    {'name': 'bench_exec', 'description': 'Executa um comando curto no display da bancada (máx. 60s).',
     'inputSchema': {'type': 'object', 'properties': {
         'bench': {'type': 'string'},
         'argv': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['bench', 'argv']}},
    {'name': 'bench_launch', 'description': 'Abre um aplicativo duradouro dentro da bancada.',
     'inputSchema': {'type': 'object', 'properties': {
         'bench': {'type': 'string'},
         'argv': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['bench', 'argv']}},
    {'name': 'bench_stop', 'description': 'Encerra a bancada. Perfil e cookies permanecem em disco. Não encerre padrao sem pedido.',
     'inputSchema': {'type': 'object', 'properties': {'bench': {'type': 'string'}}, 'required': ['bench']}},
    {'name': 'bench_gc', 'description': 'Lista ou remove sessões de bancada expiradas. Respeita .keep e bancadas ativas.',
     'inputSchema': {'type': 'object', 'properties': {
         'apply': {'type': 'boolean'}, 'days': {'type': 'integer'}}}},
]


NON_STANDARD_FORMATS = {'uint8', 'uint16', 'uint32', 'uint64', 'int8', 'int16', 'int32', 'int64',
                        'float', 'double', 'usize', 'isize'}


def strip_non_standard_formats(node):
    """Remove `format: uint32` e afins (schemars) que fazem OpenCode/ajv logar
    'unknown format ... ignored in schema' 88 vezes por boot. Recursivo, sem mudar o resto."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == 'format' and isinstance(value, str) and value in NON_STANDARD_FORMATS:
                continue
            out[key] = strip_non_standard_formats(value)
        return out
    if isinstance(node, list):
        return [strip_non_standard_formats(item) for item in node]
    return node


def inject_bench_schema(tool):
    schema = strip_non_standard_formats(dict(tool.get('inputSchema') or {'type': 'object', 'properties': {}}))
    props = dict(schema.get('properties') or {})
    if 'bench' not in props:
        props['bench'] = {'type': 'string',
                           'description': 'Nome da bancada. Padrão: padrao. Ensure automático na primeira chamada.'}
        schema['properties'] = props
    updated = dict(tool)
    schema_copy = dict(schema)
    updated['inputSchema'] = schema_copy
    if 'outputSchema' in updated:
        updated['outputSchema'] = strip_non_standard_formats(updated['outputSchema'])
    return updated


def jsonrpc_result(msg_id, result):
    return {'jsonrpc': '2.0', 'id': msg_id, 'result': result}


def jsonrpc_error_result(msg_id, text):
    return {'jsonrpc': '2.0', 'id': msg_id, 'result': {
        'isError': True, 'content': [{'type': 'text', 'text': str(text)}]}}


def public_status(payload):
    if not isinstance(payload, dict):
        return payload
    return {k: v for k, v in payload.items() if k != 'env'}


def as_text(payload):
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        text = str(payload)
    return {'content': [{'type': 'text', 'text': text}]}
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        text = str(payload)
    return {'content': [{'type': 'text', 'text': text}]}


def handle_hub(name, arguments):
    args = arguments or {}
    bench = args.get('bench', 'padrao')
    owner = args.get('owner') or infer_owner()
    if name == 'bench_ensure':
        return ensure(bench, owner=owner, browser=bool(args.get('browser') or args.get('url')),
                      url=args.get('url'), isolated=bool(args.get('isolated')))
    if name == 'bench_list':
        return list_benches_simple()
    if name == 'bench_doctor':
        info = ensure(bench, owner=owner)
        from toolkit import inspect
        from bench_control import views
        try:
            view = views('status', bench)
        except RuntimeError:
            view = {}
        return inspect(bench, {**info, 'state': info.get('state', str(paths(bench)[1]))}, view)
    if name == 'bench_cdp':
        info = ensure(bench, owner=owner, browser=True, url=args.get('url'), isolated=bool(args.get('isolated')))
        snap = info.get('cdp') or wait_cdp(info.get('state', paths(bench)[1]))
        if snap.get('status') != 'conectado':
            raise RuntimeError('Chromium sem CDP. Tente bench_browser.')
        return snap
    if name == 'bench_browser':
        return ensure(bench, owner=owner, browser=True, url=args.get('url'), isolated=bool(args.get('isolated')))
    if name == 'bench_screenshot':
        info = ensure(bench, owner=owner)
        from pathlib import Path
        output = Path(args['path']).resolve()
        with control(bench):
            result = request(bench, {'action': 'screenshot', 'output': str(output)})
        if result.get('returncode'):
            raise RuntimeError(result.get('stderr') or 'screenshot falhou')
        return {'path': str(output)}
    if name == 'bench_clipboard_get':
        ensure(bench, owner=owner)
        result = request(bench, {'action': 'clipboard-get'})
        return {'text': result.get('stdout', '')}
    if name == 'bench_clipboard_set':
        ensure(bench, owner=owner)
        with control(bench):
            request(bench, {'action': 'clipboard-set', 'input': args.get('text', '')})
        return {'ok': True}
    if name == 'bench_exec':
        ensure(bench, owner=owner)
        argv = args.get('argv')
        if not isinstance(argv, list) or not argv:
            raise ValueError('argv inválido')
        with control(bench):
            return request(bench, {'action': 'exec', 'argv': argv, 'cwd': str(__import__('pathlib').Path.cwd())})
    if name == 'bench_launch':
        ensure(bench, owner=owner)
        argv = args.get('argv')
        if not isinstance(argv, list) or not argv:
            raise ValueError('argv inválido')
        with control(bench):
            return request(bench, {'action': 'launch', 'argv': argv, 'cwd': str(__import__('pathlib').Path.cwd())})
    if name == 'bench_stop':
        if bench == 'padrao':
            raise RuntimeError('padrao sobe no login; só encerre se o humano pedir.')
        subprocess.run(['systemctl', '--user', 'stop', f'agent-bench@{bench}.service'], check=True)
        return {'stopped': bench}
    if name == 'bench_gc':
        return gc_sessions(days=args.get('days'), apply=bool(args.get('apply')))
    raise ValueError('Ferramenta desconhecida: ' + name)


class CuaPool:
    def __init__(self):
        self.drivers = {}
        self.lock = threading.Lock()

    def close(self):
        with self.lock:
            names = list(self.drivers)
        for name in names:
            self._stop(name)

    def _stop(self, name):
        with self.lock:
            entry = self.drivers.pop(name, None)
        if not entry:
            return
        proc = entry['proc']
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()

    def driver(self, name):
        info = ensure(name, owner=infer_owner())
        with self.lock:
            entry = self.drivers.get(name)
            if entry and entry['proc'].poll() is None:
                return entry
        env = os.environ.copy()
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'XAUTHORITY',
                    'HYPRLAND_INSTANCE_SIGNATURE', 'DBUS_SESSION_BUS_ADDRESS'):
            env.pop(key, None)
        env.update(info.get('env') or {})
        proc = subprocess.Popen(['cua-driver', 'mcp', '--direct', '--no-overlay'], env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                               bufsize=1, start_new_session=True)
        init_id = f'init-{name}'
        proc.stdin.write(json.dumps({
            'jsonrpc': '2.0', 'id': init_id, 'method': 'initialize',
            'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                       'clientInfo': {'name': 'agent-bench-mcp', 'version': '1'}}}) + '\n')
        proc.stdin.flush()
        tools = []
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError('cua-driver encerrou durante initialize')
            msg = json.loads(line)
            if msg.get('id') == init_id:
                proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
                proc.stdin.flush()
                list_id = f'list-{name}'
                proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': list_id, 'method': 'tools/list'}) + '\n')
                proc.stdin.flush()
                while True:
                    listed = json.loads(proc.stdout.readline())
                    if listed.get('id') == list_id:
                        tools = listed.get('result', {}).get('tools', [])
                        break
                break
        entry = {'proc': proc, 'tools': tools, 'io': threading.Lock()}
        with self.lock:
            previous = self.drivers.pop(name, None)
            self.drivers[name] = entry
        if previous and previous['proc'].poll() is None:
            old = previous['proc']
            os.killpg(old.pid, signal.SIGTERM)
            try:
                old.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(old.pid, signal.SIGKILL)
                old.wait()
        return entry

    def call(self, name, msg):
        entry = self.driver(name)
        with entry['io']:
            entry['proc'].stdin.write(json.dumps(msg) + '\n')
            entry['proc'].stdin.flush()
            while True:
                line = entry['proc'].stdout.readline()
                if not line:
                    raise RuntimeError('cua-driver sem resposta')
                reply = json.loads(line)
                if 'id' in reply and reply.get('id') == msg.get('id'):
                    return reply


def list_benches_simple():
    from pathlib import Path
    runtime = Path(f'/run/user/{os.getuid()}/agent-bench')
    found = []
    for sock in sorted(runtime.glob('*/control.sock')):
        item = sock.parent.name
        try:
            status = request(item, {'action': 'status'}, timeout=1)
        except (OSError, ValueError, RuntimeError):
            continue
        record = {k: status.get(k) for k in ('name', 'display', 'geometry', 'control_mode')}
        record['owner'] = load_owner(item).get('owner')
        found.append(record)
    return found


class Hub:
    def __init__(self):
        self.cua = CuaPool()
        self.cua_names = set()

    def tools(self):
        tools = list(HUB_TOOLS)
        try:
            entry = self.cua.driver('padrao')
            for tool in entry['tools']:
                tools.append(inject_bench_schema(tool))
                self.cua_names.add(tool['name'])
        except (OSError, RuntimeError, ValueError, FileNotFoundError) as exc:
            print('agent-bench-mcp cua:', exc, file=sys.stderr)
        return tools

    def handle(self, msg):
        method = msg.get('method')
        msg_id = msg.get('id')
        if method == 'initialize':
            return jsonrpc_result(msg_id, {
                'protocolVersion': msg.get('params', {}).get('protocolVersion', '2024-11-05'),
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'agent-bench', 'version': '1'}})
        if method == 'notifications/initialized' or method is None:
            return None
        if method == 'tools/list':
            return jsonrpc_result(msg_id, {'tools': self.tools()})
        if method == 'tools/call':
            params = msg.get('params') or {}
            tool = params.get('name')
            arguments = dict(params.get('arguments') or {})
            try:
                if tool in {t['name'] for t in HUB_TOOLS}:
                    payload = public_status(handle_hub(tool, arguments))
                    return jsonrpc_result(msg_id, as_text(payload))
                bench = arguments.pop('bench', 'padrao')
                ensure(bench, owner=infer_owner())
                forwarded = {'jsonrpc': '2.0', 'id': msg_id, 'method': 'tools/call',
                             'params': {'name': tool, 'arguments': arguments}}
                if tool not in READ_ONLY:
                    with control(bench):
                        return self.cua.call(bench, forwarded)
                return self.cua.call(bench, forwarded)
            except Exception as exc:
                return jsonrpc_error_result(msg_id, exc)
        if msg_id is not None:
            return {'jsonrpc': '2.0', 'id': msg_id,
                    'error': {'code': -32601, 'message': 'Method not found'}}
        return None


def run():
    hub = Hub()
    def terminate(*_):
        hub.cua.close()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            reply = hub.handle(msg)
            if reply is not None:
                sys.stdout.write(json.dumps(reply) + '\n')
                sys.stdout.flush()
    finally:
        hub.cua.close()
