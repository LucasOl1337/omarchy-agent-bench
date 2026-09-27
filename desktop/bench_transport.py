"""Bounded CUA newline JSON transport shared by both MCP entry points."""
import json
import os
import selectors
import time


class CuaChannel:
    """Bounded newline JSON transport, including partial lines and full pipes."""
    MAX_FRAME_BYTES = 32 * 1024 * 1024

    def __init__(self, proc):
        self.proc = proc
        self.buffer = bytearray()
        os.set_blocking(proc.stdout.fileno(), False)
        os.set_blocking(proc.stdin.fileno(), False)

    @staticmethod
    def _wait(fd, event, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('prazo da resposta CUA excedido')
        with selectors.DefaultSelector() as selector:
            selector.register(fd, event)
            if not selector.select(remaining):
                raise TimeoutError('prazo da resposta CUA excedido')

    def send(self, message, deadline):
        data = memoryview((json.dumps(message) + '\n').encode())
        if len(data) > self.MAX_FRAME_BYTES:
            raise ValueError('mensagem CUA excede limite do transporte')
        fd = self.proc.stdin.fileno()
        while data:
            self._wait(fd, selectors.EVENT_WRITE, deadline)
            try:
                count = os.write(fd, data)
            except BlockingIOError:
                continue
            if not count:
                raise RuntimeError('pipe CUA fechado durante envio')
            data = data[count:]

    def receive(self, message_id, deadline):
        fd = self.proc.stdout.fileno()
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('prazo da resposta CUA excedido')
            if b'\n' in self.buffer:
                line, _, rest = self.buffer.partition(b'\n')
                self.buffer = bytearray(rest)
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError('resposta CUA não é objeto JSON')
                if message.get('id') == message_id and ('result' in message or 'error' in message):
                    return message
                continue
            self._wait(fd, selectors.EVENT_READ, deadline)
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                raise RuntimeError('cua-driver encerrou sem resposta completa')
            self.buffer.extend(chunk)
            if len(self.buffer) > self.MAX_FRAME_BYTES:
                raise ValueError('resposta CUA excede limite do transporte')
