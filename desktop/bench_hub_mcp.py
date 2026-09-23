"""Multiplexed MCP: ensure + CDP + CUA routed to a named bench."""
import argparse
import json
import math
import os
import signal
import sys
import threading
import time

from bench_control import RUNTIME, control, rpc
from bench_transport import CuaChannel
from bench_catalog import CatalogError, load_cua_tools
from bench_native import guard_request, recheck_request, filter_response, available_tools, normalize_request
from bench_web import WEB_TOOL, run as web_run
from bench_ops import (cdp_snapshot, ensure, gc_sessions, infer_owner, load_owner,
                       paths, request, wait_cdp, launch_cua, stop_cua, stop)

READ_ONLY = frozenset(('list_apps', 'list_windows', 'get_window_state', 'verify_state',
    'clipboard_read', 'get_screen_size', 'get_desktop_state', 'get_cursor_position',
    'get_agent_cursor_state', 'check_permissions', 'health_report', 'get_config',
    'get_accessibility_tree', 'get_browser_state', 'get_recording_state',
    'get_session', 'list_sessions', 'get_session_state', 'bench_list', 'bench_doctor',
    'bench_cdp', 'bench_clipboard_get', 'bench_gc'))

HUB_TOOLS = [
    WEB_TOOL,
    {'name': 'bench_ensure', 'description':
     'Garante a bancada agent-bench (sobe se preciso, devolve viewer, opcionalmente abre Chromium). Toda navegação visual nesta máquina usa uma bancada, workspaces 6–11.',
     'inputSchema': {'type': 'object', 'properties': {
         'bench': {'type': 'string', 'description': 'Nome da bancada (minúsculas, hífen).'},
         'browser': {'type': 'boolean', 'description': 'Abrir o Chromium persistente previamente preparado e exclusivo desta bancada.'},
         'url': {'type': 'string', 'description': 'URL para abrir numa aba própria.'},
         'isolated': {'type': 'boolean', 'description': 'Compatibilidade legada; perfis vazios não são criados automaticamente.'}},
         'required': ['bench']}},
    {'name': 'bench_list', 'description': 'Lista bancadas que respondem ao status, workspace atribuído quando disponível, controle reportado e rótulos de atores. Somente leitura; os rótulos não reservam a bancada.',
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


def request_error(msg, code, text):
    if isinstance(msg, dict) and 'id' not in msg:
        print('agent-bench-mcp:', text, file=sys.stderr)
        return None
    return {'jsonrpc': '2.0', 'id': msg.get('id') if isinstance(msg, dict) else None,
            'error': {'code': code, 'message': text}}


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


def checked_clipboard_result(result, action):
    if not isinstance(result, dict) or type(result.get('returncode')) is not int:
        raise RuntimeError(f'CLIPBOARD_RESULT_INVALID: resposta inválida de {action}.')
    if result['returncode'] != 0:
        raise RuntimeError(f'CLIPBOARD_FAILED: {action} retornou código {result["returncode"]}. '
                           'Confira o estado e o log da bancada antes de repetir a operação.')
    if action == 'clipboard-get' and not isinstance(result.get('stdout'), str):
        raise RuntimeError('CLIPBOARD_RESULT_INVALID: texto ausente ou inválido de clipboard-get.')
    return result


def handle_hub(name, arguments):
    args = arguments or {}
    if name == 'bench_web':
        return web_run(args)
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
        result = checked_clipboard_result(request(bench, {'action': 'clipboard-get'}), 'clipboard-get')
        return {'text': result['stdout']}
    if name == 'bench_clipboard_set':
        ensure(bench, owner=owner)
        with control(bench):
            result = request(bench, {'action': 'clipboard-set', 'input': args.get('text', '')})
        checked_clipboard_result(result, 'clipboard-set')
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
        stop(bench)
        return {'stopped': bench}
    if name == 'bench_gc':
        return gc_sessions(days=args.get('days'), apply=bool(args.get('apply')))
    raise ValueError('Ferramenta desconhecida: ' + name)


class CuaPool:
    def __init__(self, init_timeout=15, call_timeout=60):
        if not 0 < init_timeout <= 300 or not 0 < call_timeout <= 300:
            raise ValueError('prazos CUA devem estar entre 0 e 300 segundos')
        self.init_timeout = init_timeout
        self.call_timeout = call_timeout
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
        stop_cua(entry['proc'])

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
        proc = launch_cua(name, env)
        try:
            tools, channel = self._initialize(proc, name)
        except Exception:
            stop_cua(proc)
            raise
        entry = {'proc': proc, 'tools': tools, 'channel': channel, 'io': threading.Lock()}
        with self.lock:
            previous = self.drivers.pop(name, None)
            self.drivers[name] = entry
        if previous:
            stop_cua(previous['proc'])
        return entry

    def _initialize(self, proc, name):
        channel = CuaChannel(proc)
        deadline = time.monotonic() + self.init_timeout
        init_id = f'init-{name}'
        phase = 'initialize'
        try:
            channel.send({
                'jsonrpc': '2.0', 'id': init_id, 'method': 'initialize',
                'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                           'clientInfo': {'name': 'agent-bench-mcp', 'version': '1'}}}, deadline)
            initialized = channel.receive(init_id, deadline)
            if 'error' in initialized:
                raise RuntimeError('cua-driver recusou initialize')
            phase = 'tools/list'
            channel.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, deadline)
            list_id = f'list-{name}'
            channel.send({'jsonrpc': '2.0', 'id': list_id, 'method': 'tools/list'}, deadline)
            listed = channel.receive(list_id, deadline)
            if 'error' in listed:
                raise RuntimeError('cua-driver recusou tools/list')
            return listed.get('result', {}).get('tools', []), channel
        except TimeoutError as exc:
            raise RuntimeError(f'CUA_INIT_TIMEOUT: {phase} não respondeu em {self.init_timeout}s; '
                               'nenhuma ação da tarefa foi enviada.') from exc

    def call(self, name, msg):
        msg = normalize_request(msg)
        guard = guard_request(name, msg)
        entry = self.driver(name)
        with entry['io']:
            recheck_request(guard)
            deadline = time.monotonic() + self.call_timeout
            try:
                entry['channel'].send(msg, deadline)
                reply = entry['channel'].receive(msg.get('id'), deadline)
            except (OSError, RuntimeError, ValueError) as exc:
                with self.lock:
                    if self.drivers.get(name) is entry:
                        del self.drivers[name]
                try:
                    stop_cua(entry['proc'])
                except Exception as cleanup:
                    print('agent-bench-mcp cleanup:', cleanup, file=sys.stderr)
                    cleanup_note = ' Encerramento da unidade não foi confirmado.'
                else:
                    cleanup_note = ''
                tool = msg.get('params', {}).get('name')
                mutation = msg.get('method') == 'tools/call' and tool not in READ_ONLY
                if mutation:
                    raise RuntimeError('CUA_RESULTADO_INCERTO: falha no transporte após tentar enviar a ação; '
                                       'ela pode ter sido executada. Observe o estado antes de decidir o próximo '
                                       'passo; não repita automaticamente.' + cleanup_note) from exc
                code = 'CUA_TIMEOUT' if isinstance(exc, TimeoutError) else 'CUA_TRANSPORTE_FALHOU'
                raise RuntimeError(f'{code}: leitura não retornou uma resposta completa; '
                                   'driver removido do pool, sem repetição automática.' + cleanup_note) from exc
            return filter_response(name, msg, reply, guard)


def list_benches_simple():
    found = []
    for sock in sorted(RUNTIME.glob('*/control.sock')):
        item = sock.parent.name
        try:
            status = request(item, {'action': 'status'}, timeout=1)
        except (OSError, ValueError, RuntimeError, TypeError):
            continue
        if not isinstance(status, dict):
            continue
        record = {k: status.get(k) for k in ('name', 'display', 'geometry', 'control_mode')}
        owner = load_owner(item)
        record['owner'] = owner.get('owner')
        try:
            view = rpc(RUNTIME / 'views.sock', {'action': 'status', 'name': item}, timeout=1)
        except (OSError, ValueError, RuntimeError, TypeError):
            view = None
        workspace = view.get('workspace') if isinstance(view, dict) else None
        record['workspace'] = workspace if type(workspace) is int else None
        version, origin = owner.get('metadata_version'), owner.get('owner_origin')
        actor, seen = owner.get('last_actor'), owner.get('last_seen_at')
        record['metadata_version'] = version if type(version) is int and version == 2 else None
        record['owner_origin'] = origin if origin in ('legacy_label', 'first_observed_actor') else None
        record['last_actor'] = actor if isinstance(actor, str) and actor.strip() else None
        record['last_seen_at'] = (seen if type(seen) in (int, float) and seen >= 0
                                  and (type(seen) is int or math.isfinite(seen)) else None)
        found.append(record)
    return found


class ToolSelectionError(ValueError):
    pass


class Hub:
    def __init__(self, tools=None):
        self.cua_names = set()
        self.allowed_tools = None
        if tools is not None:
            if (not isinstance(tools, (list, tuple)) or not tools
                    or any(not isinstance(name, str) or not name.strip() for name in tools)):
                raise ToolSelectionError('TOOLS_CONFIG_INVALID: --tools exige uma lista não vazia de nomes.')
            self.allowed_tools = frozenset(tools)
            try:
                self.tools()  # Validate configuration before any pool/bench use.
            except CatalogError as exc:
                raise ToolSelectionError(f'TOOLS_CONFIG_INVALID: {exc}') from exc
        self.cua = CuaPool()

    def tools(self):
        hub_names = {tool['name'] for tool in HUB_TOOLS}
        hub_only = self.allowed_tools is not None and self.allowed_tools <= hub_names
        native = [] if hub_only else available_tools(load_cua_tools())
        tools = [inject_bench_schema(tool) for tool in native]
        catalog = [*HUB_TOOLS, *tools]
        if self.allowed_tools is not None:
            missing = self.allowed_tools - {tool['name'] for tool in catalog}
            if missing:
                raise CatalogError('catalog_selection_invalid',
                                   'ferramentas desconhecidas ou indisponíveis: ' + ', '.join(sorted(missing)))
            catalog = [tool for tool in catalog if tool['name'] in self.allowed_tools]
        self.cua_names = {tool['name'] for tool in catalog if tool['name'] not in hub_names}
        return catalog

    def handle(self, msg):
        if not isinstance(msg, dict):
            return request_error(msg, -32600, 'NATIVE_REQUEST_INVALID: pedido MCP deve ser objeto JSON.')
        method = msg.get('method')
        msg_id = msg.get('id')
        if method == 'tools/call' and 'id' not in msg:
            return request_error(msg, -32600, 'NATIVE_REQUEST_INVALID: tools/call exige id para confirmar conclusão sob o gate.')
        params = msg.get('params', {})
        if not isinstance(params, dict):
            return request_error(msg, -32602, 'NATIVE_REQUEST_INVALID: params deve ser objeto JSON.')
        if method == 'initialize':
            return jsonrpc_result(msg_id, {
                'protocolVersion': params.get('protocolVersion', '2024-11-05'),
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'agent-bench', 'version': '1'}})
        if method == 'notifications/initialized' or method is None:
            return None
        if method == 'tools/list':
            try:
                return jsonrpc_result(msg_id, {'tools': self.tools()})
            except CatalogError as exc:
                return {'jsonrpc': '2.0', 'id': msg_id,
                        'error': {'code': -32603, 'message': str(exc)}}
        if method == 'tools/call':
            tool = params.get('name')
            arguments = params.get('arguments')
            try:
                if self.allowed_tools is not None and tool not in self.allowed_tools:
                    return jsonrpc_error_result(msg_id, 'TOOL_NOT_ALLOWED: ferramenta fora da lista --tools deste hub.')
                if tool in {t['name'] for t in HUB_TOOLS}:
                    payload = public_status(handle_hub(tool, dict(arguments or {})))
                    return jsonrpc_result(msg_id, as_text(payload))
                bench = 'padrao'
                if isinstance(arguments, dict):
                    arguments = dict(arguments)
                    bench = arguments.pop('bench', bench)
                ensure(bench, owner=infer_owner())
                native_params = {'name': tool}
                if 'arguments' in params:
                    native_params['arguments'] = arguments
                forwarded = {'jsonrpc': '2.0', 'id': msg_id, 'method': 'tools/call',
                             'params': native_params}
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


def run(tools=None):
    hub = Hub(tools)
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
                reply = request_error(None, -32700, 'NATIVE_REQUEST_INVALID: JSON inválido.')
            else:
                reply = hub.handle(msg)
            if reply is not None:
                sys.stdout.write(json.dumps(reply) + '\n')
                sys.stdout.flush()
    finally:
        hub.cua.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='MCP das bancadas agent-bench.')
    parser.add_argument('--tools', nargs='+', metavar='NAME',
                        help='Lista explícita de ferramentas; omitida, mantém o catálogo completo.')
    args = parser.parse_args(argv)
    try:
        return run(args.tools)
    except ToolSelectionError as exc:
        parser.error(str(exc))
