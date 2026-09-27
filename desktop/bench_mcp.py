"""Sequential, bounded stdio bridge with native isolation and human handoff."""
from contextlib import nullcontext
import json
import signal
import sys
import time

from bench_control import control
from bench_ops import launch_cua, stop_cua
from bench_native import guard_request, recheck_request, filter_response, normalize_request
from bench_transport import CuaChannel

READ_ONLY = frozenset(('list_apps', 'list_windows', 'get_window_state', 'verify_state',
    'clipboard_read', 'get_screen_size', 'get_desktop_state', 'get_cursor_position',
    'get_agent_cursor_state', 'check_permissions', 'health_report', 'get_config',
    'get_accessibility_tree', 'get_browser_state', 'get_recording_state',
    'get_session', 'list_sessions', 'get_session_state'))


class BridgeTransportError(RuntimeError):
    pass


def _exchange(name, channel, message, timeout):
    method = message.get('method')
    if method == 'tools/call' and 'id' not in message:
        raise ValueError('NATIVE_REQUEST_INVALID: tools/call exige id para confirmar conclusão sob o gate.')
    params = message.get('params', {})
    if not isinstance(params, dict):
        raise ValueError('NATIVE_REQUEST_INVALID: params deve ser objeto JSON.')
    if method == 'tools/call' and not isinstance(params.get('name'), str):
        raise ValueError('NATIVE_REQUEST_INVALID: name da ferramenta deve ser string.')
    message = normalize_request(message)
    guard = guard_request(name, message)
    mutation = method == 'tools/call' and params.get('name') not in READ_ONLY
    with control(name) if mutation else nullcontext():
        recheck_request(guard)
        deadline = time.monotonic() + timeout
        try:
            channel.send(message, deadline)
            reply = channel.receive(message['id'], deadline) if 'id' in message else None
        except (OSError, RuntimeError, ValueError) as exc:
            if mutation:
                error = ('CUA_RESULTADO_INCERTO: falha no transporte após tentar enviar a ação; '
                         'ela pode ter sido executada. Observe o estado antes de decidir o próximo '
                         'passo; não repita automaticamente.')
            elif method == 'initialize':
                code = 'CUA_INIT_TIMEOUT' if isinstance(exc, TimeoutError) else 'CUA_INIT_FAILED'
                error = f'{code}: initialize não concluiu; nenhuma ação da tarefa foi enviada.'
            else:
                code = 'CUA_TIMEOUT' if isinstance(exc, TimeoutError) else 'CUA_TRANSPORTE_FALHOU'
                error = f'{code}: troca CUA não concluiu; sem repetição automática.'
            raise BridgeTransportError(error + ' Conexão dedicada encerrada; reconecte explicitamente.') from exc
    # Release input before filtering or writing to a possibly slow MCP client.
    return filter_response(name, message, reply, guard) if reply is not None else None


def _error_reply(message, error):
    if 'id' not in message:
        return None
    if message.get('method') == 'tools/call':
        return {'jsonrpc': '2.0', 'id': message['id'], 'result': {
            'isError': True, 'content': [{'type': 'text', 'text': str(error)}]}}
    return {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32000, 'message': str(error)}}


def run(name, env, init_timeout=15, call_timeout=60):
    if not 0 < init_timeout <= 300 or not 0 < call_timeout <= 300:
        raise ValueError('prazos CUA devem estar entre 0 e 300 segundos')

    def emit(message):
        if message is not None:
            sys.stdout.write(json.dumps(message) + '\n')
            sys.stdout.flush()

    def terminate(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    proc = launch_cua(name, env)
    fatal = None
    failed = False
    try:
        channel = CuaChannel(proc)
        for line in sys.stdin:
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError('pedido MCP não é objeto JSON')
            except ValueError as exc:
                emit({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': str(exc)}})
                continue
            try:
                timeout = init_timeout if message.get('method') == 'initialize' else call_timeout
                reply = _exchange(name, channel, message, timeout)
            except BridgeTransportError as exc:
                failed = True
                fatal = _error_reply(message, exc)
                if fatal is None:
                    print('agent-bench mcp:', exc, file=sys.stderr)
                break
            except (OSError, RuntimeError, ValueError) as exc:
                reply = _error_reply(message, exc)
                if reply is None:
                    print('agent-bench mcp:', exc, file=sys.stderr)
            emit(reply)
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            stop_cua(proc)
        except Exception as exc:
            failed = True
            print('agent-bench mcp cleanup: encerramento da unidade não confirmado:', exc, file=sys.stderr)
    emit(fatal)
    return 1 if failed else 0
