"""Keep numeric CUA process targets and discovery within one verified bench."""
import json
import os
from pathlib import PurePosixPath
import re
import subprocess

import bench_ops as ops

DISCOVERY = frozenset(('list_apps', 'list_windows', 'get_accessibility_tree'))
BROWSER_UNBOUND_TOOLS = frozenset(('page', 'get_browser_state', 'browser_prepare',
    'browser_navigate', 'browser_click', 'browser_type', 'browser_dialog',
    'browser_set_input_files', 'browser_download', 'browser_pointer'))
NATIVE_UNSUPPORTED_TOOLS = BROWSER_UNBOUND_TOOLS | {'get_accessibility_tree', 'replay_trajectory',
                                                  'set_config', 'launch_app', 'install_extension'}
FOREGROUND_DEFAULT_TOOLS = frozenset(('click', 'double_click', 'right_click', 'drag',
                                    'type_text', 'press_key', 'hotkey', 'scroll'))
DELIVERY_MODE_DESCRIPTION = (
    "Padrão da bancada quando omitido: 'foreground' (usa o foco do DISPLAY próprio). "
    "Valores explícitos são encaminhados sem alteração e validados pelo driver; "
    "'background' continua sujeito às proteções do backend e pode ser recusado. "
    "O gate de controle humano permanece obrigatório.")


def normalize_request(message):
    """Default only omitted delivery modes, without changing the caller's objects."""
    if message.get('method') != 'tools/call':
        return message
    params = message.get('params')
    if not isinstance(params, dict) or params.get('name') not in FOREGROUND_DEFAULT_TOOLS:
        return message
    arguments = params.get('arguments')
    if not isinstance(arguments, dict) or 'delivery_mode' in arguments:
        return message
    return {**message, 'params': {**params, 'arguments': {
        **arguments, 'delivery_mode': 'foreground'}}}


def _delivery_schema(tool):
    if tool['name'] not in FOREGROUND_DEFAULT_TOOLS:
        return tool
    schema = tool.get('inputSchema')
    properties = schema.get('properties') if isinstance(schema, dict) else None
    delivery = properties.get('delivery_mode') if isinstance(properties, dict) else None
    if not isinstance(delivery, dict):
        return tool
    return {**tool, 'inputSchema': {**schema, 'properties': {**properties,
        'delivery_mode': {**delivery, 'default': 'foreground',
                          'description': DELIVERY_MODE_DESCRIPTION}}}}


def available_tools(tool_list):
    """Hide tools whose backend targets cannot yet be bound to this bench."""
    if (not isinstance(tool_list, list) or any(not isinstance(tool, dict)
            or not isinstance(tool.get('name'), str) for tool in tool_list)):
        raise NativeIsolationError('NATIVE_TOOL_CATALOG_UNCERTAIN: catálogo de ferramentas inválido.')
    return [_delivery_schema(tool) for tool in tool_list
            if tool['name'] not in NATIVE_UNSUPPORTED_TOOLS]


class NativeIsolationError(RuntimeError):
    pass


def _pid(value):
    if type(value) is not int or value <= 0:
        raise NativeIsolationError('NATIVE_PID_INVALID: use um PID positivo explícito.')
    return value


def _group(value):
    if not isinstance(value, str) or not value.startswith('/') or '..' in value.split('/'):
        raise NativeIsolationError('NATIVE_PID_UNCERTAIN: cgroup inválido.')
    return PurePosixPath(value)


def _process(pid):
    directory = ops.PROC / str(_pid(pid))
    if directory.stat().st_uid != os.getuid():
        raise NativeIsolationError('NATIVE_PID_OUTSIDE: processo de outro usuário.')
    lines = [line[3:] for line in (directory / 'cgroup').read_text().splitlines() if line.startswith('0::')]
    if len(lines) != 1:
        raise NativeIsolationError('NATIVE_PID_UNCERTAIN: cgroup v2 não confirmado.')
    group = _group(lines[0])
    fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
    return group, int(fields[19])  # /proc stat field 22, starttime (PID reuse)


def _member(pid, group):
    directory = ops.CGROUP.joinpath(*group.parts[1:])
    if not directory.resolve().is_relative_to(ops.CGROUP.resolve()):
        raise NativeIsolationError('NATIVE_PID_UNCERTAIN: raiz de cgroup inválida.')
    return str(pid) in (directory / 'cgroup.procs').read_text().splitlines()


def _unit_properties(unit):
    result = subprocess.run(['systemctl', '--user', 'show', unit,
                             '--property=Id,LoadState,ActiveState,ControlGroup,BindsTo'],
                            env=ops.systemd_env(), capture_output=True, text=True,
                            check=True, timeout=2)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


class NativeGuard:
    def __init__(self, bench):
        self.bench = ops.valid(bench)
        self.units = {}
        self.targets = {}
        try:
            info = ops.request(bench, {'action': 'status'}, timeout=2)
            self.server = _pid(info['server_pid'])
            if info.get('name') != bench:
                raise NativeIsolationError('NATIVE_BENCH_UNCERTAIN: status de outra bancada.')
            group, self.server_start = _process(self.server)
            parts = group.parts
            service = ops.bench_service(bench)
            if parts.count(service) != 1:
                raise NativeIsolationError('NATIVE_BENCH_UNCERTAIN: Xvnc fora da unidade esperada.')
            self.root = PurePosixPath(*parts[:parts.index(service) + 1])
            argv = (ops.PROC / str(self.server) / 'cmdline').read_bytes().split(b'\0')
            if not argv or PurePosixPath(os.fsdecode(argv[0])).name != 'Xvnc' or not _member(self.server, group):
                raise NativeIsolationError('NATIVE_BENCH_UNCERTAIN: servidor Xvnc não confirmado.')
        except NativeIsolationError:
            raise
        except (OSError, RuntimeError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise NativeIsolationError('NATIVE_BENCH_UNCERTAIN: não foi possível validar a bancada.') from exc

    def require(self, pid):
        pid = _pid(pid)
        try:
            before = _process(pid)
            group = before[0]
            if not group.is_relative_to(self.root):
                units = [part for part in group.parts if re.fullmatch(
                    r'agent-bench-cua-' + re.escape(self.bench) + r'-[0-9a-f]{32}\.service', part)]
                if len(units) != 1:
                    raise NativeIsolationError('NATIVE_PID_OUTSIDE: alvo não pertence à bancada solicitada.')
                unit = units[0]
                if unit not in self.units:
                    self.units[unit] = _unit_properties(unit)
                properties = self.units[unit]
                unit_root = PurePosixPath(*group.parts[:group.parts.index(unit) + 1])
                if (properties.get('Id') != unit or properties.get('LoadState') != 'loaded'
                        or properties.get('ActiveState') != 'active'
                        or ops.bench_service(self.bench) not in properties.get('BindsTo', '').split()
                        or _group(properties.get('ControlGroup')) != unit_root):
                    raise NativeIsolationError('NATIVE_PID_OUTSIDE: unidade auxiliar não vinculada à bancada.')
            if not _member(pid, group) or _process(pid) != before:
                raise NativeIsolationError('NATIVE_PID_UNCERTAIN: processo mudou durante a validação.')
            server_group, start = _process(self.server)
            if start != self.server_start or not server_group.is_relative_to(self.root):
                raise NativeIsolationError('NATIVE_BENCH_UNCERTAIN: servidor da bancada mudou.')
            return before
        except NativeIsolationError:
            raise
        except (OSError, RuntimeError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError) as exc:
            raise NativeIsolationError('NATIVE_PID_UNCERTAIN: origem do processo não confirmada.') from exc

    def allows(self, pid):
        try:
            self.require(pid)
            return True
        except NativeIsolationError:
            return False


def guard_request(bench, message):
    """Check explicit PID forms before any request is sent to the native driver."""
    if message.get('method') != 'tools/call':
        return None
    params = message.get('params') or {}
    if not isinstance(params, dict):
        raise NativeIsolationError('NATIVE_REQUEST_INVALID: parâmetros inválidos.')
    arguments = params.get('arguments') or {}
    if not isinstance(arguments, dict):
        raise NativeIsolationError('NATIVE_REQUEST_INVALID: argumentos inválidos.')
    tool = params.get('name')
    if tool == 'launch_app':
        raise NativeIsolationError('NATIVE_LAUNCH_TRANSIENT: launch_app prende o aplicativo à unidade temporária '
                                   'do driver; use bench_launch com argv ou agent-bench launch NOME -- APP ARG... '
                                   'Para navegador, use bench_browser ou agent-bench browser NOME.')
    if tool == 'set_config':
        raise NativeIsolationError('NATIVE_CONFIG_GLOBAL: set_config persiste configuração em HOME compartilhado; '
                                   'use max_dimension por chamada de get_window_state para limitar a imagem, '
                                   'sem elevar o teto configurado. Não há chave efêmera segura em set_config.')
    if tool == 'install_extension':
        raise NativeIsolationError('NATIVE_EXTENSION_GLOBAL: install_extension grava extensão do driver em HOME '
                                   'compartilhado e vale para todas as bancadas; instalação fica com o humano.')
    if tool in BROWSER_UNBOUND_TOOLS:
        raise NativeIsolationError('NATIVE_BROWSER_UNBOUND: ferramenta sem vínculo comprovado com bancada '
                                   'e missão; use bench_web ou bench_cdp com superfície validada.')
    if tool == 'replay_trajectory':
        raise NativeIsolationError('NATIVE_REPLAY_UNBOUND: replay interno contorna a validação por ação; '
                                   'observe o estado e execute ações individuais sob o gate da bancada.')
    if tool == 'get_accessibility_tree':
        raise NativeIsolationError('NATIVE_DISCOVERY_UNSUPPORTED: descoberta global legada sem formato validado; '
                                   'use list_windows e get_window_state dentro da bancada.')
    pids = []
    if 'pid' in arguments:
        pids.append(_pid(arguments['pid']))
    target = arguments.get('target')
    if isinstance(target, dict) and 'pid' in target:
        pids.append(_pid(target['pid']))
    if len(set(pids)) > 1:
        raise NativeIsolationError('NATIVE_PID_AMBIGUOUS: pid e target.pid divergem.')
    if tool == 'kill_app' and 'pid' not in arguments:
        raise NativeIsolationError('NATIVE_PID_INVALID: kill_app exige um PID positivo explícito.')
    if not pids and params.get('name') not in DISCOVERY:
        return None
    guard = NativeGuard(bench)
    for pid in set(pids):
        guard.targets[pid] = guard.require(pid)
    return guard


def recheck_request(guard):
    """Keep the original identity across driver startup and input-lock waits."""
    if guard is None:
        return
    guard.units.clear()  # BindsTo/ControlGroup must be observed again at dispatch.
    try:
        group, start = _process(guard.server)
        if start != guard.server_start or not group.is_relative_to(guard.root) or not _member(guard.server, group):
            raise NativeIsolationError('NATIVE_BENCH_UNCERTAIN: servidor mudou antes do envio.')
        for pid, expected in guard.targets.items():
            if guard.require(pid) != expected:
                raise NativeIsolationError('NATIVE_PID_CHANGED: PID foi reutilizado ou movido antes do envio.')
    except NativeIsolationError:
        raise
    except (OSError, RuntimeError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise NativeIsolationError('NATIVE_PID_UNCERTAIN: identidade não confirmada antes do envio.') from exc


def _payload(result):
    payload = result.get('structuredContent')
    if payload is None:
        content = result.get('content', [])
        if len(content) != 1 or content[0].get('type') != 'text':
            raise ValueError('no structured discovery payload')
        payload = json.loads(content[0]['text'])
    if not isinstance(payload, dict):
        raise ValueError('discovery payload is not an object')
    return payload


APP_FIELDS = frozenset(('pid', 'name', 'running', 'active', 'bundle_id', 'kind', 'launch_path', 'last_used'))
WINDOW_FIELDS = frozenset(('pid', 'window_id', 'app_name', 'title', 'bounds', 'is_on_screen',
                          'z_index', 'minimized', 'layer', 'current_space_id', 'on_current_space',
                          'space_ids', 'x', 'y', 'width', 'height'))


def filter_response(bench, message, reply, guard=None):
    if message.get('method') == 'tools/list':
        if 'error' in reply:
            return reply
        try:
            result = reply['result']
            return {**reply, 'result': {**result, 'tools': available_tools(result['tools'])}}
        except (KeyError, TypeError) as exc:
            raise NativeIsolationError('NATIVE_TOOL_CATALOG_UNCERTAIN: catálogo de ferramentas inválido.') from exc
    tool = (message.get('params') or {}).get('name')
    if message.get('method') != 'tools/call' or tool not in DISCOVERY:
        return reply
    guard = guard or NativeGuard(bench)
    try:
        result = reply['result']
        if result.get('isError'):
            raise ValueError('driver returned an error')
        payload = _payload(result)
        if tool == 'list_apps':
            rows, fields, key = payload['apps'], APP_FIELDS, 'apps'
        elif tool == 'list_windows':
            rows, fields, key = payload['windows'], WINDOW_FIELDS, 'windows'
        else:
            # There is no output schema for this tool in CUA 0.28.1. Do not
            # expose its global process inventory through a guessed adapter.
            raise ValueError('unsupported accessibility discovery layout')
        if not isinstance(rows, list):
            raise ValueError('discovery records are not an array')
        kept = []
        for row in rows:
            if not isinstance(row, dict) or 'pid' not in row:
                raise ValueError('discovery record has no PID')
            pid = row['pid']
            installed = tool == 'list_apps' and type(pid) is int and pid == 0 and row.get('running') is False
            if installed or guard.allows(pid):
                kept.append({key: value for key, value in row.items() if key in fields})
        filtered = {key: kept}
        # Rebuild both representations: the original human-readable text may
        # still contain every host PID even when structuredContent was filtered.
        return {**reply, 'result': {'structuredContent': filtered,
                                  'content': [{'type': 'text', 'text': json.dumps(filtered, ensure_ascii=False)}]}}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise NativeIsolationError('NATIVE_DISCOVERY_UNCERTAIN: formato de descoberta não validado; '
                                   'nenhum alvo foi exposto. Use list_apps/list_windows com formato suportado.') from exc
